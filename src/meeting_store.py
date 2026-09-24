"""Small Transcriber meeting index. No files are created on import."""

import hashlib
import os
import sqlite3
import stat
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


DEFAULT_ROOT = Path.home() / "Library/Application Support/Small Transcriber"
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE meetings (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    title TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('import', 'recording', 'legacy')),
    source_original_path TEXT,
    source_size_bytes INTEGER CHECK (source_size_bytes IS NULL OR source_size_bytes >= 0),
    source_sha256 TEXT,
    active_analysis_run_id TEXT,
    FOREIGN KEY (id, active_analysis_run_id)
        REFERENCES analysis_runs(meeting_id, id) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE transcription_runs (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL REFERENCES meetings(id),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'succeeded', 'failed', 'interrupted', 'cancelled', 'incomplete')
    ),
    backend TEXT,
    model_used TEXT,
    chunk_seconds INTEGER,
    overlap_seconds INTEGER,
    source_audio_sha256 TEXT,
    transcript_path TEXT,
    transcript_sha256 TEXT,
    error TEXT,
    UNIQUE (meeting_id, id),
    CHECK (status <> 'succeeded' OR
           (transcript_path IS NOT NULL AND length(transcript_path) > 0 AND
            transcript_sha256 IS NOT NULL AND length(transcript_sha256) = 64))
);
CREATE TABLE analysis_runs (
    id TEXT PRIMARY KEY,
    meeting_id TEXT NOT NULL,
    transcription_run_id TEXT NOT NULL,
    transcript_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'succeeded', 'failed', 'interrupted', 'cancelled', 'incomplete')
    ),
    prompt_sha256 TEXT,
    model_requested TEXT,
    models_used_json TEXT,
    additional_instructions TEXT NOT NULL DEFAULT '',
    output_json_path TEXT UNIQUE,
    output_html_path TEXT UNIQUE,
    output_json_sha256 TEXT,
    output_html_sha256 TEXT,
    error TEXT,
    UNIQUE (meeting_id, id),
    FOREIGN KEY (meeting_id, transcription_run_id)
        REFERENCES transcription_runs(meeting_id, id),
    CHECK (status <> 'succeeded' OR
           (output_json_path IS NOT NULL AND length(output_json_path) > 0 AND
            output_html_path IS NOT NULL AND length(output_html_path) > 0 AND
            output_json_sha256 IS NOT NULL AND length(output_json_sha256) = 64 AND
            output_html_sha256 IS NOT NULL AND length(output_html_sha256) = 64))
);
CREATE INDEX idx_meetings_created ON meetings(created_at DESC);
CREATE INDEX idx_transcription_meeting ON transcription_runs(meeting_id, created_at DESC);
CREATE INDEX idx_analysis_meeting ON analysis_runs(meeting_id, created_at DESC);
"""


def file_sha256(path, block_size=1024 * 1024):
    """Return a file's lowercase SHA-256 hex digest, reading in blocks.

    Raise ValueError for a nonpositive block size.
    """
    if block_size <= 0:
        raise ValueError("block_size must be positive")
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(block_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _uuid():
    return str(uuid.uuid4())


def _digest(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Expected lowercase SHA-256 hex digest")
    return value


def _required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} is required".format(name))
    return value


def _row(row):
    return dict(row) if row is not None else None


class MeetingStore:
    """SQLite index for meetings and runs; construction does not create files."""

    def __init__(self, root=None):
        """Use the app's managed root, or an explicit root for tests/integration."""
        self.root = Path(root) if root is not None else DEFAULT_ROOT
        self.db_path = self.root / "index.sqlite3"

    def initialize(self):
        """Create or reopen the private v1 DB; return None.

        Raise ValueError for a symlink root, PermissionError unless root/DB
        have modes 0700/0600, or RuntimeError for an unknown schema version
        or missing v1 tables. Existing columns are not exhaustively checked.
        """
        if self.root.is_symlink():
            raise ValueError("Managed root cannot be a symlink")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) != 0o700:
            raise PermissionError("Managed root must have mode 0700")
        try:
            fd = os.open(str(self.db_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
        if self.db_path.is_symlink() or stat.S_IMODE(self.db_path.stat().st_mode) != 0o600:
            raise PermissionError("Database must be a regular private file (0600)")
        if not self.db_path.is_file():
            raise ValueError("Database path is not a file")

        with closing(self._connect()) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if not {"meetings", "transcription_runs", "analysis_runs"} <= tables:
                    raise RuntimeError("Incomplete schema v1")
                return
            if version != 0 or conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' LIMIT 1"
            ).fetchone():
                raise RuntimeError("Unknown database schema")
            conn.executescript(
                "BEGIN IMMEDIATE;\n" + SCHEMA +
                "PRAGMA user_version = 1;\nCOMMIT;"
            )

    def _connect(self):
        # mode=rw prevents an accidental database creation outside initialize().
        uri = "file:{}?mode=rw".format(quote(str(self.db_path.absolute())))
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _write(self):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def resolve_managed_path(self, relative):
        """Return a resolved Path inside root, even if the final file is absent.

        Raise ValueError for absolute paths, '..', or symlink escape. Callers
        must check again when opening a file because the filesystem can change.
        """
        path = Path(relative)
        if path.is_absolute() or not path.parts or ".." in path.parts or path == Path("."):
            raise ValueError("Expected managed relative path without traversal")
        root = self.root.resolve()
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError("Managed path escapes root") from None
        return target

    def _managed(self, path):
        if path is None:
            raise ValueError("managed path is required")
        raw = _required(os.fspath(path), "managed path")
        resolved = self.resolve_managed_path(raw)
        # Persist the resolved location, not a symlink that may later be retargeted.
        return str(resolved.relative_to(self.root.resolve()))

    def create_meeting(self, title, source_kind="import", source_original_path=None,
                       source_size_bytes=None, source_sha256=None):
        """Create a meeting and return its UUIDv4 ID.

        Require a nonblank title and, if provided, a SHA-256 hex digest;
        otherwise raise ValueError. SQLite restricts source_kind.
        """
        meeting_id = _uuid()
        if source_sha256 is not None:
            _digest(source_sha256)
        with self._write() as conn:
            conn.execute(
                "INSERT INTO meetings (id, created_at, title, source_kind, source_original_path, "
                "source_size_bytes, source_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (meeting_id, _now(), _required(title, "title"), source_kind,
                 str(source_original_path) if source_original_path is not None else None,
                 source_size_bytes, source_sha256),
            )
        return meeting_id

    def get_meeting(self, meeting_id):
        """Return the meeting row as a dict, or None if its ID is absent."""
        with closing(self._connect()) as conn:
            return _row(conn.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone())

    def create_transcription_run(self, meeting_id, backend=None, model_used=None,
                                 chunk_seconds=None, overlap_seconds=None,
                                 source_audio_sha256=None):
        """Create a running run for an existing meeting; return its UUIDv4 ID.

        Raise ValueError for an invalid optional source audio SHA-256.
        """
        if source_audio_sha256 is not None:
            _digest(source_audio_sha256)
        run_id = _uuid()
        with self._write() as conn:
            conn.execute(
                "INSERT INTO transcription_runs "
                "(id, meeting_id, created_at, status, backend, model_used, chunk_seconds, "
                "overlap_seconds, source_audio_sha256) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?)",
                (run_id, meeting_id, _now(), backend, model_used, chunk_seconds,
                 overlap_seconds, source_audio_sha256),
            )
        return run_id

    def mark_transcription_succeeded(self, run_id, transcript_path, transcript_sha256):
        """Complete a running run with a managed transcript path and SHA-256.

        Return None; raise ValueError for invalid metadata or a nonrunning run.
        This does not verify the transcript file or its contents.
        """
        path = self._managed(transcript_path)
        digest = _digest(transcript_sha256)
        with self._write() as conn:
            changed = conn.execute(
                "UPDATE transcription_runs SET status='succeeded', completed_at=?, "
                "transcript_path=?, transcript_sha256=? WHERE id=? AND status='running'",
                (_now(), path, digest, run_id),
            ).rowcount
            if not changed:
                raise ValueError("Transcription run is not running")

    def mark_transcription_failed(self, run_id, error=None, status="failed"):
        """End a running run as failed, cancelled, or incomplete; return None.

        Raise ValueError for another status or a nonrunning run.
        """
        self._mark_unsuccessful("transcription_runs", run_id, error, status)

    def create_analysis_run(self, meeting_id, transcription_run_id, prompt_sha256=None,
                            model_requested=None, additional_instructions=""):
        """Create a running analysis and return its UUIDv4 ID.

        Require a succeeded transcription from the same meeting and a valid
        optional prompt SHA-256; otherwise raise ValueError. Copy the
        transcription's transcript hash into the new run.
        """
        if prompt_sha256 is not None:
            _digest(prompt_sha256)
        run_id = _uuid()
        with self._write() as conn:
            transcript = conn.execute(
                "SELECT transcript_sha256 FROM transcription_runs "
                "WHERE id=? AND meeting_id=? AND status='succeeded'",
                (transcription_run_id, meeting_id),
            ).fetchone()
            if transcript is None:
                raise ValueError("Successful transcription for this meeting is required")
            conn.execute(
                "INSERT INTO analysis_runs (id, meeting_id, transcription_run_id, "
                "transcript_sha256, created_at, status, prompt_sha256, model_requested, "
                "additional_instructions) VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)",
                (run_id, meeting_id, transcription_run_id, transcript[0], _now(),
                 prompt_sha256, model_requested, additional_instructions),
            )
        return run_id

    def mark_analysis_succeeded(self, run_id, output_json_path, output_html_path,
                                output_json_sha256, output_html_sha256,
                                models_used_json=None):
        """Complete a running analysis with distinct managed paths and hashes.

        Return None; raise ValueError for invalid metadata or a nonrunning run.
        This does not verify the output files or their contents.
        """
        json_path = self._managed(output_json_path)
        html_path = self._managed(output_html_path)
        if json_path == html_path:
            raise ValueError("JSON and HTML paths must differ")
        json_hash = _digest(output_json_sha256)
        html_hash = _digest(output_html_sha256)
        with self._write() as conn:
            changed = conn.execute(
                "UPDATE analysis_runs SET status='succeeded', completed_at=?, "
                "output_json_path=?, output_html_path=?, output_json_sha256=?, "
                "output_html_sha256=?, models_used_json=? WHERE id=? AND status='running'",
                (_now(), json_path, html_path, json_hash, html_hash, models_used_json, run_id),
            ).rowcount
            if not changed:
                raise ValueError("Analysis run is not running")

    def mark_analysis_failed(self, run_id, error=None, status="failed"):
        """End a running analysis as failed, cancelled, or incomplete; return None.

        Raise ValueError for another status or a nonrunning run.
        """
        self._mark_unsuccessful("analysis_runs", run_id, error, status)

    def _mark_unsuccessful(self, table, run_id, error, status):
        if status not in ("failed", "cancelled", "incomplete"):
            raise ValueError("Invalid unsuccessful status")
        with self._write() as conn:
            changed = conn.execute(
                "UPDATE {} SET status=?, completed_at=?, error=? "
                "WHERE id=? AND status='running'".format(table),
                (status, _now(), error, run_id),
            ).rowcount
            if not changed:
                raise ValueError("Run is not running")

    def set_active_analysis(self, meeting_id, analysis_run_id):
        """Atomically select a succeeded, complete run from this meeting.

        Return None; raise ValueError if the meeting or eligible run is absent.
        Previously active runs remain in the database.
        """
        with self._write() as conn:
            if conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
                raise ValueError("Meeting not found")
            run = conn.execute(
                "SELECT status, output_json_path, output_html_path, output_json_sha256, "
                "output_html_sha256 FROM analysis_runs WHERE id=? AND meeting_id=?",
                (analysis_run_id, meeting_id),
            ).fetchone()
            if run is None or run["status"] != "succeeded" or any(
                not run[key] for key in ("output_json_path", "output_html_path",
                                        "output_json_sha256", "output_html_sha256")
            ):
                raise ValueError("Successful analysis with complete output metadata is required")
            conn.execute(
                "UPDATE meetings SET active_analysis_run_id=? WHERE id=?",
                (analysis_run_id, meeting_id),
            )

    def get_active_analysis(self, meeting_id):
        """Return the active analysis row as a dict, or None if none is set."""
        with closing(self._connect()) as conn:
            return _row(conn.execute(
                "SELECT a.* FROM meetings AS m JOIN analysis_runs AS a "
                "ON a.id=m.active_analysis_run_id WHERE m.id=?", (meeting_id,)
            ).fetchone())

    def list_transcription_runs(self, meeting_id):
        """Return transcription rows for a meeting, ordered by creation and ID."""
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM transcription_runs WHERE meeting_id=? ORDER BY created_at, id",
                (meeting_id,),
            )]

    def list_analysis_runs(self, meeting_id):
        """Return analysis rows for a meeting, ordered by creation and ID."""
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM analysis_runs WHERE meeting_id=? ORDER BY created_at, id",
                (meeting_id,),
            )]

    def find_running_runs(self):
        """Return running rows grouped by transcription_runs and analysis_runs."""
        with closing(self._connect()) as conn:
            return {
                table: [dict(row) for row in conn.execute(
                    "SELECT * FROM {} WHERE status='running' ORDER BY created_at, id".format(table)
                )]
                for table in ("transcription_runs", "analysis_runs")
            }

    def mark_interrupted(self, table, run_id):
        """Mark a running run interrupted and return whether it changed.

        Raise ValueError unless table names a transcription or analysis run table.
        """
        if table not in ("transcription_runs", "analysis_runs"):
            raise ValueError("Unknown run table")
        with self._write() as conn:
            return bool(conn.execute(
                "UPDATE {} SET status='interrupted', completed_at=? "
                "WHERE id=? AND status='running'".format(table),
                (_now(), run_id),
            ).rowcount)
