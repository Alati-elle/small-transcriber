"""Safe managed-only Gemini progress, quota classification and local attempt accounting."""

import json
import os
import re
import sys
from pathlib import Path

from meeting_store import MeetingStore


PREFIX = "@@small-transcriber-event:"
_SAFE = re.compile(r"^[A-Za-z0-9_.:/-]{1,160}$")


def managed():
    return bool(os.environ.get("SMALL_TRANSCRIBER_STORAGE_ROOT"))


def emit(event, **fields):
    if managed():
        print(PREFIX + json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def stage(stage, status, message_code=None, safe_message=None, **fields):
    emit("stage_" + status, stage=stage, status="running" if status == "started" else status,
         message_code=message_code or status, safe_message=safe_message, **fields)


def classify(error):
    """Return stable kind and safe observation; never return API message/body."""
    if not isinstance(error, dict):
        return "other_gemini_error", {}
    code = error.get("code")
    if code == 503:
        return "gemini_overloaded", {}
    if code != 429:
        return "other_gemini_error", {}
    observation = {}
    kinds = set()
    details = error.get("details")
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            delay = detail.get("retryDelay")
            if isinstance(delay, str) and re.fullmatch(r"\d+(?:\.\d+)?s", delay):
                observation["retry_after_seconds"] = min(float(delay[:-1]), 3600)
        if not str(detail.get("@type", "")).endswith("QuotaFailure"):
            continue
        for violation in detail.get("violations", []):
            if not isinstance(violation, dict):
                continue
            metric = violation.get("quotaMetric", "")
            quota_id = violation.get("quotaId", "")
            tokens = (str(metric) + " " + str(quota_id)).lower().replace("_", "")
            if any(word in tokens for word in ("perday", "daily", "requestsperday")):
                kinds.add("daily_quota_exhausted")
            elif any(word in tokens for word in ("tokensperminute", "tokenperminute", "tpm")):
                kinds.add("rate_limit_tpm")
            elif any(word in tokens for word in ("requestsperminute", "requestperminute", "rpm")):
                kinds.add("rate_limit_rpm")
            if isinstance(metric, str) and _SAFE.fullmatch(metric):
                observation["quota_metric_safe"] = metric
            if isinstance(quota_id, str) and _SAFE.fullmatch(quota_id):
                observation["quota_id_safe"] = quota_id
            value = violation.get("quotaValue")
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                observation["quota_value"] = value
            elif isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value):
                observation["quota_value"] = float(value)
    if len(kinds) == 1:
        return kinds.pop(), observation
    return "rate_limit_unknown", observation


def request(stage_name, model, attempt, result, request_type="generate_content"):
    """Record once after an actual curl process, then emit a safe live snapshot."""
    if not managed():
        return "other_gemini_error", {}
    if result.returncode and not result.stdout:
        # No HTTP response evidence (for example DNS/connect failure): do not
        # present a local transport error as a request consumed by Gemini.
        emit("gemini_request_failed", stage=stage_name, model=model,
             request_type=request_type, attempt=attempt, quota_kind="other_gemini_error")
        return "other_gemini_error", {}
    try:
        data = json.loads(result.stdout) if result.stdout else {}
    except (ValueError, TypeError):
        data = {}
    error = data.get("error") if isinstance(data, dict) else None
    if request_type == "upload" and error is None and isinstance(result.stdout, str):
        statuses = re.findall(r"(?im)^HTTP/\S+\s+(\d{3})\b", result.stdout)
        if statuses and int(statuses[-1]) >= 400:
            error = {"code": int(statuses[-1])}
    kind, observation = classify(error)
    code = error.get("code") if isinstance(error, dict) else None
    outcome = {429: "http_429", 503: "http_503"}.get(code,
        "other_error" if result.returncode or error or (not data and request_type != "upload")
        else "succeeded")
    store = MeetingStore(Path(os.environ["SMALL_TRANSCRIBER_STORAGE_ROOT"]))
    snapshot = store.record_gemini_request(
        stage=stage_name, model=model, request_type=request_type, attempt=attempt,
        outcome=outcome, http_status=code, quota_kind=kind if error else None,
        meeting_id=os.environ.get("SMALL_TRANSCRIBER_MEETING_ID"),
        transcription_run_id=os.environ.get("SMALL_TRANSCRIBER_TRANSCRIPTION_RUN_ID"),
        analysis_run_id=os.environ.get("SMALL_TRANSCRIBER_ANALYSIS_RUN_ID"),
        observation=observation if error else None)
    emit("gemini_request_" + ("succeeded" if outcome == "succeeded" else "failed"),
         stage=stage_name, model=model, request_type=request_type, attempt=attempt,
         http_status=code, quota_kind=kind if error else None,
         retry_after_seconds=observation.get("retry_after_seconds"))
    emit("usage_updated", **snapshot)
    return kind, observation


def request_started(stage_name, model, attempt, max_attempts=None, request_type="generate_content"):
    emit("gemini_request_started", stage=stage_name, model=model, request_type=request_type,
         attempt=attempt, max_attempts=max_attempts)


def request_retry(stage_name, model, next_attempt, delay, quota_kind):
    emit("gemini_request_retry", stage=stage_name, model=model, request_type="generate_content",
         attempt=next_attempt, retry_after_seconds=delay, quota_kind=quota_kind)


class DailyQuotaExhausted(RuntimeError):
    pass
