"""Synthetic, temporary-only checks for the meeting storage core."""

import hashlib
import sqlite3
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meeting_store import MeetingStore, file_sha256  # noqa: E402


HASH = "a" * 64


class MeetingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.store = MeetingStore(self.root)
        self.assertFalse(self.root.exists())
        self.store.initialize()

    def _transcription(self, meeting_id):
        run_id = self.store.create_transcription_run(meeting_id, backend="synthetic")
        self.store.mark_transcription_succeeded(
            run_id, "meetings/{}/transcript.txt".format(meeting_id), HASH
        )
        return run_id

    def _analysis(self, meeting_id, transcription_id, suffix):
        run_id = self.store.create_analysis_run(meeting_id, transcription_id)
        prefix = "meetings/{}/analysis/{}".format(meeting_id, suffix)
        self.store.mark_analysis_succeeded(
            run_id, prefix + "/protocol.json", prefix + "/protocol.html", HASH, HASH
        )
        return run_id

    def test_schema_version_idempotence_and_permissions(self):
        self.store.initialize()
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.store.db_path.stat().st_mode), 0o600)
        with self.store._connect() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        self.assertTrue({"meetings", "transcription_runs", "analysis_runs"} <= names)

    def test_existing_insecure_permissions_are_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(PermissionError):
            self.store.initialize()
        self.root.chmod(0o700)
        self.store.db_path.chmod(0o644)
        with self.assertRaises(PermissionError):
            self.store.initialize()

    def test_meeting_and_unique_uuid_ids(self):
        first = self.store.create_meeting("Synthetic one")
        second = self.store.create_meeting("Synthetic two")
        self.assertNotEqual(first, second)
        self.assertEqual(uuid.UUID(first).version, 4)
        self.assertEqual(self.store.get_meeting(first)["title"], "Synthetic one")
        self.assertIsNone(self.store.get_meeting("missing"))
        transcription = self._transcription(first)
        analysis = self.store.create_analysis_run(first, transcription)
        self.assertEqual(uuid.UUID(transcription).version, 4)
        self.assertEqual(uuid.UUID(analysis).version, 4)
        self.assertEqual(len({first, second, transcription, analysis}), 4)

    def test_foreign_keys_and_transcription_statuses(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_transcription_run("missing")
        meeting = self.store.create_meeting("Synthetic")
        run = self.store.create_transcription_run(meeting)
        with self.assertRaises(ValueError):
            self.store.mark_transcription_succeeded(run, "", HASH)
        with self.assertRaises(ValueError):
            self.store.mark_transcription_succeeded(run, "transcript.txt", "bad")
        self.store.mark_transcription_failed(run, "synthetic failure")
        self.assertEqual(self.store.list_transcription_runs(meeting)[0]["status"], "failed")
        self.assertIsNone(self.store.list_transcription_runs(meeting)[0]["transcript_path"])
        with self.assertRaises(ValueError):
            self.store.mark_transcription_succeeded(run, "transcript.txt", HASH)

    def test_analysis_required_metadata_and_active_switch(self):
        meeting = self.store.create_meeting("Synthetic")
        transcription = self._transcription(meeting)
        run = self.store.create_analysis_run(meeting, transcription)
        self.assertEqual(self.store.list_analysis_runs(meeting)[0]["transcript_sha256"], HASH)
        with self.assertRaises(ValueError):
            self.store.mark_analysis_succeeded(run, "", "protocol.html", HASH, HASH)
        with self.assertRaises(ValueError):
            self.store.mark_analysis_succeeded(run, None, "protocol.html", HASH, HASH)
        with self.assertRaises(ValueError):
            self.store.mark_analysis_succeeded(run, "protocol.json", "protocol.html", HASH, "bad")
        with self.assertRaises(ValueError):
            self.store.mark_analysis_succeeded(run, "same", "same", HASH, HASH)
        with self.assertRaises(ValueError):
            self.store.set_active_analysis(meeting, run)
        first = self._analysis(meeting, transcription, "one")
        second = self._analysis(meeting, transcription, "two")
        self.store.set_active_analysis(meeting, first)
        self.assertEqual(self.store.get_active_analysis(meeting)["id"], first)
        self.store.set_active_analysis(meeting, second)
        self.assertEqual(self.store.get_active_analysis(meeting)["id"], second)
        self.assertIn(first, {r["id"] for r in self.store.list_analysis_runs(meeting)})

    def test_unsuccessful_analyses_and_other_meeting_cannot_be_active(self):
        meeting = self.store.create_meeting("Synthetic one")
        other = self.store.create_meeting("Synthetic two")
        transcription = self._transcription(meeting)
        old_active = self._analysis(meeting, transcription, "old")
        self.store.set_active_analysis(meeting, old_active)
        for status in ("failed", "cancelled", "incomplete"):
            run = self.store.create_analysis_run(meeting, transcription)
            self.store.mark_analysis_failed(run, "synthetic", status=status)
            with self.assertRaises(ValueError):
                self.store.set_active_analysis(meeting, run)
            self.assertEqual(self.store.get_active_analysis(meeting)["id"], old_active)
        interrupted = self.store.create_analysis_run(meeting, transcription)
        self.assertTrue(self.store.mark_interrupted("analysis_runs", interrupted))
        with self.assertRaises(ValueError):
            self.store.set_active_analysis(meeting, interrupted)
        self.assertEqual(self.store.get_active_analysis(meeting)["id"], old_active)
        success = self._analysis(meeting, transcription, "success")
        with self.assertRaises(ValueError):
            self.store.set_active_analysis(other, success)
        self.assertIsNone(self.store.get_active_analysis(other))
        with self.assertRaises(ValueError):
            self.store.create_analysis_run(other, transcription)

    def test_composite_active_fk_rejects_foreign_meeting(self):
        meeting = self.store.create_meeting("Synthetic one")
        other = self.store.create_meeting("Synthetic two")
        transcription = self._transcription(other)
        run = self._analysis(other, transcription, "foreign")
        conn = self.store._connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                with conn:
                    conn.execute(
                        "UPDATE meetings SET active_analysis_run_id=? WHERE id=?",
                        (run, meeting),
                    )
        finally:
            conn.close()
        self.assertIsNone(self.store.get_active_analysis(meeting))

    def test_running_recovery_primitives(self):
        meeting = self.store.create_meeting("Synthetic")
        transcription = self.store.create_transcription_run(meeting)
        self.assertEqual(len(self.store.find_running_runs()["transcription_runs"]), 1)
        self.assertTrue(self.store.mark_interrupted("transcription_runs", transcription))
        self.assertFalse(self.store.mark_interrupted("transcription_runs", transcription))
        self.assertFalse(self.store.find_running_runs()["transcription_runs"])
        with self.assertRaises(ValueError):
            self.store.mark_interrupted("meetings", transcription)

    def test_hash_is_streamed_and_correct(self):
        payload = b"synthetic bytes" * 10000
        path = self.root / "sample.bin"
        path.write_bytes(payload)
        self.assertEqual(file_sha256(path, block_size=7), hashlib.sha256(payload).hexdigest())
        with self.assertRaises(ValueError):
            file_sha256(path, block_size=0)

    def test_managed_paths_block_traversal_absolute_and_symlink_escape(self):
        with self.assertRaises(ValueError):
            self.store.resolve_managed_path("../escape")
        with self.assertRaises(ValueError):
            self.store.resolve_managed_path("/tmp/escape")
        with self.assertRaises(ValueError):
            self.store.resolve_managed_path(".")
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.resolve_managed_path("link/escape")
        self.assertEqual(self.store.resolve_managed_path("inside/ok"), (self.root / "inside/ok").resolve())

    def test_internal_symlink_is_canonicalized_before_storage(self):
        inside = self.root / "inside"
        inside.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(inside, target_is_directory=True)
        self.assertEqual(self.store.resolve_managed_path("alias/transcript.txt"),
                         (inside / "transcript.txt").resolve())
        meeting = self.store.create_meeting("Synthetic")
        run = self.store.create_transcription_run(meeting)
        self.store.mark_transcription_succeeded(run, "alias/transcript.txt", HASH)
        self.assertEqual(self.store.list_transcription_runs(meeting)[0]["transcript_path"],
                         "inside/transcript.txt")
        alias.unlink()
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        alias.symlink_to(outside, target_is_directory=True)
        stored = self.store.list_transcription_runs(meeting)[0]["transcript_path"]
        self.assertEqual(self.store.resolve_managed_path(stored),
                         (inside / "transcript.txt").resolve())

    def test_reopen_preserves_data(self):
        meeting = self.store.create_meeting("Synthetic")
        transcription = self._transcription(meeting)
        analysis = self._analysis(meeting, transcription, "persist")
        self.store.set_active_analysis(meeting, analysis)
        reopened = MeetingStore(self.root)
        reopened.initialize()
        self.assertEqual(reopened.get_meeting(meeting)["title"], "Synthetic")
        self.assertEqual(reopened.get_active_analysis(meeting)["id"], analysis)


if __name__ == "__main__":
    unittest.main()
