"""Synthetic JSON-lines child for Swift development checks; no Gemini or SQLite."""

import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from meeting_config import defaults


def emit(event, **fields):
    print(json.dumps(dict(event=event, timestamp="2026-01-01T00:00:00+00:00", **fields)), flush=True)


def main():
    assert sys.argv[1] in ("run", "retry-analysis", "status", "config-get", "config-save")
    assert "--storage-root" in sys.argv
    root = Path(sys.argv[sys.argv.index("--storage-root") + 1])
    scenario = os.environ.get("FAKE_PIPELINE_SCENARIO", "success")
    if sys.argv[1] == "status":
        recent = [] if scenario == "empty" else [{
            "meeting_id": "meeting-synthetic", "title": "Тестовая встреча",
            "created_at": "2026-09-25T19:34:00Z", "status": "protocol_ready",
            "has_transcript": True, "has_active_protocol": True,
            "transcription_run_id": "transcription-synthetic",
            "protocol_path": str(root / "synthetic-meeting" / "synthetic.html")
        }]
        print(json.dumps({"usage": {"quota_date": "2026-09-25",
              "models": {"gemini-3.6-flash": 12}, "total": 12, "upload": 3,
              "observed_daily_limits": {}}, "recent_meetings": recent,
              "manual_limits": defaults()["gemini"]["models"], "config_warning": None}))
        return 0
    if sys.argv[1] == "config-get":
        print(json.dumps({"config": defaults(), "warning": None,
                          "path": str(root / "config.json")}))
        return 0
    if sys.argv[1] == "config-save":
        json.load(sys.stdin)
        print(json.dumps({"saved": True}))
        return 0
    meeting_dir = root / "synthetic-meeting"
    log = meeting_dir / "protocol.log"
    if sys.argv[1] == "retry-analysis":
        assert "--meeting-id" in sys.argv and "--transcription-run-id" in sys.argv
        assert not any(arg.endswith(".m4a") for arg in sys.argv)
        meeting_dir.mkdir(parents=True, exist_ok=True)
        transcript = meeting_dir / "synthetic.txt"
        transcript.write_text("synthetic transcript", encoding="utf-8")
        fields = dict(operation="analysis_retry", meeting_id="meeting-synthetic",
                      transcription_run_id="transcription-synthetic")
        emit("pipeline_started", meeting_dir=str(meeting_dir), transcript_path=str(transcript), **fields)
        emit("analysis_started", phase="analysis", current_log_path=str(log), **fields)
        if scenario in ("retry_overloaded", "retry_generic"):
            log.write_text("synthetic failure", encoding="utf-8")
            extra = {"error_code": "gemini_overloaded"} if scenario == "retry_overloaded" else {}
            emit("pipeline_failed", phase="analysis", status="failed", meeting_dir=str(meeting_dir),
                 current_log_path=str(log), **fields, **extra)
            return 4
        html = meeting_dir / "synthetic.html"
        html.write_text("<html>synthetic retry</html>", encoding="utf-8")
        emit("analysis_succeeded", phase="analysis", **fields)
        emit("pipeline_succeeded", analysis_run_id="analysis-retry-synthetic",
             transcript_path=str(transcript), active_json_path=str(meeting_dir / "synthetic.json"),
             active_html_path=str(html), meeting_dir=str(meeting_dir), **fields)
        return 0
    emit("pipeline_started", meeting_id="meeting-synthetic", meeting_dir=str(meeting_dir))
    emit("transcription_started", phase="transcription", current_log_path=str(meeting_dir / "transcribe.log"))
    emit("transcription_succeeded", phase="transcription", meeting_id="meeting-synthetic",
         transcription_run_id="transcription-synthetic",
         transcript_path=str(meeting_dir / "synthetic.txt"))
    emit("analysis_started", phase="analysis", current_log_path=str(log))
    if scenario == "stage_failure":
        meeting_dir.mkdir(parents=True, exist_ok=True)
        log.write_text("synthetic failure", encoding="utf-8")
        emit("pipeline_failed", phase="analysis", status="failed", meeting_dir=str(meeting_dir),
             current_log_path=str(log))
        return 4
    if scenario == "malformed":
        print("{not-json", flush=True)
    if scenario == "missing_final":
        return 0
    if scenario != "missing_meeting":
        meeting_dir.mkdir(parents=True, exist_ok=True)
    else:
        root.mkdir(parents=True)
    html = (root if scenario == "missing_meeting" else meeting_dir) / "synthetic.html"
    if scenario != "missing_html":
        html.write_text("<html>synthetic</html>", encoding="utf-8")
    for name in ("transcription", "speaker_normalization", "name_detection",
                 "protocol_generation", "finalization"):
        emit("stage_succeeded", stage=name, status="succeeded")
    emit("analysis_succeeded", phase="analysis")
    emit("pipeline_succeeded", meeting_id="meeting-synthetic",
         transcription_run_id="transcription-synthetic", analysis_run_id="analysis-synthetic",
         transcript_path=str(meeting_dir / "synthetic.txt"),
         active_json_path=str(meeting_dir / "synthetic.json"), active_html_path=str(html),
         meeting_dir=str(meeting_dir), transcribe_log_path=str(meeting_dir / "transcribe.log"),
         protocol_log_path=str(log))
    return 5 if scenario == "nonzero_after_success" else 0


if __name__ == "__main__":
    sys.exit(main())
