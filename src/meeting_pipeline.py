"""Managed meeting pipeline. stdout is reserved for JSON-lines progress events."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from meeting_store import MeetingStore, file_sha256


HERE = Path(__file__).resolve().parent


class PipelineError(Exception):
    def __init__(self, phase, kind, message):
        super().__init__(message)
        self.phase = phase
        self.kind = kind


class ProgressParser(argparse.ArgumentParser):
    def error(self, message):
        print("Pipeline input: invalid arguments", file=sys.stderr)
        emit("pipeline_failed", phase="input", status="failed")
        self.exit(2)


def emit(event, **fields):
    record = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), flush=True)


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=False)


def checked_file(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Expected nonempty regular output file")
    return file_sha256(path)


def publish_directory(staging, published, expected):
    """Publish a complete run on the same filesystem without replacing old output."""
    if staging.parent != published.parent or not staging.is_dir() or published.exists() or published.is_symlink():
        raise ValueError("Invalid publication directories")
    if any(not path.is_file() or path.is_symlink() for path in expected):
        raise ValueError("Missing staged output")
    os.rename(staging, published)
    for path in expected:
        checked_file(published / path.name)


def run_child(script, argv, log_path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(log_path, flags, 0o600), "wb") as log:
        result = subprocess.run([sys.executable, str(script), *map(str, argv)],
                                stdout=log, stderr=subprocess.STDOUT, check=False)
    return result.returncode


def initialize_store(store):
    # First-use schema creation may race another pipeline opening the same root.
    for attempt in range(3):
        try:
            store.initialize()
            return
        except sqlite3.OperationalError as error:
            if attempt == 2 or not any(text in str(error).lower()
                                        for text in ("locked", "already exists")):
                raise
            time.sleep(0.1 * (attempt + 1))


def validate_protocol(json_path, html_path, transcript):
    checked_file(json_path)
    checked_file(html_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or data.get("source_transcript") != str(transcript)
            or not isinstance(data.get("transcript"), list)
            or not isinstance(data.get("items"), list)
            or not isinstance(data.get("summary"), dict)
            or not isinstance(data.get("categories"), list)
            or not isinstance(data.get("models_used"), list)
            or data.get("rows_count") != len(data["transcript"])
            or data.get("items_count") != len(data["items"])):
        raise ValueError("Invalid protocol JSON")


def execute(audio, root=None, transcriber=None, protocol=None):
    previous_umask = os.umask(0o077)
    ids = {}
    phase = "input"
    store = None
    current_run = None
    current_log_path = None
    meeting_dir = None
    try:
        source = Path(audio).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError("Source is not a regular file")
        source_size = source.stat().st_size
        if source_size == 0:
            raise ValueError("Source is empty")
        source_hash = file_sha256(source)

        phase = "storage"
        store = MeetingStore(root)
        initialize_store(store)
        meeting_id = store.create_meeting(source.stem, source_kind="import",
                                          source_original_path=source, source_size_bytes=source_size,
                                          source_sha256=source_hash)
        ids["meeting_id"] = meeting_id
        trans_id = store.create_transcription_run(meeting_id, source_audio_sha256=source_hash)
        ids["transcription_run_id"] = trans_id
        current_run = ("transcription", trans_id)
        meeting_dir = store.resolve_managed_path(Path("meetings") / meeting_id)
        trans_dir = store.resolve_managed_path(Path("meetings") / meeting_id / "transcriptions" / trans_id)
        private_dir(trans_dir)
        staging = trans_dir / ".staging"
        cache = trans_dir / "cache"
        private_dir(staging)
        private_dir(cache)
        current_log_path = trans_dir / "transcribe.log"
        emit("pipeline_started", meeting_dir=str(meeting_dir), **ids)
        emit("transcription_started", phase="transcription", status="running",
             current_log_path=str(current_log_path), **ids)

        phase = "transcription"
        trans_script = Path(transcriber) if transcriber else HERE / "gemini_transcribe_meeting.py"
        if run_child(trans_script, [source, "--output-dir", staging, "--cache-dir", cache,
                                    "--expected-source-sha256", source_hash], current_log_path):
            raise PipelineError(phase, "failed", "Transcriber exited unsuccessfully")
        transcript_name = source.stem + "_ПОЛНАЯ_РАСШИФРОВКА.txt"
        transcript_hash = checked_file(staging / transcript_name)
        if file_sha256(source) != source_hash or source.stat().st_size != source_size:
            raise PipelineError(phase, "incomplete", "Source changed during transcription")
        phase = "publication"
        trans_published = trans_dir / "published"
        publish_directory(staging, trans_published, [staging / transcript_name])
        transcript = trans_published / transcript_name
        if checked_file(transcript) != transcript_hash:
            raise ValueError("Published transcript hash mismatch")
        store.mark_transcription_succeeded(trans_id, transcript.relative_to(store.root.resolve()), transcript_hash)
        current_run = None
        emit("transcription_succeeded", phase="transcription", status="succeeded", **ids)

        phase = "storage"
        current_log_path = None
        analysis_id = store.create_analysis_run(meeting_id, trans_id)
        ids["analysis_run_id"] = analysis_id
        current_run = ("analysis", analysis_id)
        analysis_dir = store.resolve_managed_path(Path("meetings") / meeting_id / "analyses" / analysis_id)
        private_dir(analysis_dir)
        analysis_staging = analysis_dir / ".staging"
        private_dir(analysis_staging)
        current_log_path = analysis_dir / "protocol.log"
        emit("analysis_started", phase="analysis", status="running",
             current_log_path=str(current_log_path), **ids)

        phase = "analysis"
        protocol_script = Path(protocol) if protocol else HERE / "gemini_make_protocol.py"
        if run_child(protocol_script, [transcript, "--output-dir", analysis_staging],
                     current_log_path):
            raise PipelineError(phase, "failed", "Protocol generator exited unsuccessfully")
        if checked_file(transcript) != transcript_hash:
            raise PipelineError(phase, "incomplete", "Transcript changed during analysis")
        base = source.stem + "_ПРОТОКОЛ"
        json_name, html_name = base + ".json", base + ".html"
        validate_protocol(analysis_staging / json_name, analysis_staging / html_name, transcript)
        json_hash = checked_file(analysis_staging / json_name)
        html_hash = checked_file(analysis_staging / html_name)
        phase = "publication"
        analysis_published = analysis_dir / "published"
        publish_directory(analysis_staging, analysis_published,
                          [analysis_staging / json_name, analysis_staging / html_name])
        json_path, html_path = analysis_published / json_name, analysis_published / html_name
        validate_protocol(json_path, html_path, transcript)
        if (checked_file(json_path) != json_hash or checked_file(html_path) != html_hash
                or checked_file(transcript) != transcript_hash):
            raise ValueError("Published protocol hash mismatch")
        store.mark_analysis_succeeded(analysis_id, json_path.relative_to(store.root.resolve()),
                                      html_path.relative_to(store.root.resolve()), json_hash, html_hash)
        current_run = None
        emit("analysis_succeeded", phase="analysis", status="succeeded", **ids)
        phase = "active"
        store.set_active_analysis(meeting_id, analysis_id)
        active = store.get_active_analysis(meeting_id)
        if active is None or active["id"] != analysis_id:
            raise ValueError("Active analysis read-back mismatch")
        emit("pipeline_succeeded", phase="pipeline", status="succeeded", **ids,
             transcript_path=str(transcript), active_json_path=str(json_path),
             active_html_path=str(html_path), meeting_dir=str(meeting_dir),
             transcribe_log_path=str(trans_dir / "transcribe.log"),
             protocol_log_path=str(analysis_dir / "protocol.log"))
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, PipelineError, KeyboardInterrupt) as error:
        kind = "interrupted" if isinstance(error, KeyboardInterrupt) else (
            error.kind if isinstance(error, PipelineError) else (
                "failed" if phase in ("input", "storage") else "incomplete")
        )
        if store and current_run:
            try:
                if kind == "interrupted":
                    table = "transcription_runs" if current_run[0] == "transcription" else "analysis_runs"
                    store.mark_interrupted(table, current_run[1])
                elif current_run[0] == "transcription":
                    store.mark_transcription_failed(current_run[1], str(error), status=kind)
                else:
                    store.mark_analysis_failed(current_run[1], str(error), status=kind)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as update_error:
                print("Could not update run status: {}".format(type(update_error).__name__), file=sys.stderr)
        print("Pipeline {}: {}".format(phase, type(error).__name__), file=sys.stderr)
        fields = dict(ids)
        if meeting_dir is not None:
            fields["meeting_dir"] = str(meeting_dir)
        if current_log_path is not None:
            fields["current_log_path"] = str(current_log_path)
        emit("pipeline_failed", phase=phase, status=kind, **fields)
        return 1
    finally:
        os.umask(previous_umask)


def main(argv=None):
    parser = ProgressParser(description="Managed meeting pipeline")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("audio", type=Path)
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--transcriber-script", type=Path, help="test/dev child script")
    parser.add_argument("--protocol-script", type=Path, help="test/dev child script")
    args = parser.parse_args(argv)
    return execute(args.audio, args.storage_root, args.transcriber_script, args.protocol_script)


if __name__ == "__main__":
    sys.exit(main())
