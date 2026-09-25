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

    def retry(self, meeting_id, protocol_script=None, transcription_run_id=None):
        cmd = [sys.executable, str(Path(pipeline.__file__)), "retry-analysis",
               "--meeting-id", meeting_id, "--storage-root", str(self.root),
               "--protocol-script", str(protocol_script or self.protocol)]
        if transcription_run_id:
            cmd += ["--transcription-run-id", transcription_run_id]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result, [json.loads(line) for line in result.stdout.splitlines()]

    def test_success_lifecycle_events_paths_and_permissions(self):
        audio, result, events = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["event"] for e in events if e["event"] in (
            "pipeline_started", "transcription_started", "transcription_succeeded",
            "analysis_started", "analysis_succeeded", "pipeline_succeeded")],
            ["pipeline_started", "transcription_started", "transcription_succeeded",
             "analysis_started", "analysis_succeeded", "pipeline_succeeded"])
        self.assertEqual(events[1]["event"], "usage_updated")
        self.assertEqual(events[1]["total"], 0)
        self.assertTrue(all(e.get("operation") == "full" for e in (events[0], events[-1])))
        by_event = {e["event"]: e for e in events}
        self.assertEqual(by_event["transcription_succeeded"]["transcript_path"], events[-1]["transcript_path"])
        self.assertEqual(events[0]["meeting_dir"], events[-1]["meeting_dir"])
        self.assertEqual(by_event["transcription_started"]["current_log_path"], events[-1]["transcribe_log_path"])
        self.assertEqual(by_event["analysis_started"]["current_log_path"], events[-1]["protocol_log_path"])
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

    def test_child_progress_is_forwarded_without_raw_log(self):
        script = self.base / "progress_transcriber.py"
        script.write_text(TRANSCRIBER + "\n"
            "print('@@small-transcriber-event:{\"event\":\"stage_progress\",\"stage\":\"transcription\","
            "\"status\":\"progress\",\"message_code\":\"transcription_chunk\","
            "\"chunk\":1,\"chunks\":1,\"safe_message\":\"PRIVATE TRANSCRIPT BODY\"}', flush=True)\n"
            "print('PRIVATE TRANSCRIPT BODY', flush=True)\n", encoding="utf-8")
        _, result, events = self.invoke(transcriber_script=script)
        self.assertEqual(result.returncode, 0, result.stderr)
        progress = next(e for e in events if e["event"] == "stage_progress")
        self.assertEqual(progress["safe_message"], "Фрагмент 1 из 1")
        self.assertEqual(progress["operation"], "full")
        self.assertNotIn("PRIVATE TRANSCRIPT BODY", result.stdout)
        self.assertIn("PRIVATE TRANSCRIPT BODY", Path(events[-1]["transcribe_log_path"]).read_text())

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
                self.assertEqual(events[-1]["current_log_path"], next(e for e in events if e["event"] == "transcription_started")["current_log_path"])
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
                self.assertEqual(events[-1]["current_log_path"], next(e for e in events if e["event"] == "analysis_started")["current_log_path"])
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

    def test_retry_preserves_failed_analysis_and_transcript_without_audio(self):
        failing = self.base / "failing_protocol.py"
        failing.write_text("import sys; sys.exit(8)\n", encoding="utf-8")
        audio, first, events = self.invoke(protocol_script=failing)
        self.assertNotEqual(first.returncode, 0)
        meeting_id = events[-1]["meeting_id"]
        store = MeetingStore(self.root)
        old = store.list_analysis_runs(meeting_id)[0]
        trans = store.list_transcription_runs(meeting_id)[0]
        transcript = store.resolve_managed_path(trans["transcript_path"])
        original = transcript.read_bytes()
        audio.unlink()
        result, retry_events = self.retry(meeting_id)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["event"] for e in retry_events if e["event"] in (
            "pipeline_started", "analysis_started", "analysis_succeeded", "pipeline_succeeded")],
            ["pipeline_started", "analysis_started", "analysis_succeeded", "pipeline_succeeded"])
        self.assertTrue(all(e["operation"] == "analysis_retry" for e in retry_events if "operation" in e))
        self.assertFalse(any(e["event"] == "transcription_started" for e in retry_events))
        self.assertEqual(retry_events[0]["transcription_run_id"], trans["id"])
        self.assertEqual(transcript.read_bytes(), original)
        self.assertEqual(store.list_transcription_runs(meeting_id), [trans])
        analyses = store.list_analysis_runs(meeting_id)
        self.assertEqual(len(analyses), 2)
        self.assertEqual(next(a for a in analyses if a["id"] == old["id"]), old)
        new = next(a for a in analyses if a["id"] != old["id"])
        self.assertEqual(new["status"], "succeeded")
        self.assertEqual(store.get_active_analysis(meeting_id)["id"], new["id"])
        self.assertTrue(Path(retry_events[-1]["active_html_path"]).is_file())

    def test_failed_retry_keeps_previous_active_and_can_retry_again(self):
        audio, first, events = self.invoke()
        self.assertEqual(first.returncode, 0)
        meeting_id = events[-1]["meeting_id"]
        store = MeetingStore(self.root)
        active_id = store.get_active_analysis(meeting_id)["id"]
        audio.unlink()
        failing = self.base / "failing_protocol.py"
        failing.write_text("import sys; sys.exit(8)\n", encoding="utf-8")
        failed, failure_events = self.retry(meeting_id, failing)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failure_events[-1]["operation"], "analysis_retry")
        self.assertEqual(failure_events[-1]["status"], "failed")
        self.assertEqual(store.get_active_analysis(meeting_id)["id"], active_id)
        second, retry_events = self.retry(meeting_id)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(store.list_transcription_runs(meeting_id)), 1)
        self.assertEqual(sorted(a["status"] for a in store.list_analysis_runs(meeting_id)),
                         ["failed", "succeeded", "succeeded"])
        self.assertEqual(store.get_active_analysis(meeting_id)["id"], retry_events[-1]["analysis_run_id"])

    def test_retry_rejects_missing_and_changed_transcript_before_new_run(self):
        for change in ("missing", "changed"):
            with self.subTest(change=change):
                audio, first, events = self.invoke(name=change + ".m4a")
                self.assertEqual(first.returncode, 0)
                meeting_id = events[-1]["meeting_id"]
                store = MeetingStore(self.root)
                trans = store.list_transcription_runs(meeting_id)[0]
                transcript = store.resolve_managed_path(trans["transcript_path"])
                if change == "missing": transcript.unlink()
                else: transcript.write_text("tampered", encoding="utf-8")
                audio.unlink()
                failed, failure_events = self.retry(meeting_id)
                self.assertNotEqual(failed.returncode, 0)
                self.assertEqual(failure_events[-1]["operation"], "analysis_retry")
                self.assertEqual(len(store.list_analysis_runs(meeting_id)), 1)

    def test_retry_requires_explicit_run_when_multiple_succeeded(self):
        _, first, events = self.invoke()
        self.assertEqual(first.returncode, 0)
        meeting_id = events[-1]["meeting_id"]
        store = MeetingStore(self.root)
        first_run = store.list_transcription_runs(meeting_id)[0]
        second_id = store.create_transcription_run(meeting_id)
        store.mark_transcription_succeeded(second_id, first_run["transcript_path"],
                                           first_run["transcript_sha256"])
        before = len(store.list_analysis_runs(meeting_id))
        failed, failure_events = self.retry(meeting_id)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failure_events[-1]["phase"], "storage")
        self.assertEqual(len(store.list_analysis_runs(meeting_id)), before)
        selected, selected_events = self.retry(meeting_id, transcription_run_id=first_run["id"])
        self.assertEqual(selected.returncode, 0, selected.stderr)
        self.assertEqual(selected_events[-1]["transcription_run_id"], first_run["id"])

    def test_protocol_exit_75_is_structured_without_log_text_in_events(self):
        overloaded = self.base / "overloaded_protocol.py"
        overloaded.write_text("import sys; print('raw Gemini body', file=sys.stderr); sys.exit(75)\n",
                              encoding="utf-8")
        _, first, events = self.invoke(protocol_script=overloaded)
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(events[-1]["error_code"], "gemini_overloaded")
        self.assertNotIn("raw Gemini body", first.stdout)
        second, retry_events = self.retry(events[-1]["meeting_id"], overloaded)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(retry_events[-1]["error_code"], "gemini_overloaded")

    def test_retry_missing_storage_does_not_create_root(self):
        result, events = self.retry("missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events[-1]["operation"], "analysis_retry")
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
