"""Synthetic managed cache tests; no real audio, Keychain, ffmpeg or Gemini."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
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


class CacheProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source" / "meeting.wav"
        self.source.parent.mkdir()
        self.source.write_bytes(b"synthetic audio")
        self.ffmpeg = self.root / "synthetic-ffmpeg"
        self.ffmpeg.write_bytes(b"synthetic executable identity")
        self.output = self.root / "output"
        self.cache = self.root / "cache"
        self.module = load_transcriber()
        self.total_duration = 1

    def digest(self):
        return hashlib.sha256(self.source.read_bytes()).hexdigest()

    def manifest(self):
        return json.loads((self.cache / "manifest.json").read_text(encoding="utf-8"))

    def generation(self):
        return self.cache / "generations" / self.manifest()["generation"]

    def run_once(self, vocabulary=(), managed=True, expected=None):
        module = self.module
        expected = expected or self.digest()
        args = ["transcriber", str(self.source)]
        if managed:
            args += ["--output-dir", str(self.output), "--cache-dir", str(self.cache),
                     "--expected-source-sha256", expected]

        def fake_ffmpeg(command, **_):
            Path(command[-1]).write_bytes(b"complete synthetic chunk")

        row = {"start": 0, "end": 1, "global_speaker": "Спикер 1", "text": "Синтетика"}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", args))
            stack.enter_context(mock.patch.object(module, "FFMPEG", str(self.ffmpeg)))
            stack.enter_context(mock.patch.object(module, "FFPROBE", str(self.ffmpeg)))
            stack.enter_context(mock.patch.object(module, "get_key", return_value="fake"))
            stack.enter_context(mock.patch.object(module, "load_vocabulary", return_value=list(vocabulary)))
            stack.enter_context(mock.patch.object(module, "duration", return_value=self.total_duration))
            ffmpeg = stack.enter_context(mock.patch.object(module, "run", side_effect=fake_ffmpeg))
            upload = stack.enter_context(mock.patch.object(module, "upload_audio", return_value="fake-uri"))
            gemini = stack.enter_context(mock.patch.object(
                module, "transcribe", return_value={"candidates": [{}]}
            ))
            stack.enter_context(mock.patch.object(module, "extract_parts", return_value=[]))
            stack.enter_context(mock.patch.object(module, "merge", return_value=([row], [])))
            stack.enter_context(mock.patch.object(
                module, "assess_quality", return_value={"ok": True, "severe": [], "warnings": []}
            ))
            stack.enter_context(mock.patch.object(module.subprocess, "run"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            module.main()
            return ffmpeg.call_count, upload.call_count, gemini.call_count

    def test_first_run_creates_versioned_manifest(self):
        self.assertEqual(self.run_once(), (1, 1, 1))
        manifest = self.manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["cache_format_version"], 1)
        self.assertEqual(manifest["source_sha256"], self.digest())
        self.assertEqual(manifest["source_size_bytes"], self.source.stat().st_size)
        self.assertEqual(manifest["backend"], "gemini-generateContent")
        self.assertEqual(manifest["model"], self.module.MODEL)
        self.assertEqual(manifest["request_api"], self.module.API)
        self.assertEqual(manifest["upload_api"], self.module.UPLOAD_API)
        self.assertEqual(manifest["chunk_seconds"], self.module.CHUNK)
        self.assertEqual(manifest["overlap_seconds"], self.module.OVERLAP)
        self.assertEqual(manifest["step_seconds"], self.module.STEP)
        self.assertEqual(len(manifest["audio_preprocessing"]["ffmpeg_sha256"]), 64)
        self.assertEqual(len(manifest["audio_preprocessing"]["ffprobe_sha256"]), 64)
        self.assertEqual(len(manifest["request_sha256"]), 64)
        self.assertEqual(len(manifest["vocabulary_sha256"]), 64)
        self.assertTrue(self.generation().is_dir())
        self.assertEqual(list(self.source.parent.iterdir()), [self.source])

    def test_exact_match_reuses_chunk_and_response(self):
        self.run_once()
        generation = self.generation()
        self.assertEqual(self.run_once(), (0, 0, 0))
        self.assertEqual(self.generation(), generation)

    def test_missing_manifest_ignores_preexisting_cache(self):
        self.run_once()
        old = self.generation()
        (self.cache / "manifest.json").unlink()
        self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)
        self.assertTrue(old.is_dir())

    def test_pre_manifest_root_level_cache_is_ignored(self):
        self.cache.mkdir()
        (self.cache / "meeting_chunk360_ov30_001.m4a").write_bytes(b"old chunk")
        (self.cache / "meeting_chunk360_ov30_001_gemini.json").write_text(
            '{"candidates":[{}]}', encoding="utf-8"
        )
        self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertTrue((self.cache / "meeting_chunk360_ov30_001_gemini.json").exists())
        self.assertTrue(next(self.generation().glob("*_gemini.json")).exists())

    def test_malformed_and_unsupported_manifest_rebuild(self):
        self.run_once()
        for replacement in ("{bad", json.dumps({**self.manifest(), "schema_version": 999})):
            old = self.generation()
            (self.cache / "manifest.json").write_text(replacement, encoding="utf-8")
            self.assertEqual(self.run_once(), (1, 1, 1))
            self.assertNotEqual(self.generation(), old)

    def test_wrong_source_hash_and_changed_source_rebuild(self):
        self.run_once()
        old = self.generation()
        manifest = self.manifest()
        manifest["source_sha256"] = "0" * 64
        (self.cache / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)
        old = self.generation()
        self.source.write_bytes(b"different synthetic audio")
        self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)

    def test_model_chunk_and_vocabulary_changes_rebuild(self):
        self.run_once(vocabulary=["alpha"])
        old = self.generation()
        with mock.patch.object(self.module, "MODEL", "synthetic-new-model"):
            self.assertEqual(self.run_once(vocabulary=["alpha"]), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)
        old = self.generation()
        with mock.patch.object(self.module, "CHUNK", 120):
            with mock.patch.object(self.module, "STEP", 90):
                self.assertEqual(self.run_once(vocabulary=["alpha"]), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)
        old = self.generation()
        self.assertEqual(self.run_once(vocabulary=["beta"]), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)

    def test_ffmpeg_and_request_config_changes_rebuild(self):
        self.run_once()
        old = self.generation()
        self.ffmpeg.write_bytes(b"different executable identity")
        self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)
        old = self.generation()
        payload = self.module.transcription_payload

        def changed_payload(uri, vocabulary):
            result = payload(uri, vocabulary)
            result["generationConfig"]["audioTranscriptionConfig"]["mode"] = "SYNTHETIC"
            return result

        with mock.patch.object(self.module, "transcription_payload", side_effect=changed_payload):
            self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertNotEqual(self.generation(), old)

    def test_partial_cache_reuses_valid_entries(self):
        self.total_duration = 400
        self.assertEqual(self.run_once(), (2, 2, 2))
        files = sorted(self.generation().glob("*_gemini.json"))
        self.assertEqual(len(files), 2)
        files[1].unlink()
        self.assertEqual(self.run_once(), (0, 1, 1))

    def test_truncated_cached_json_is_regenerated(self):
        self.run_once()
        cached = next(self.generation().glob("*_gemini.json"))
        cached.write_text('{"candidates":', encoding="utf-8")
        self.assertEqual(self.run_once(), (0, 1, 1))
        self.assertEqual(json.loads(cached.read_text())["candidates"], [{}])

    def test_source_mutation_during_chunk_creation_does_not_publish_chunk(self):
        generation = self.root / "generation"
        generation.mkdir()
        expected = self.digest()

        def mutate_after_write(command, **_):
            Path(command[-1]).write_bytes(b"untrusted synthetic chunk")
            self.source.write_bytes(b"changed during ffmpeg")

        with mock.patch.object(self.module, "duration", return_value=1):
            with mock.patch.object(self.module, "run", side_effect=mutate_after_write):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                        self.module.split_audio(
                            self.source, generation, managed=True,
                            expected_source_sha=expected,
                        )
        self.assertEqual(list(generation.iterdir()), [])

    def test_manifest_and_cached_json_are_written_atomically(self):
        replace = os.replace
        published = []

        def observed_replace(source, target):
            if Path(target).name in ("manifest.json", "meeting_chunk360_ov30_001_gemini.json"):
                self.assertTrue(Path(source).is_file())
                json.loads(Path(source).read_text(encoding="utf-8"))
                published.append(Path(target).name)
            return replace(source, target)

        with mock.patch.object(self.module.os, "replace", side_effect=observed_replace):
            self.run_once()
        self.assertIn("manifest.json", published)
        self.assertIn("meeting_chunk360_ov30_001_gemini.json", published)
        self.assertEqual(list(self.cache.glob(".cache-*")), [])

    def test_manifest_write_failure_keeps_previous_manifest(self):
        self.run_once()
        previous = (self.cache / "manifest.json").read_bytes()
        replace = os.replace

        def fail_manifest(source, target):
            if Path(target).name == "manifest.json":
                raise OSError("synthetic disk full")
            return replace(source, target)

        with mock.patch.object(self.module.os, "replace", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "synthetic disk full"):
                self.run_once(vocabulary=["changed"])
        self.assertEqual((self.cache / "manifest.json").read_bytes(), previous)
        self.assertEqual(list(self.cache.glob(".cache-*")), [])

    def test_orphan_temp_from_interrupted_manifest_write_is_ignored(self):
        self.cache.mkdir()
        (self.cache / ".cache-interrupted").write_text('{"candidates":[{}]}')
        self.assertEqual(self.run_once(), (1, 1, 1))
        self.assertTrue((self.cache / ".cache-interrupted").exists())
        self.assertTrue((self.cache / "manifest.json").is_file())

    def test_uppercase_sha_matches_lowercase_manifest(self):
        self.run_once()
        old = self.generation()
        self.assertEqual(self.run_once(expected=self.digest().upper()), (0, 0, 0))
        self.assertEqual(self.generation(), old)

    def test_legacy_reuses_cache_without_manifest(self):
        self.assertEqual(self.run_once(managed=False), (1, 1, 1))
        self.source.write_bytes(b"different synthetic audio")
        self.assertEqual(self.run_once(managed=False), (0, 0, 0))
        self.assertFalse((self.source.parent / "meeting/_service/manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
