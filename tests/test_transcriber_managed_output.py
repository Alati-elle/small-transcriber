"""Synthetic transcriber boundary tests; no Keychain, ffmpeg or Gemini calls."""

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "src/gemini_transcribe_meeting.py"


def load_transcriber():
    spec = importlib.util.spec_from_file_location("gemini_transcribe_meeting", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ManagedOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source" / "meeting.wav"
        self.source.parent.mkdir()
        self.source.write_bytes(b"synthetic audio")
        self.digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.module = load_transcriber()
        self.output = self.root / "managed" / "output"
        self.cache = self.root / "managed" / "cache"

    def managed_args(self):
        return [str(self.source), "--output-dir", str(self.output),
                "--cache-dir", str(self.cache),
                "--expected-source-sha256", self.digest]

    def run_synthetic(self, args, mutate=None):
        module = self.module
        paths = []

        def split(source, directory, managed=False, expected_source_sha=None):
            paths.append(directory)
            chunk = directory / "meeting_chunk001.m4a"
            chunk.write_bytes(b"synthetic chunk")
            return [(chunk, 0)]

        def quality(*_):
            if mutate:
                mutate()
            return {"ok": True, "severe": [], "warnings": []}

        row = {"start": 0, "end": 1, "global_speaker": "Спикер 1", "text": "Тест"}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", ["transcriber", *args]))
            stack.enter_context(mock.patch.object(module, "FFMPEG", str(self.source)))
            stack.enter_context(mock.patch.object(module, "FFPROBE", str(self.source)))
            stack.enter_context(mock.patch.object(module, "get_key", return_value="fake"))
            stack.enter_context(mock.patch.object(module, "load_vocabulary", return_value=[]))
            stack.enter_context(mock.patch.object(module, "split_audio", side_effect=split))
            stack.enter_context(mock.patch.object(module, "upload_audio", return_value="fake-uri"))
            stack.enter_context(mock.patch.object(module, "transcribe", return_value={"candidates": [{}]}))
            stack.enter_context(mock.patch.object(module, "extract_parts", return_value=[]))
            stack.enter_context(mock.patch.object(module, "merge", return_value=([row], [])))
            stack.enter_context(mock.patch.object(module, "duration", return_value=1))
            stack.enter_context(mock.patch.object(module, "assess_quality", side_effect=quality))
            opened = stack.enter_context(mock.patch.object(module.subprocess, "run"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            module.main()
        return paths, opened

    def test_legacy_paths_and_open(self):
        paths, opened = self.run_synthetic([str(self.source)])
        outdir = self.source.parent / "meeting"
        self.assertEqual(paths, [outdir / "_service"])
        self.assertTrue((outdir / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt").is_file())
        self.assertTrue((outdir / "_service/meeting_chunk001_gemini.json").is_file())
        self.assertTrue((outdir / "_service/merge_diagnostics.json").is_file())
        opened.assert_called_once_with(["/usr/bin/open", str(outdir)])

    def test_managed_paths_diagnostics_and_no_source_adjacent_artifacts(self):
        paths, opened = self.run_synthetic(self.managed_args())
        manifest = json.loads((self.cache / "manifest.json").read_text())
        generation = self.cache / "generations" / manifest["generation"]
        self.assertEqual(paths, [generation])
        self.assertTrue((generation / "meeting_chunk001.m4a").is_file())
        self.assertTrue((generation / "meeting_chunk001_gemini.json").is_file())
        self.assertTrue((generation / "meeting_chunk001_transcript.txt").is_file())
        self.assertTrue((self.output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt").is_file())
        self.assertEqual(json.loads((self.output / "_service/merge_diagnostics.json").read_text())["boundaries"], [])
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])
        opened.assert_not_called()

    def test_incomplete_and_invalid_cli(self):
        for args in (
            [str(self.source), "--output-dir", str(self.output)],
            [str(self.source), "--output-dir", str(self.output), "--cache-dir", str(self.cache)],
            [str(self.source), "--cache-dir", str(self.cache)],
            [str(self.source), "--expected-source-sha256", self.digest],
            [str(self.source), "--unknown"],
            [str(self.source), "--output-dir", str(self.output), "--cache-dir", str(self.cache),
             "--expected-source-sha256", "bad"],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    self.module.parse_args(args)
                self.assertEqual(error.exception.code, 2)
        self.assertFalse(self.output.exists())

    def test_help_describes_managed_mode(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as error:
                self.module.parse_args(["--help"])
        self.assertEqual(error.exception.code, 0)
        for option in ("--output-dir", "--cache-dir", "--expected-source-sha256"):
            self.assertIn(option, output.getvalue())

    def test_pre_run_mismatch_creates_no_artifacts(self):
        args = self.managed_args()
        args[-1] = "0" * 64
        with mock.patch.object(sys, "argv", ["transcriber", *args]):
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                self.module.main()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.cache.exists())

    def test_post_run_mutation_prevents_final_publication(self):
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            self.run_synthetic(self.managed_args(), lambda: self.source.write_bytes(b"changed"))
        self.assertFalse((self.output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt").exists())
        self.assertEqual(list(self.output.glob(".transcript-*")), [])
        self.assertTrue((self.output / "_service/merge_diagnostics.json").is_file())
        self.assertFalse((self.cache / "manifest.json").exists())
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])

    def test_previous_final_survives_post_hash_failure(self):
        self.output.mkdir(parents=True)
        previous = self.output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt"
        previous.write_bytes(b"previous good transcript")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            self.run_synthetic(self.managed_args(), lambda: self.source.write_bytes(b"changed"))
        self.assertEqual(previous.read_bytes(), b"previous good transcript")
        self.assertEqual(list(self.output.glob(".transcript-*")), [])

    def test_successful_managed_run_publishes_final_only_after_post_hash_check(self):
        events = []
        check = self.module.check_source_hash
        replace = self.module.os.replace

        def observed_check(*args):
            events.append("hash")
            return check(*args)

        def observed_replace(*args):
            if args[1] == self.output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt":
                events.append("publish")
            return replace(*args)

        with mock.patch.object(self.module, "check_source_hash", side_effect=observed_check):
            with mock.patch.object(self.module.os, "replace", side_effect=observed_replace):
                self.run_synthetic(self.managed_args())
        self.assertEqual(events, ["hash", "hash", "publish"])

    def test_late_exception_preserves_previous_final(self):
        self.output.mkdir(parents=True)
        previous = self.output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt"
        previous.write_bytes(b"previous good transcript")
        original_log = self.module.log

        def fail_after_hash(message=""):
            if message == "ГОТОВО":
                raise RuntimeError("synthetic late failure")
            return original_log(message)

        with mock.patch.object(self.module, "log", side_effect=fail_after_hash):
            with self.assertRaisesRegex(RuntimeError, "synthetic late failure"):
                self.run_synthetic(self.managed_args())
        self.assertEqual(previous.read_bytes(), b"previous good transcript")
        self.assertEqual(list(self.output.glob(".transcript-*")), [])

    def test_uppercase_sha_is_accepted(self):
        args = self.managed_args()
        args[-1] = self.digest.upper()
        self.run_synthetic(args)
        self.assertTrue((self.output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt").exists())

    def test_nonexistent_and_unreadable_source(self):
        self.source.unlink()
        with mock.patch.object(sys, "argv", ["transcriber", *self.managed_args()]):
            with self.assertRaisesRegex(RuntimeError, "Файл не найден"):
                self.module.main()
        self.source.write_bytes(b"synthetic audio")
        with mock.patch.object(sys, "argv", ["transcriber", *self.managed_args()]):
            with mock.patch.object(Path, "open", side_effect=PermissionError("synthetic unreadable")):
                with self.assertRaisesRegex(PermissionError, "synthetic unreadable"):
                    self.module.main()
        self.assertFalse(self.output.exists())

    def test_existing_and_nested_managed_directories(self):
        layouts = (
            (self.root / "one", self.root / "one"),
            (self.root / "two", self.root / "two/cache"),
            (self.root / "three/output", self.root / "three"),
        )
        for output, cache in layouts:
            with self.subTest(output=output, cache=cache):
                self.output, self.cache = output, cache
                output.mkdir(parents=True)
                cache.mkdir(parents=True, exist_ok=True)
                (output / "_service").mkdir(exist_ok=True)
                self.run_synthetic(self.managed_args())
                self.assertTrue((output / "meeting_ПОЛНАЯ_РАСШИФРОВКА.txt").is_file())

    def test_existing_service_symlink_is_rejected(self):
        self.output.mkdir(parents=True)
        (self.output / "_service").symlink_to(self.source.parent, target_is_directory=True)
        with mock.patch.object(sys, "argv", ["transcriber", *self.managed_args()]):
            with mock.patch.object(self.module, "FFMPEG", str(self.source)):
                with mock.patch.object(self.module, "FFPROBE", str(self.source)):
                    with mock.patch.object(self.module, "get_key", return_value="fake"):
                        with self.assertRaisesRegex(RuntimeError, "symlink"):
                            self.module.main()
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])

    def test_cache_artifact_symlink_cannot_write_beside_source(self):
        self.run_synthetic(self.managed_args())
        manifest = json.loads((self.cache / "manifest.json").read_text())
        generation = self.cache / "generations" / manifest["generation"]
        adjacent = self.source.parent / "outside.json"
        cached_json = generation / "meeting_chunk001_gemini.json"
        cached_json.unlink()
        cached_json.symlink_to(adjacent)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.run_synthetic(self.managed_args())
        self.assertFalse(adjacent.exists())
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])


if __name__ == "__main__":
    unittest.main()
