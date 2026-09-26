import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meeting_config import defaults, load, reset_limits, save, stage_models
from meeting_store import MeetingStore, file_sha256
import gemini_transcribe_meeting as transcriber
import gemini_make_protocol as protocol


class ConfigShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "storage"

    def test_defaults_validation_save_permissions_and_malformed_fallback(self):
        config, warning = load(self.root)
        self.assertIsNone(warning)
        self.assertEqual(stage_models(config, "transcription"), ["gemini-3.5-transcribe"])
        self.assertEqual(stage_models(config, "protocol_generation"),
                         ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash"])
        self.assertFalse(self.root.exists())
        config["gemini"]["models"]["gemini-3.6-flash"]["rpd"] = 20
        config["gemini"]["stages"]["name_detection"]["primary_model"] = "gemini-3.8-flash"
        config["gemini"]["stages"]["name_detection"]["fallback_models"] = ["gemini-3.6-flash"]
        config["future_field"] = {"preserved": True}
        save(self.root, config)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "config.json").stat().st_mode), 0o600)
        loaded, warning = load(self.root)
        self.assertIsNone(warning)
        self.assertEqual(loaded, config)
        reset = reset_limits(loaded)
        self.assertIsNone(reset["gemini"]["models"]["gemini-3.6-flash"]["rpd"])
        self.assertEqual(reset["gemini"]["stages"], config["gemini"]["stages"])
        (self.root / "config.json").write_text("{bad", encoding="utf-8")
        fallback, warning = load(self.root)
        self.assertIsNotNone(warning)
        self.assertEqual(fallback, defaults())
        self.assertEqual((self.root / "config.json").read_text(), "{bad")

    def test_invalid_model_rejected_without_replacing_existing(self):
        save(self.root, defaults())
        before = (self.root / "config.json").read_bytes()
        invalid = copy.deepcopy(defaults())
        invalid["gemini"]["known_models"].append("bad/model")
        with self.assertRaises(ValueError):
            save(self.root, invalid)
        self.assertEqual((self.root / "config.json").read_bytes(), before)

    def test_config_cli_save_and_reload(self):
        script = Path(__file__).resolve().parents[1] / "src/meeting_pipeline.py"
        config = defaults()
        config["gemini"]["models"]["gemini-3.6-flash"]["rpd"] = 25
        args = [sys.executable, str(script), "config-save", "--storage-root", str(self.root)]
        saved = subprocess.run(args, input=json.dumps(config), text=True, capture_output=True)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        read = json.loads(subprocess.check_output([sys.executable, str(script), "config-get",
                                                   "--storage-root", str(self.root)]))
        self.assertEqual(read["config"], config)

    def test_transcription_fallback_uses_configured_order_without_shell(self):
        previous = (transcriber.MODEL_ORDER, transcriber.MODEL, transcriber.API)
        transcriber.MODEL_ORDER = ["safe-primary", "safe-fallback"]
        transcriber.MODEL = "safe-primary"
        transcriber.API = "https://generativelanguage.googleapis.com/v1beta/models/safe-primary:generateContent"
        urls = []
        def fake_request(command, _key, _payload, **_kwargs):
            urls.append(command[command.index("POST") + 1])
            return SimpleNamespace(stdout=json.dumps({"error": {"code": 503}} if len(urls) == 1
                                                   else {"response": "ok"}))
        try:
            with patch.object(transcriber, "transcription_payload", return_value={}), \
                 patch.object(transcriber, "run_gemini_curl", side_effect=fake_request):
                result = transcriber.transcribe("test-uri", "test-key", vocabulary=[])
            self.assertEqual(result, {"response": "ok"})
            self.assertEqual([url.split("/models/")[1].split(":")[0] for url in urls],
                             ["safe-primary", "safe-fallback"])
        finally:
            transcriber.MODEL_ORDER, transcriber.MODEL, transcriber.API = previous

    def test_all_managed_stages_read_configured_models_and_defaults(self):
        previous_trans = (transcriber.MODEL_ORDER, transcriber.MODEL, transcriber.API)
        previous_protocol = (protocol.MODELS, protocol.STAGE_MODELS)
        try:
            transcriber.configure_managed_models(self.root)
            protocol.configure_managed_models(self.root)
            self.assertEqual(transcriber.MODEL_ORDER, ["gemini-3.5-transcribe"])
            self.assertEqual(protocol.MODELS,
                             ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash"])
            self.assertEqual(protocol.STAGE_MODELS["speaker_normalization"],
                             ["gemini-3.6-flash", "gemini-3.8-flash"])
            self.assertEqual(protocol.STAGE_MODELS["name_detection"],
                             ["gemini-3.6-flash", "gemini-3.8-flash"])
            config = defaults()
            choices = config["gemini"]["stages"]
            choices["transcription"] = {"primary_model": "gemini-3.5-transcribe",
                                        "fallback_models": ["gemini-3.8-flash", "gemini-3.6-flash"]}
            choices["speaker_normalization"] = {"primary_model": "gemini-3.5-flash",
                                                 "fallback_models": ["gemini-3.6-flash"]}
            choices["name_detection"] = {"primary_model": "gemini-3.8-flash",
                                         "fallback_models": ["gemini-3.5-flash"]}
            choices["protocol_generation"] = {"primary_model": "gemini-3.5-flash",
                                               "fallback_models": ["gemini-3.8-flash", "gemini-3.6-flash"]}
            save(self.root, config)
            transcriber.configure_managed_models(self.root)
            protocol.configure_managed_models(self.root)
            self.assertEqual(transcriber.MODEL_ORDER,
                             ["gemini-3.5-transcribe", "gemini-3.8-flash", "gemini-3.6-flash"])
            self.assertIn("gemini-3.5-transcribe", transcriber.API)
            self.assertEqual(protocol.STAGE_MODELS["speaker_normalization"],
                             ["gemini-3.5-flash", "gemini-3.6-flash"])
            self.assertEqual(protocol.STAGE_MODELS["name_detection"],
                             ["gemini-3.8-flash", "gemini-3.5-flash"])
            self.assertEqual(protocol.MODELS,
                             ["gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.6-flash"])
        finally:
            transcriber.MODEL_ORDER, transcriber.MODEL, transcriber.API = previous_trans
            protocol.MODELS, protocol.STAGE_MODELS = previous_protocol

    def test_status_is_read_only_and_history_has_no_content(self):
        script = Path(__file__).resolve().parents[1] / "src/meeting_pipeline.py"
        args = [sys.executable, str(script), "status", "--storage-root", str(self.root)]
        empty = json.loads(subprocess.check_output(args))
        self.assertEqual(empty["recent_meetings"], [])
        self.assertFalse(self.root.exists())

        store = MeetingStore(self.root)
        store.initialize()
        meeting = store.create_meeting("Синтетическая встреча")
        run = store.create_transcription_run(meeting)
        published = self.root / "meetings" / meeting / "transcriptions" / run / "published"
        published.mkdir(parents=True)
        transcript = published / "sample_ПОЛНАЯ_РАСШИФРОВКА.txt"
        transcript.write_text("secret test transcript", encoding="utf-8")
        store.mark_transcription_succeeded(run, transcript.relative_to(self.root), file_sha256(transcript))
        status = json.loads(subprocess.check_output(args))
        self.assertEqual(status["recent_meetings"][0]["title"], "Синтетическая встреча")
        self.assertEqual(status["recent_meetings"][0]["status"], "transcript_ready")
        self.assertNotIn("secret test transcript", json.dumps(status))
        self.assertEqual(len(store.list_recent_meetings(1)), 1)
        analysis = store.create_analysis_run(meeting, run)
        output = self.root / "meetings" / meeting / "analyses" / analysis / "published"
        output.mkdir(parents=True)
        json_path, html_path = output / "sample.json", output / "sample.html"
        json_path.write_text("{}", encoding="utf-8")
        html_path.write_text("<html>synthetic</html>", encoding="utf-8")
        store.mark_analysis_succeeded(analysis, json_path.relative_to(self.root),
                                      html_path.relative_to(self.root),
                                      file_sha256(json_path), file_sha256(html_path))
        store.set_active_analysis(meeting, analysis)
        active = json.loads(subprocess.check_output(args))["recent_meetings"][0]
        self.assertEqual(active["status"], "protocol_ready")
        self.assertTrue(active["has_active_protocol"])
        self.assertEqual(active["protocol_path"], str(html_path.resolve()))
        second = store.create_meeting("Вторая встреча")
        with store._write() as conn:
            conn.execute("UPDATE meetings SET created_at=? WHERE id=?", ("2026-01-01T00:00:00Z", meeting))
            conn.execute("UPDATE meetings SET created_at=? WHERE id=?", ("2026-01-02T00:00:00Z", second))
        self.assertEqual([row["title"] for row in store.list_recent_meetings(1)], ["Вторая встреча"])


if __name__ == "__main__":
    unittest.main()
