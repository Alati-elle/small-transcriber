"""Synthetic JSON-lines child for Swift development checks; no Gemini or SQLite."""

import json
import os
import sys
from pathlib import Path


def emit(event, **fields):
    print(json.dumps(dict(event=event, timestamp="2026-01-01T00:00:00+00:00", **fields)), flush=True)


def main():
    assert sys.argv[1] == "run"
    assert "--storage-root" in sys.argv
    root = Path(sys.argv[sys.argv.index("--storage-root") + 1])
    scenario = os.environ.get("FAKE_PIPELINE_SCENARIO", "success")
    meeting_dir = root / "synthetic-meeting"
    log = meeting_dir / "protocol.log"
    emit("pipeline_started", meeting_id="meeting-synthetic", meeting_dir=str(meeting_dir))
    emit("transcription_started", phase="transcription", current_log_path=str(meeting_dir / "transcribe.log"))
    emit("transcription_succeeded", phase="transcription")
    emit("analysis_started", phase="analysis", current_log_path=str(log))
    if scenario == "stage_failure":
        meeting_dir.mkdir(parents=True)
        log.write_text("synthetic failure", encoding="utf-8")
        emit("pipeline_failed", phase="analysis", status="failed", meeting_dir=str(meeting_dir),
             current_log_path=str(log))
        return 4
    if scenario == "malformed":
        print("{not-json", flush=True)
    if scenario == "missing_final":
        return 0
    if scenario != "missing_meeting":
        meeting_dir.mkdir(parents=True)
    else:
        root.mkdir(parents=True)
    html = (root if scenario == "missing_meeting" else meeting_dir) / "synthetic.html"
    if scenario != "missing_html":
        html.write_text("<html>synthetic</html>", encoding="utf-8")
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
