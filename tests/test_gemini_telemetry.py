"""Synthetic quota and local usage checks; no network or user storage."""

import os
import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gemini_telemetry import classify, request, request_started
from meeting_store import MeetingStore, quota_date_pacific


def quota(quota_id, value=20, delay="48s"):
    return {"code": 429, "message": "PRIVATE BODY", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
            {"quotaId": quota_id, "quotaMetric": "generate_content_requests", "quotaValue": value,
             "description": "PRIVATE DESCRIPTION"}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "storage"
        self.store = MeetingStore(self.root)
        self.store.initialize()

    def test_classifications_are_metadata_only(self):
        for quota_id, expected in (("GenerateRequestsPerDay", "daily_quota_exhausted"),
                                   ("GenerateRequestsPerMinute", "rate_limit_rpm"),
                                   ("GenerateTokensPerMinute", "rate_limit_tpm")):
            kind, observation = classify(quota(quota_id))
            self.assertEqual(kind, expected)
            self.assertEqual(observation["retry_after_seconds"], 48)
            self.assertEqual(observation["quota_value"], 20)
            self.assertNotIn("PRIVATE", str(observation))
        self.assertEqual(classify({"code": 429, "message": "per-day"})[0], "rate_limit_unknown")
        self.assertEqual(classify({"code": 503})[0], "gemini_overloaded")
        self.assertEqual(classify("bad")[0], "other_gemini_error")

    def test_usage_counts_actual_result_and_only_safe_columns(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"SMALL_TRANSCRIBER_STORAGE_ROOT": str(self.root)}), \
                contextlib.redirect_stdout(output):
            request_started("protocol_generation", "gemini-test", 1)
            self.assertEqual(self.store.gemini_usage_snapshot()["total"], 0)
            for attempt, result in enumerate((
                SimpleNamespace(stdout='{"candidates":[]}', returncode=0),
                SimpleNamespace(stdout='{"error":' + __import__('json').dumps(quota("GenerateRequestsPerDay")) + '}', returncode=0),
                SimpleNamespace(stdout='{"error":{"code":503}}', returncode=0)), 1):
                request("protocol_generation", "gemini-test", attempt, result)
            request("transcription", "gemini-test", 1,
                    SimpleNamespace(stdout='{"file":{"uri":"synthetic"}}', returncode=0), "upload")
            request("transcription", "gemini-test", 2,
                    SimpleNamespace(stdout='', returncode=6), "upload")
        snapshot = self.store.gemini_usage_snapshot()
        self.assertEqual(snapshot["models"], {"gemini-test": 3})
        self.assertEqual(snapshot["total"], 3)
        self.assertEqual(snapshot["upload"], 1)
        with self.store._connect() as conn:
            rows = conn.execute("SELECT outcome FROM gemini_request_usage ORDER BY id").fetchall()
            schema = " ".join(row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        self.assertEqual([row[0] for row in rows], ["succeeded", "http_429", "http_503", "succeeded"])
        self.assertNotIn("PRIVATE", schema)
        self.assertEqual(snapshot["observed_daily_limits"]["gemini-test"], 20)
        self.assertNotIn("PRIVATE", output.getvalue())
        self.assertIn('"event": "usage_updated"', output.getvalue())

    def test_pacific_day_boundary(self):
        self.assertEqual(quota_date_pacific(datetime(2026, 9, 25, 6, 59, tzinfo=timezone.utc)),
                         "2026-09-24")
        self.assertEqual(quota_date_pacific(datetime(2026, 9, 25, 7, 0, tzinfo=timezone.utc)),
                         "2026-09-25")

    def test_v1_migration_preserves_rows_and_is_idempotent(self):
        meeting = self.store.create_meeting("Synthetic")
        run = self.store.create_transcription_run(meeting)
        with self.store._connect() as conn:
            conn.executescript("DROP TABLE gemini_quota_observations; DROP TABLE gemini_request_usage; "
                               "PRAGMA user_version=1;")
        self.store.initialize()
        self.store.initialize()
        self.assertEqual(self.store.get_meeting(meeting)["title"], "Synthetic")
        self.assertEqual(self.store.list_transcription_runs(meeting)[0]["id"], run)
        with self.store._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
