"""Managed meeting pipeline. stdout is reserved for JSON-lines progress events."""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from meeting_store import MeetingStore, file_sha256
from gemini_telemetry import PREFIX


HERE = Path(__file__).resolve().parent


class PipelineError(Exception):
    def __init__(self, phase, kind, message, error_code=None):
        super().__init__(message)
        self.phase = phase
        self.kind = kind
        self.error_code = error_code


class ProgressParser(argparse.ArgumentParser):
    def error(self, message):
        print("Pipeline input: invalid arguments", file=sys.stderr)
        emit("pipeline_failed", phase="input", status="failed")
        self.exit(2)


def emit(event, **fields):
    record = {"event": event, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False), flush=True)


STAGES = {"transcription", "speaker_normalization", "name_detection",
          "protocol_generation", "finalization"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
SAFE_MESSAGES = {"preparing_audio": "Подготовка аудио…",
                 "merging_transcript": "Объединение расшифровки…",
                 "optional_skipped": "Этап пропущен. Создание протокола продолжено."}


def safe_child_fields(event, record):
    """Allowlist child progress; never forward arbitrary text or raw API fields."""
    if event == "usage_updated":
        models = record.get("models")
        if not isinstance(models, dict):
            return None
        clean_models = {key: value for key, value in models.items()
                        if isinstance(key, str) and SAFE_NAME.fullmatch(key)
                        and type(value) is int and value >= 0}
        total = record.get("total")
        if type(total) is not int or total < 0:
            return None
        limits = record.get("observed_daily_limits", {})
        if not isinstance(limits, dict):
            limits = {}
        quota_date = record.get("quota_date")
        return {"quota_date": quota_date if isinstance(quota_date, str) and
                re.fullmatch(r"\d{4}-\d{2}-\d{2}", quota_date) else "",
                "models": clean_models, "total": total,
                "upload": record.get("upload") if type(record.get("upload")) is int else 0,
                "observed_daily_limits": {k: v for k, v in limits.items()
                                          if isinstance(k, str) and SAFE_NAME.fullmatch(k)
                                          and type(v) in (int, float) and v >= 0}}
    stage_name = record.get("stage")
    if stage_name not in STAGES:
        return None
    fields = {"stage": stage_name}
    if event.startswith("stage_"):
        code = record.get("message_code")
        if not isinstance(code, str) or not SAFE_NAME.fullmatch(code):
            code = "progress"
        fields.update(status=record.get("status") if record.get("status") in
                      ("running", "succeeded", "warning", "failed", "progress") else "progress",
                      message_code=code)
        if code in SAFE_MESSAGES:
            fields["safe_message"] = SAFE_MESSAGES[code]
        elif code in ("transcription_chunk", "protocol_batch"):
            number = record.get("chunk") if code == "transcription_chunk" else record.get("batch")
            count = record.get("chunks") if code == "transcription_chunk" else record.get("batches")
            if type(number) is int and type(count) is int and 0 < number <= count <= 100000:
                fields["safe_message"] = ("Фрагмент" if code == "transcription_chunk" else "Батч") + f" {number} из {count}"
        return fields
    model = record.get("model")
    if not isinstance(model, str) or not SAFE_NAME.fullmatch(model):
        return None
    fields.update(model=model, request_type=record.get("request_type") if record.get("request_type") in
                  ("generate_content", "upload", "other") else "other")
    for name in ("attempt", "max_attempts", "http_status"):
        if type(record.get(name)) is int and 0 <= record[name] <= 10000:
            fields[name] = record[name]
    for name in ("quota_kind",):
        if record.get(name) in ("rate_limit_rpm", "rate_limit_tpm", "daily_quota_exhausted",
                                "rate_limit_unknown", "gemini_overloaded", "other_gemini_error"):
            fields[name] = record[name]
    delay = record.get("retry_after_seconds")
    if type(delay) in (int, float) and 0 <= delay <= 3600:
        fields["retry_after_seconds"] = delay
    return fields


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


def run_child(script, argv, log_path, env=None):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    with os.fdopen(os.open(log_path, flags, 0o600), "wb") as log:
        with subprocess.Popen([sys.executable, str(script), *map(str, argv)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env) as child:
            for line in child.stdout:
                if line.startswith(PREFIX.encode()):
                    try:
                        record = json.loads(line[len(PREFIX):])
                        if isinstance(record, dict) and record.get("event") in (
                            "stage_started", "stage_succeeded", "stage_warning", "stage_failed",
                            "stage_progress", "gemini_request_started", "gemini_request_retry",
                            "gemini_request_succeeded", "gemini_request_failed", "usage_updated"):
                            event = record.pop("event")
                            safe = safe_child_fields(event, record)
                            if safe is not None:
                                emit(event, operation=(env or {}).get("SMALL_TRANSCRIBER_OPERATION", "full"),
                                     **safe)
                                continue
                    except (ValueError, UnicodeDecodeError, TypeError):
                        pass
                log.write(line)
            return child.wait()


def child_env(store, ids, operation):
    env = os.environ.copy()
    env.update(SMALL_TRANSCRIBER_STORAGE_ROOT=str(store.root.resolve()),
               SMALL_TRANSCRIBER_OPERATION=operation,
               SMALL_TRANSCRIBER_MEETING_ID=ids.get("meeting_id", ""),
               SMALL_TRANSCRIBER_TRANSCRIPTION_RUN_ID=ids.get("transcription_run_id", ""),
               SMALL_TRANSCRIBER_ANALYSIS_RUN_ID=ids.get("analysis_run_id", ""))
    return env


def stage(name, status, operation, message_code=None, **ids):
    emit("stage_" + status, operation=operation, stage=name,
         status="running" if status == "started" else status,
         message_code=message_code or status, **ids)


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
        emit("pipeline_started", operation="full", meeting_dir=str(meeting_dir), **ids)
        emit("usage_updated", **store.gemini_usage_snapshot())
        stage("transcription", "started", "full", **ids)
        emit("transcription_started", phase="transcription", status="running",
             current_log_path=str(current_log_path), **ids)

        phase = "transcription"
        trans_script = Path(transcriber) if transcriber else HERE / "gemini_transcribe_meeting.py"
        if run_child(trans_script, [source, "--output-dir", staging, "--cache-dir", cache,
                                    "--expected-source-sha256", source_hash], current_log_path,
                     child_env(store, ids, "full")):
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
        stage("transcription", "succeeded", "full", **ids)
        emit("transcription_succeeded", phase="transcription", status="succeeded",
             transcript_path=str(transcript), **ids)

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
        protocol_exit = run_child(protocol_script, [transcript, "--output-dir", analysis_staging],
                                  current_log_path, child_env(store, ids, "full"))
        if protocol_exit:
            raise PipelineError(phase, "failed", "Protocol generator exited unsuccessfully",
                                {75: "gemini_overloaded", 76: "daily_quota_exhausted"}.get(protocol_exit))
        stage("protocol_generation", "succeeded", "full", **ids)
        stage("finalization", "started", "full", "validating_publication", **ids)
        phase = "publication"
        if checked_file(transcript) != transcript_hash:
            raise PipelineError(phase, "incomplete", "Transcript changed during analysis")
        base = source.stem + "_ПРОТОКОЛ"
        json_name, html_name = base + ".json", base + ".html"
        validate_protocol(analysis_staging / json_name, analysis_staging / html_name, transcript)
        json_hash = checked_file(analysis_staging / json_name)
        html_hash = checked_file(analysis_staging / html_name)
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
        stage("finalization", "succeeded", "full", **ids)
        emit("pipeline_succeeded", phase="pipeline", status="succeeded", operation="full", **ids,
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
        if isinstance(error, PipelineError) and error.error_code:
            fields["error_code"] = error.error_code
        fields["operation"] = "full"
        failed_stage = "transcription" if phase == "transcription" else (
            "finalization" if phase in ("publication", "active") else "protocol_generation")
        if phase not in ("input", "storage"):
            stage(failed_stage, "failed", "full", fields.get("error_code", "failed"), **ids)
        emit("pipeline_failed", phase=phase, status=kind, **fields)
        return 1
    finally:
        os.umask(previous_umask)


def retry_analysis(meeting_id, root=None, transcription_run_id=None, protocol=None):
    """Create a new analysis from a verified published transcript, without audio."""
    previous_umask = os.umask(0o077)
    ids = {"meeting_id": meeting_id}
    phase = "input"
    store = None
    current_run = None
    current_log_path = None
    meeting_dir = None
    try:
        store = MeetingStore(root)
        if not store.root.is_dir() or not store.db_path.is_file():
            raise ValueError("Existing managed storage is required")
        phase = "storage"
        initialize_store(store)
        meeting = store.get_meeting(meeting_id)
        if meeting is None:
            raise ValueError("Meeting not found")
        successful = [run for run in store.list_transcription_runs(meeting_id)
                      if run["status"] == "succeeded"]
        if transcription_run_id is None:
            if len(successful) != 1:
                raise ValueError("Exactly one successful transcription is required; select a run ID")
            selected = successful[0]
        else:
            selected = next((run for run in successful if run["id"] == transcription_run_id), None)
            if selected is None:
                raise ValueError("Successful transcription run not found for meeting")
        ids["transcription_run_id"] = selected["id"]
        relative = Path(selected["transcript_path"])
        expected_parent = (Path("meetings") / meeting_id / "transcriptions" /
                           selected["id"] / "published")
        if relative.parent != expected_parent or not relative.name.endswith(
                "_ПОЛНАЯ_РАСШИФРОВКА.txt"):
            raise ValueError("Unexpected published transcript path")
        transcript = store.resolve_managed_path(relative)
        transcript_hash = checked_file(transcript)
        if transcript_hash != selected["transcript_sha256"]:
            raise ValueError("Published transcript hash mismatch")
        suffix = "_ПОЛНАЯ_РАСШИФРОВКА.txt"
        base = relative.name[:-len(suffix)]
        meeting_dir = store.resolve_managed_path(Path("meetings") / meeting_id)
        emit("pipeline_started", operation="analysis_retry", meeting_dir=str(meeting_dir),
             transcript_path=str(transcript), **ids)
        emit("usage_updated", **store.gemini_usage_snapshot())
        stage("transcription", "succeeded", "analysis_retry", "reused_transcript", **ids)

        analysis_id = store.create_analysis_run(meeting_id, selected["id"])
        ids["analysis_run_id"] = analysis_id
        current_run = analysis_id
        analysis_dir = store.resolve_managed_path(Path("meetings") / meeting_id /
                                                  "analyses" / analysis_id)
        private_dir(analysis_dir)
        staging = analysis_dir / ".staging"
        private_dir(staging)
        current_log_path = analysis_dir / "protocol.log"
        phase = "analysis"
        emit("analysis_started", phase="analysis", status="running", operation="analysis_retry",
             current_log_path=str(current_log_path), **ids)
        protocol_script = Path(protocol) if protocol else HERE / "gemini_make_protocol.py"
        protocol_exit = run_child(protocol_script, [transcript, "--output-dir", staging],
                                  current_log_path, child_env(store, ids, "analysis_retry"))
        if protocol_exit:
            raise PipelineError(phase, "failed", "Protocol generator exited unsuccessfully",
                                {75: "gemini_overloaded", 76: "daily_quota_exhausted"}.get(protocol_exit))
        stage("protocol_generation", "succeeded", "analysis_retry", **ids)
        stage("finalization", "started", "analysis_retry", "validating_publication", **ids)
        phase = "publication"
        if checked_file(transcript) != transcript_hash:
            raise PipelineError(phase, "incomplete", "Transcript changed during analysis")
        json_name, html_name = base + "_ПРОТОКОЛ.json", base + "_ПРОТОКОЛ.html"
        validate_protocol(staging / json_name, staging / html_name, transcript)
        json_hash = checked_file(staging / json_name)
        html_hash = checked_file(staging / html_name)
        published = analysis_dir / "published"
        publish_directory(staging, published, [staging / json_name, staging / html_name])
        json_path, html_path = published / json_name, published / html_name
        validate_protocol(json_path, html_path, transcript)
        if (checked_file(json_path) != json_hash or checked_file(html_path) != html_hash or
                checked_file(transcript) != transcript_hash):
            raise ValueError("Published output hash mismatch")
        store.mark_analysis_succeeded(analysis_id, json_path.relative_to(store.root.resolve()),
                                      html_path.relative_to(store.root.resolve()), json_hash, html_hash)
        current_run = None
        emit("analysis_succeeded", phase="analysis", status="succeeded",
             operation="analysis_retry", **ids)
        phase = "active"
        store.set_active_analysis(meeting_id, analysis_id)
        active = store.get_active_analysis(meeting_id)
        if active is None or active["id"] != analysis_id:
            raise ValueError("Active analysis read-back mismatch")
        stage("finalization", "succeeded", "analysis_retry", **ids)
        emit("pipeline_succeeded", phase="pipeline", status="succeeded",
             operation="analysis_retry", transcript_path=str(transcript),
             active_json_path=str(json_path), active_html_path=str(html_path),
             meeting_dir=str(meeting_dir), protocol_log_path=str(current_log_path), **ids)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error, PipelineError, KeyboardInterrupt) as error:
        kind = "interrupted" if isinstance(error, KeyboardInterrupt) else (
            error.kind if isinstance(error, PipelineError) else (
                "failed" if phase in ("input", "storage") else "incomplete"))
        if store and current_run:
            try:
                store.mark_analysis_failed(current_run, str(error), status=kind)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as update_error:
                print("Could not update run status: {}".format(type(update_error).__name__), file=sys.stderr)
        print("Pipeline {}: {}".format(phase, type(error).__name__), file=sys.stderr)
        fields = dict(ids, operation="analysis_retry")
        if meeting_dir is not None:
            fields["meeting_dir"] = str(meeting_dir)
        if current_log_path is not None:
            fields["current_log_path"] = str(current_log_path)
        if isinstance(error, PipelineError) and error.error_code:
            fields["error_code"] = error.error_code
        if phase not in ("input", "storage"):
            stage("finalization" if phase in ("publication", "active") else "protocol_generation",
                  "failed", "analysis_retry", fields.get("error_code", "failed"), **ids)
        emit("pipeline_failed", phase=phase, status=kind, **fields)
        return 1
    finally:
        os.umask(previous_umask)


def main(argv=None):
    parser = ProgressParser(description="Managed meeting pipeline")
    parser.add_argument("command", choices=["run", "retry-analysis"])
    parser.add_argument("audio", type=Path, nargs="?")
    parser.add_argument("--meeting-id")
    parser.add_argument("--transcription-run-id")
    parser.add_argument("--storage-root", type=Path)
    parser.add_argument("--transcriber-script", type=Path, help="test/dev child script")
    parser.add_argument("--protocol-script", type=Path, help="test/dev child script")
    args = parser.parse_args(argv)
    if args.command == "run":
        if args.audio is None or args.meeting_id or args.transcription_run_id:
            parser.error("run requires audio and cannot select an existing meeting")
        return execute(args.audio, args.storage_root, args.transcriber_script, args.protocol_script)
    if args.audio is not None or not args.meeting_id or args.transcriber_script:
        parser.error("retry-analysis requires --meeting-id and no audio or transcriber")
    return retry_analysis(args.meeting_id, args.storage_root, args.transcription_run_id,
                          args.protocol_script)


if __name__ == "__main__":
    sys.exit(main())
