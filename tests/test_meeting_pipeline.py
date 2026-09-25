"""Synthetic end-to-end checks; no Gemini, installer, or user storage."""

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import meeting_pipeline as pipeline  # noqa: E402
from meeting_store import MeetingStore, file_sha256  # noqa: E402


TRANSCRIBER = '''import pathlib, sys
source = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])
if 'FAIL' in source.name: sys.exit(7)
if 'MISSING' not in source.name:
    (out / (source.stem + '_ПОЛНАЯ_РАСШИФРОВКА.txt')).write_text('SYNTHETIC TRANSCRIPT', encoding='utf-8')
'''
PROTOCOL = '''import json, pathlib, sys
source = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])
if 'FAIL' in source.name: sys.exit(8)
base = source.name.replace('_ПОЛНАЯ_РАСШИФРОВКА.txt', '_ПРОТОКОЛ')
if 'MISSING' not in source.name:
    data = dict(source_transcript=str(source), transcript=[], items=[], summary={}, categories=[], models_used=[], rows_count=0, items_count=0)
    (out / (base + '.json')).write_text(json.dumps(data), encoding='utf-8')
    (out / (base + '.html')).write_text('<html>SYNTHETIC</html>', encoding='utf-8')
'''


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "storage"
        self.transcriber = self.base / "fake_transcriber.py"
        self.protocol = self.base / "fake_protocol.py"
        self.transcriber.write_text(TRANSCRIBER, encoding="utf-8")
        self.protocol.write_text(PROTOCOL, encoding="utf-8")

    def invoke(self, name="sample.m4a", protocol_script=None, transcriber_script=None):
        audio = self.base / name
        audio.write_bytes(b"synthetic audio")
        cmd = [sys.executable, str(Path(pipeline.__file__)), "run", str(audio),
               "--storage-root", str(self.root), "--transcriber-script",
               str(transcriber_script or self.transcriber), "--protocol-script",
               str(protocol_script or self.protocol)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        return audio, result, events

    def test_success_lifecycle_events_paths_and_permissions(self):
        audio, result, events = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["event"] for e in events], ["pipeline_started", "transcription_started",
                         "transcription_succeeded", "analysis_started", "analysis_succeeded", "pipeline_succeeded"])
        self.assertEqual(events[0]["meeting_dir"], events[-1]["meeting_dir"])
        self.assertEqual(events[1]["current_log_path"], events[-1]["transcribe_log_path"])
        self.assertEqual(events[3]["current_log_path"], events[-1]["protocol_log_path"])
        self.assertNotIn("SYNTHETIC TRANSCRIPT", result.stdout)
        final = events[-1]
        meeting_id = final["meeting_id"]
        store = MeetingStore(self.root)
        meeting = store.get_meeting(meeting_id)
        self.assertEqual(meeting["source_original_path"], str(audio.resolve()))
        self.assertEqual(meeting["source_size_bytes"], audio.stat().st_size)
        self.assertEqual(meeting["source_sha256"], file_sha256(audio))
        trans = store.list_transcription_runs(meeting_id)[0]
        analysis = store.list_analysis_runs(meeting_id)[0]
        self.assertEqual((trans["status"], analysis["status"]), ("succeeded", "succeeded"))
        self.assertEqual(analysis["transcript_sha256"], trans["transcript_sha256"])
        self.assertEqual(file_sha256(Path(final["transcript_path"])), trans["transcript_sha256"])
        self.assertEqual(analysis["id"], store.get_active_analysis(meeting_id)["id"])
        for key, relative in (("transcript_path", trans["transcript_path"]),
                              ("active_json_path", analysis["output_json_path"]),
                              ("active_html_path", analysis["output_html_path"])):
            self.assertFalse(Path(relative).is_absolute())
            self.assertEqual(Path(final[key]), store.resolve_managed_path(relative))
            self.assertTrue(Path(final[key]).is_file())
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(Path(final["transcribe_log_path"]).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(final["active_json_path"]).stat().st_mode), 0o600)
        self.assertTrue(Path(final["meeting_dir"]).is_dir())

    def test_source_failure_does_not_initialize_storage(self):
        missing = self.base / "absent.m4a"
        result = subprocess.run([sys.executable, str(Path(pipeline.__file__)), "run", str(missing),
                                 "--storage-root", str(self.root)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["event"], "pipeline_failed")
        self.assertFalse(self.root.exists())

    def test_usage_failure_is_json_lines(self):
        result = subprocess.run([sys.executable, str(Path(pipeline.__file__)), "run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["event"], "pipeline_failed")
        self.assertFalse(self.root.exists())

    def test_transcriber_failure_and_missing_output(self):
        for name, expected in (("FAIL.m4a", "failed"), ("MISSING.m4a", "incomplete")):
            with self.subTest(name=name):
                _, result, events = self.invoke(name)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events[-1]["status"], expected)
                self.assertEqual(events[-1]["current_log_path"], events[1]["current_log_path"])
                store = MeetingStore(self.root)
                meeting_id = events[-1]["meeting_id"]
                self.assertEqual(store.list_transcription_runs(meeting_id)[0]["status"], expected)
                self.assertEqual(store.list_analysis_runs(meeting_id), [])
                self.assertIsNone(store.get_active_analysis(meeting_id))

    def test_source_change_after_child_blocks_publication(self):
        script = self.base / "mutating_transcriber.py"
        script.write_text(TRANSCRIBER + "\nsource.write_bytes(b'changed source')\n", encoding="utf-8")
        _, result, events = self.invoke(transcriber_script=script)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events[-1]["status"], "incomplete")
        meeting_id = events[-1]["meeting_id"]
        store = MeetingStore(self.root)
        self.assertEqual(store.list_transcription_runs(meeting_id)[0]["status"], "incomplete")
        self.assertEqual(store.list_analysis_runs(meeting_id), [])
        self.assertFalse((self.root / "meetings" / meeting_id / "transcriptions" /
                          events[-1]["transcription_run_id"] / "published").exists())

    def test_protocol_failure_and_missing_pair(self):
        for code, expected in (("import sys; sys.exit(8)", "failed"),
                               ("pass", "incomplete")):
            with self.subTest(code=code):
                script = self.base / "alternate_protocol.py"
                script.write_text(code, encoding="utf-8")
                _, result, events = self.invoke(protocol_script=script)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events[-1]["status"], expected)
                self.assertEqual(events[-1]["current_log_path"], events[3]["current_log_path"])
                store = MeetingStore(self.root)
                meeting_id = events[-1]["meeting_id"]
                self.assertEqual(store.list_transcription_runs(meeting_id)[0]["status"], "succeeded")
                self.assertEqual(store.list_analysis_runs(meeting_id)[0]["status"], expected)
                self.assertIsNone(store.get_active_analysis(meeting_id))

    def test_protocol_missing_one_output(self):
        for extension in (".json", ".html"):
            with self.subTest(extension=extension):
                script = self.base / "partial_protocol.py"
                lines = [line for line in PROTOCOL.splitlines()
                         if not ("write_text" in line and "base + '" + extension + "'" in line)]
                script.write_text("\n".join(lines) + "\n", encoding="utf-8")
                _, result, events = self.invoke(protocol_script=script)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events[-1]["status"], "incomplete")
                store = MeetingStore(self.root)
                meeting_id = events[-1]["meeting_id"]
                self.assertEqual(store.list_analysis_runs(meeting_id)[0]["status"], "incomplete")

    def test_protocol_cannot_silently_change_transcript(self):
        script = self.base / "mutating_protocol.py"
        script.write_text(PROTOCOL + "\nsource.write_text('changed transcript', encoding='utf-8')\n",
                          encoding="utf-8")
        _, result, events = self.invoke(protocol_script=script)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events[-1]["status"], "incomplete")
        store = MeetingStore(self.root)
        meeting_id = events[-1]["meeting_id"]
        self.assertEqual(store.list_analysis_runs(meeting_id)[0]["status"], "incomplete")
        self.assertIsNone(store.get_active_analysis(meeting_id))

    def test_publication_rejects_existing_directory(self):
        staging = self.base / ".staging"
        published = self.base / "published"
        staging.mkdir()
        published.mkdir()
        (staging / "output.txt").write_text("synthetic", encoding="utf-8")
        with self.assertRaises(ValueError):
            pipeline.publish_directory(staging, published, [staging / "output.txt"])
        self.assertTrue((staging / "output.txt").exists())

    def test_publication_failure_marks_run_incomplete(self):
        audio = self.base / "sample.m4a"
        audio.write_bytes(b"synthetic audio")
        with patch.object(pipeline, "publish_directory", side_effect=OSError("synthetic")):
            self.assertEqual(pipeline.execute(audio, self.root, self.transcriber, self.protocol), 1)
        store = MeetingStore(self.root)
        meeting_id = next((self.root / "meetings").iterdir()).name
        self.assertEqual(store.list_transcription_runs(meeting_id)[0]["status"], "incomplete")
        self.assertEqual(store.list_analysis_runs(meeting_id), [])

    def test_analysis_publication_failure_preserves_transcription(self):
        audio = self.base / "sample.m4a"
        audio.write_bytes(b"synthetic audio")
        original = pipeline.publish_directory
        count = 0

        def fail_second(*args):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic")
            return original(*args)

        with patch.object(pipeline, "publish_directory", side_effect=fail_second):
            self.assertEqual(pipeline.execute(audio, self.root, self.transcriber, self.protocol), 1)
        store = MeetingStore(self.root)
        meeting_id = next((self.root / "meetings").iterdir()).name
        self.assertEqual(store.list_transcription_runs(meeting_id)[0]["status"], "succeeded")
        self.assertEqual(store.list_analysis_runs(meeting_id)[0]["status"], "incomplete")
        self.assertIsNone(store.get_active_analysis(meeting_id))

    def test_active_switch_failure_leaves_unselected_success(self):
        audio = self.base / "sample.m4a"
        audio.write_bytes(b"synthetic audio")
        with patch.object(MeetingStore, "set_active_analysis", side_effect=ValueError("synthetic")):
            self.assertEqual(pipeline.execute(audio, self.root, self.transcriber, self.protocol), 1)
        store = MeetingStore(self.root)
        meeting_id = next((self.root / "meetings").iterdir()).name
        self.assertEqual(store.list_analysis_runs(meeting_id)[0]["status"], "succeeded")
        self.assertIsNone(store.get_active_analysis(meeting_id))

    def test_two_runs_are_isolated(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(self.invoke, "one.m4a")
            second_future = pool.submit(self.invoke, "two.m4a")
            _, first_result, first_events = first_future.result()
            _, second_result, second_events = second_future.result()
        self.assertEqual(first_result.returncode, 0, first_result.stderr)
        self.assertEqual(second_result.returncode, 0, second_result.stderr)
        first = first_events[-1]
        second = second_events[-1]
        self.assertNotEqual(first["meeting_id"], second["meeting_id"])
        self.assertNotEqual(first["transcript_path"], second["transcript_path"])
        self.assertNotEqual(first["active_json_path"], second["active_json_path"])
        self.assertTrue(Path(first["transcript_path"]).is_file())
        self.assertTrue(Path(second["transcript_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
