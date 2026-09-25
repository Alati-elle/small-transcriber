"""Synthetic protocol output checks; never read Keychain or call Gemini."""

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "src/gemini_make_protocol.py"


def load_protocol():
    spec = importlib.util.spec_from_file_location("gemini_make_protocol", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProtocolManagedOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source_dir = self.root / "source"
        self.source_dir.mkdir()
        self.transcript = self.source_dir / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt"
        self.transcript.write_text(
            "[00:00–00:02] Спикер 0:\nСинтетическая реплика.\n", encoding="utf-8"
        )
        self.original = self.transcript.read_bytes()
        self.output = self.root / "managed" / "output"
        self.protocol = load_protocol()

    def run_synthetic(self, managed=True, html="<html>synthetic protocol</html>", fail=None):
        args = ["protocol", str(self.transcript)]
        if managed:
            args += ["--output-dir", str(self.output)]
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", args))
            key = stack.enter_context(mock.patch.object(self.protocol, "get_key", return_value="fake"))
            stack.enter_context(mock.patch.object(
                self.protocol, "consolidate_speakers", side_effect=lambda rows, _: (rows, [], None)
            ))
            stack.enter_context(mock.patch.object(self.protocol, "detect_speakers", return_value=([], None)))
            stack.enter_context(mock.patch.object(
                self.protocol, "call_gemini",
                side_effect=RuntimeError("synthetic failure") if fail else None,
                return_value=([], "synthetic-model"),
            ))
            stack.enter_context(mock.patch.object(self.protocol, "make_html", return_value=html))
            opened = stack.enter_context(mock.patch.object(self.protocol.subprocess, "run"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.protocol.main()
        return key, opened

    def test_legacy_paths_and_layout_unchanged(self):
        self.run_synthetic(managed=False)
        json_path, html_path = self.protocol.output_paths(self.transcript)
        self.assertEqual(json_path.parent, self.source_dir)
        self.assertEqual(html_path.parent, self.source_dir)
        self.assertTrue(json_path.is_file())
        self.assertTrue(html_path.is_file())
        self.assertTrue((self.source_dir / "_service/speaker_normalization.json").is_file())
        self.assertFalse(self.output.exists())

    def test_managed_paths_service_naming_and_read_only_transcript(self):
        key, opened = self.run_synthetic()
        json_path = self.output / "meeting_ПРОТОКОЛ.json"
        html_path = self.output / "meeting_ПРОТОКОЛ.html"
        self.assertTrue(json_path.is_file())
        self.assertTrue(html_path.is_file())
        self.assertTrue((self.output / "_service/speaker_normalization.json").is_file())
        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["source_transcript"], str(self.transcript))
        self.assertEqual(self.transcript.read_bytes(), self.original)
        self.assertEqual(list(self.source_dir.iterdir()), [self.transcript])
        key.assert_called_once()
        opened.assert_not_called()

    def test_existing_empty_directory_is_allowed(self):
        self.output.mkdir(parents=True)
        self.run_synthetic()
        self.assertTrue((self.output / "meeting_ПРОТОКОЛ.json").is_file())

    def test_previous_pair_and_partial_output_are_rejected_unchanged(self):
        layouts = (("meeting_ПРОТОКОЛ.json", "meeting_ПРОТОКОЛ.html"),
                   ("meeting_ПРОТОКОЛ.json",), ("meeting_ПРОТОКОЛ.html",),
                   ("_service/old.json",))
        for index, names in enumerate(layouts):
            with self.subTest(names=names):
                self.output = self.root / f"previous-{index}"
                self.output.mkdir()
                for name in names:
                    path = self.output / name
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(b"previous good artifact")
                with self.assertRaisesRegex(RuntimeError, "пустым staging"):
                    self.run_synthetic()
                for name in names:
                    self.assertEqual((self.output / name).read_bytes(), b"previous good artifact")
                self.assertEqual(self.transcript.read_bytes(), self.original)

    def test_analysis_failure_leaves_no_protocol_pair(self):
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            self.run_synthetic(fail=True)
        self.assertFalse((self.output / "meeting_ПРОТОКОЛ.json").exists())
        self.assertFalse((self.output / "meeting_ПРОТОКОЛ.html").exists())

    def test_invalid_json_structure_prevents_publication(self):
        self.output.mkdir(parents=True)
        json_path, html_path = self.protocol.output_paths(self.transcript, self.output)
        with self.assertRaisesRegex(RuntimeError, "проверку JSON/HTML"):
            self.protocol.publish_protocol(
                json_path, html_path, {"synthetic": True}, "<html>synthetic</html>",
                managed=True, transcript_path=self.transcript,
            )
        self.assertFalse(json_path.exists())
        self.assertFalse(html_path.exists())

    def test_malformed_json_and_missing_or_empty_html_fail_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            json_path, html_path = root / "protocol.json", root / "protocol.html"
            json_path.write_text("{bad", encoding="utf-8")
            html_path.write_text("<html>synthetic</html>", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                self.protocol.validate_managed_protocol(json_path, html_path, self.transcript)
            json_path.write_text(json.dumps({"source_transcript": str(self.transcript)}))
            html_path.unlink()
            with self.assertRaises(RuntimeError):
                self.protocol.validate_managed_protocol(json_path, html_path, self.transcript)
            html_path.write_text("   ")
            with self.assertRaises(RuntimeError):
                self.protocol.validate_managed_protocol(json_path, html_path, self.transcript)

    def test_empty_html_prevents_publication(self):
        with self.assertRaises(RuntimeError):
            self.run_synthetic(html="   ")
        self.assertFalse((self.output / "meeting_ПРОТОКОЛ.json").exists())
        self.assertFalse((self.output / "meeting_ПРОТОКОЛ.html").exists())

    def test_second_publish_failure_does_not_leave_partial_pair(self):
        real_replace = self.protocol.os.replace

        def fail_html(source, target):
            if Path(target).suffix == ".html":
                raise OSError("synthetic publish failure")
            return real_replace(source, target)

        with mock.patch.object(self.protocol.os, "replace", side_effect=fail_html):
            with self.assertRaisesRegex(OSError, "synthetic publish failure"):
                self.run_synthetic()
        self.assertFalse((self.output / "meeting_ПРОТОКОЛ.json").exists())
        self.assertFalse((self.output / "meeting_ПРОТОКОЛ.html").exists())

    def test_same_directory_is_rejected(self):
        self.output = self.source_dir
        with self.assertRaisesRegex(RuntimeError, "отличаться"):
            self.run_synthetic()
        self.assertEqual(list(self.source_dir.iterdir()), [self.transcript])

    def test_unknown_argument_and_help(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                self.protocol.parse_args([str(self.transcript), "--unknown"])
        self.assertEqual(error.exception.code, 2)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as help_exit:
                self.protocol.parse_args(["--help"])
        self.assertEqual(help_exit.exception.code, 0)
        self.assertIn("--output-dir", output.getvalue())


if __name__ == "__main__":
    unittest.main()
