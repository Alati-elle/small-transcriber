#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from gemini_telemetry import (DailyQuotaExhausted, classify, request as record_request,
                              request_started, request_retry, stage as progress_stage)
from meeting_config import load as load_config, stage_models
import tempfile
import uuid
import difflib
from pathlib import Path
from datetime import datetime

CHUNK = 360
OVERLAP = 30
STEP = CHUNK - OVERLAP
MODEL = "gemini-3.5-transcribe"
MODEL_ORDER = [MODEL]
CACHE_SCHEMA_VERSION = 1
CACHE_FORMAT_VERSION = 1
FFMPEG_AUDIO_ARGS = ["-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "64k"]

VOCABULARY_FILE = (
    Path.home()
    / ".config/gemini-transcribe/vocabulary.txt"
)

MAX_TRANSCRIBE_ATTEMPTS = 4
CURL_CONNECT_TIMEOUT = 20
CURL_MAX_TIME = 120

FFMPEG = "/usr/local/bin/ffmpeg"
FFPROBE = "/usr/local/bin/ffprobe"

API = (
    "https://generativelanguage.googleapis.com/v1beta/"
    f"models/{MODEL}:generateContent"
)

UPLOAD_API = (
    "https://generativelanguage.googleapis.com/upload/v1beta/files"
)


def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)


def run_gemini_curl(command, key, payload, **kwargs):
    request_type = kwargs.pop("request_type", "generate_content")
    attempt = kwargs.pop("attempt", 1)
    check = kwargs.pop("check", False)
    if "\n" in key or "\r" in key:
        raise ValueError("Недопустимый API key")
    read_fd, write_fd = os.pipe()
    try:
        with os.fdopen(write_fd, "wb") as header:
            header.write(f"x-goog-api-key: {key}\n".encode("utf-8"))
        request_started("transcription", MODEL, attempt, MAX_TRANSCRIBE_ATTEMPTS, request_type)
        result = subprocess.run(
            command + ["-H", f"@/dev/fd/{read_fd}", "--data-binary", "@-"],
            input=payload,
            pass_fds=(read_fd,),
            **kwargs,
        )
        record_request("transcription", MODEL, attempt, result, request_type)
        if check:
            result.check_returncode()
        return result
    finally:
        os.close(read_fd)


def log(message=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if message:
        print(f"[{timestamp}] {message}", flush=True)
    else:
        print(flush=True)


def get_key():
    p = subprocess.run(
        [
            "/usr/bin/security",
            "find-generic-password",
            "-a", os.environ.get("USER", ""),
            "-s", "Gemini Transcribe API Key",
            "-w",
        ],
        capture_output=True,
        text=True,
    )

    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError("Gemini API key не найден в Keychain")

    return p.stdout.strip()


def load_vocabulary():
    """
    Читает пользовательский словарь терминов.

    Пустые строки и комментарии с # игнорируются.
    Отсутствие словаря не является ошибкой.
    """

    if not VOCABULARY_FILE.is_file():
        return []

    result = []

    for line in VOCABULARY_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        term = line.strip()

        if not term or term.startswith("#"):
            continue

        result.append(term)

    return result


def duration(path):
    p = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(p.stdout.strip())


def reject_symlink(path):
    if path.is_symlink():
        raise RuntimeError(f"Managed artifact is a symlink: {path}")


def split_audio(source, outdir, managed=False, expected_source_sha=None):
    total = duration(source)

    log(f"Длительность: {total:.1f} сек.")
    log(f"Нарезка: {CHUNK // 60:02d}:{CHUNK % 60:02d}, overlap 00:{OVERLAP:02d}")

    chunks = []
    start = 0
    n = 1

    while start < total:
        length = min(CHUNK, total - start)

        out = outdir / (
            f"{source.stem}_chunk{CHUNK}_ov{OVERLAP}_{n:03d}.m4a"
        )
        if managed:
            reject_symlink(out)

        if out.exists() and out.stat().st_size > 0:
            log(f"{n:03d}: уже существует — использую")
        else:
            log(
                f"{n:03d}: "
                f"{start:.0f}s → {start + length:.0f}s"
            )

            staged = None
            if managed:
                staged = tempfile.NamedTemporaryFile(
                    dir=outdir, prefix=".chunk-", suffix=".m4a", delete=False
                )
                staged.close()
            try:
                run([
                    FFMPEG,
                    "-y",
                    "-hide_banner",
                    "-loglevel", "error",
                    "-ss", str(start),
                    "-i", str(source),
                    "-t", str(length),
                    *FFMPEG_AUDIO_ARGS,
                    str(staged.name) if managed else str(out),
                ])
                if managed:
                    check_source_hash(source, expected_source_sha)
                    os.replace(staged.name, out)
            finally:
                if staged is not None:
                    Path(staged.name).unlink(missing_ok=True)

        chunks.append((out, start))

        start += STEP
        n += 1

    return chunks


def upload_audio(path, key):
    size = path.stat().st_size

    headers = run_gemini_curl(
        [
            "curl", "-4", "-sS",
            "--connect-timeout", str(CURL_CONNECT_TIMEOUT),
            "--max-time", str(CURL_MAX_TIME),
            "-D", "-",
            "-o", "/dev/null",
            UPLOAD_API,
            "-H", "X-Goog-Upload-Protocol: resumable",
            "-H", "X-Goog-Upload-Command: start",
            "-H", f"X-Goog-Upload-Header-Content-Length: {size}",
            "-H", "X-Goog-Upload-Header-Content-Type: audio/m4a",
            "-H", "Content-Type: application/json",
        ],
        key,
        '{"file":{"display_name":"audio"}}',
        capture_output=True,
        text=True,
        check=True,
        request_type="upload",
    ).stdout

    m = re.search(
        r"(?im)^x-goog-upload-url:\s*(.+?)\s*$",
        headers
    )

    if not m:
        raise RuntimeError("Gemini не вернул upload URL")

    upload_url = m.group(1).strip()

    request_started("transcription", MODEL, 1, request_type="upload")
    p = subprocess.run(
        [
            "curl", "-4", "-sS",
            "--connect-timeout", str(CURL_CONNECT_TIMEOUT),
            "--max-time", str(CURL_MAX_TIME),
            upload_url,
            "-H", f"Content-Length: {size}",
            "-H", "X-Goog-Upload-Offset: 0",
            "-H", "X-Goog-Upload-Command: upload, finalize",
            "--data-binary", f"@{path}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    record_request("transcription", MODEL, 1, p, "upload")
    p.check_returncode()

    data = json.loads(p.stdout)

    try:
        return data["file"]["uri"]
    except Exception:
        raise RuntimeError(
            "Ошибка загрузки Gemini:\n" +
            json.dumps(data, ensure_ascii=False, indent=2)
        )


def transcription_payload(uri, vocabulary):
    parts = [{
        "fileData": {
            "fileUri": uri,
            "mimeType": "audio/m4a",
        }
    }]

    if vocabulary:
        vocabulary_text = ", ".join(vocabulary)

        parts.append({
            "text": (
                "Контекст для распознавания речи. "
                "Ниже перечислены термины и имена, которые МОГУТ "
                "встречаться в записи. Используй их только если "
                "они действительно слышны. Не добавляй отсутствующие "
                "в аудио слова и не исправляй смысл речи. "
                "Возможные термины: "
                + vocabulary_text
            )
        })

    payload = {
        "contents": [{
            "parts": parts
        }],
        "generationConfig": {
            "audioTranscriptionConfig": {
                "languageCodes": ["ru-RU"],
                "diarization": True,
                "wordTimestamp": True,
                "mode": "VERBATIM",
            }
        },
    }

    return payload


def transcribe(uri, key, vocabulary=None):
    global MODEL, API
    if vocabulary is None:
        vocabulary = load_vocabulary()
    payload = transcription_payload(uri, vocabulary)

    attempt = 1
    model_index = MODEL_ORDER.index(MODEL)

    while True:
        log(f"  Gemini: попытка {attempt}")

        p = run_gemini_curl(
            [
                "curl", "-4", "-sS",
            "--connect-timeout", str(CURL_CONNECT_TIMEOUT),
            "--max-time", str(CURL_MAX_TIME),
                "-X", "POST",
                API,
                "-H", "Content-Type: application/json",
            ],
            key,
            json.dumps(payload),
            capture_output=True,
            text=True,
            attempt=attempt,
        )

        try:
            data = json.loads(p.stdout)
        except Exception:
            raise RuntimeError(
                "Gemini вернул некорректный JSON:\n" +
                p.stdout[:2000]
            )

        error = data.get("error")

        if not error:
            return data

        if error.get("code") == 503 and model_index + 1 < len(MODEL_ORDER):
            model_index += 1
            MODEL = MODEL_ORDER[model_index]
            API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
            attempt = 1
            continue

        if error.get("code") != 429:
            raise RuntimeError(
                json.dumps(error, ensure_ascii=False, indent=2)
            )

        quota_kind, observation = classify(error)
        if quota_kind == "daily_quota_exhausted":
            if model_index + 1 < len(MODEL_ORDER):
                model_index += 1
                MODEL = MODEL_ORDER[model_index]
                API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
                attempt = 1
                continue
            raise DailyQuotaExhausted("Gemini daily quota exhausted")

        if attempt >= MAX_TRANSCRIBE_ATTEMPTS:
            if model_index + 1 < len(MODEL_ORDER):
                model_index += 1
                MODEL = MODEL_ORDER[model_index]
                API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
                attempt = 1
                continue
            raise RuntimeError(
                "Gemini rate limit: "
                f"chunk не распознан после "
                f"{MAX_TRANSCRIBE_ATTEMPTS} попыток. "
                "Уже готовые chunks сохранены; "
                "повторите запуск позднее."
            )

        delay = min(observation.get("retry_after_seconds", 60) + 5, 65)

        log(
            f"  Rate limit — жду {delay} сек. "
            f"(попытка {attempt}/"
            f"{MAX_TRANSCRIBE_ATTEMPTS})"
        )

        request_retry("transcription", MODEL, attempt + 1, delay, quota_kind)
        time.sleep(delay)
        attempt += 1


def sec(v):
    return float(str(v).rstrip("s"))


def extract_parts(data, chunk_number, chunk_start):
    result = []

    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )

    for part in parts:
        at = part.get("audioTranscription")

        if not isinstance(at, dict):
            continue

        words = at.get("words") or []
        text = (at.get("text") or part.get("text") or "").strip()

        if not words or not text:
            continue

        try:
            start = sec(words[0]["startOffset"])
            end = sec(words[-1]["endOffset"])
        except Exception:
            continue

        # Gemini изредка возвращает повреждённые offsets.
        # Такие реплики нельзя пропускать в итоговую шкалу времени.
        if start < 0 or end <= start:
            log(
                "  ПРЕДУПРЕЖДЕНИЕ: пропущена реплика с "
                f"некорректным временем {start:.3f}–{end:.3f}"
            )
            continue

        result.append({
            "chunk": chunk_number,
            "chunk_start": chunk_start,
            "local_start": start,
            "local_end": end,
            "start": chunk_start + start,
            "end": chunk_start + end,
            "speaker": at.get("speakerLabel", "spk:?"),
            "text": text,
            "words": words,
        })

    return result


def normalize_words(text):
    return re.findall(
        r"[0-9A-Za-zА-Яа-яЁё]+",
        text.lower()
    )


def similarity(a, b):
    a = normalize_words(a)
    b = normalize_words(b)

    if not a or not b:
        return 0.0

    return difflib.SequenceMatcher(None, a, b).ratio()


def map_speakers(previous, current):
    """
    Сопоставляем local speaker IDs соседних chunks по общей
    области overlap.

    Решение строится не только по одному максимальному
    совпадению: для каждой пары speaker IDs агрегируем
    несколько совпавших реплик.

    Возвращаем:
        mapping:
            current local speaker -> previous local speaker

        evidence:
            диагностические данные по принятым соответствиям
    """

    if not previous or not current:
        return {}, []

    boundary = current[0]["chunk_start"]

    prev_overlap = [
        x for x in previous
        if x["end"] >= boundary - 3
        and x["start"] <= boundary + OVERLAP + 5
    ]

    curr_overlap = [
        x for x in current
        if x["local_start"] <= OVERLAP + 5
    ]

    pair_scores = {}

    for a in prev_overlap:
        a_text = (a.get("text") or "").strip()

        if len(a_text) < 3:
            continue

        for b in curr_overlap:
            b_text = (b.get("text") or "").strip()

            if len(b_text) < 3:
                continue

            score = similarity(a_text, b_text)

            if score < 0.30:
                continue

            key = (
                b["speaker"],
                a["speaker"],
            )

            pair_scores.setdefault(key, []).append({
                "score": score,
                "previous_text": a_text,
                "current_text": b_text,
                "previous_start": a["start"],
                "current_start": b["start"],
            })

    candidates = []

    for (
        current_speaker,
        previous_speaker,
    ), matches in pair_scores.items():

        matches = sorted(
            matches,
            key=lambda x: x["score"],
            reverse=True,
        )

        scores = [
            x["score"]
            for x in matches
        ]

        best = scores[0]

        strong = [
            x for x in scores
            if x >= 0.55
        ]

        medium = [
            x for x in scores
            if x >= 0.40
        ]

        # Максимум три лучших свидетельства.
        # Так множество слабых случайных совпадений
        # не сможет искусственно накопить высокий score.
        top_scores = scores[:3]
        aggregate = sum(top_scores)

        accepted = False
        reason = None

        # Практически идентичная реплика.
        if best >= 0.88:
            accepted = True
            reason = "very_strong_single_match"

        # Несколько сильных совпадений.
        elif len(strong) >= 2 and aggregate >= 1.15:
            accepted = True
            reason = "multiple_strong_matches"

        # Несколько независимых средних совпадений.
        elif len(medium) >= 3 and aggregate >= 1.45:
            accepted = True
            reason = "multiple_medium_matches"

        if not accepted:
            continue

        # Используется для разрешения конкуренции между
        # несколькими возможными парами.
        rank = (
            len(strong),
            len(medium),
            aggregate,
            best,
        )

        candidates.append({
            "current_speaker": current_speaker,
            "previous_speaker": previous_speaker,
            "best_score": best,
            "aggregate_score": aggregate,
            "strong_matches": len(strong),
            "medium_matches": len(medium),
            "reason": reason,
            "rank": rank,
            "examples": matches[:3],
        })

    candidates.sort(
        key=lambda x: x["rank"],
        reverse=True,
    )

    mapping = {}
    evidence = []
    used_previous = set()

    # One-to-one:
    # один local speaker текущего chunk не может
    # соответствовать двум speaker предыдущего chunk,
    # и наоборот.
    for candidate in candidates:
        current_speaker = candidate["current_speaker"]
        previous_speaker = candidate["previous_speaker"]

        if current_speaker in mapping:
            continue

        if previous_speaker in used_previous:
            continue

        mapping[current_speaker] = previous_speaker
        used_previous.add(previous_speaker)

        evidence.append({
            "current_speaker": current_speaker,
            "previous_speaker": previous_speaker,
            "best_score": round(
                candidate["best_score"],
                4,
            ),
            "aggregate_score": round(
                candidate["aggregate_score"],
                4,
            ),
            "strong_matches": candidate["strong_matches"],
            "medium_matches": candidate["medium_matches"],
            "reason": candidate["reason"],
            "examples": [
                {
                    "score": round(
                        example["score"],
                        4,
                    ),
                    "previous_start": example[
                        "previous_start"
                    ],
                    "current_start": example[
                        "current_start"
                    ],
                    "previous_text": example[
                        "previous_text"
                    ][:250],
                    "current_text": example[
                        "current_text"
                    ][:250],
                }
                for example in candidate["examples"]
            ],
        })

    return mapping, evidence


def find_cut(previous, current):
    """
    Ищем наиболее похожие реплики в overlap и ставим границу
    между их временными позициями.

    Если уверенного совпадения нет, ничего не отрезаем:
    начинаем с начала текущего chunk. Возможные overlap-дубли
    затем удалит отдельный дедупликатор. Это безопаснее потери речи.
    """

    chunk_start = current[0]["chunk_start"]
    fallback = chunk_start

    prev_overlap = [
        x for x in previous
        if x["end"] >= chunk_start - 3
    ]

    curr_overlap = [
        x for x in current
        if x["local_start"] <= OVERLAP + 3
    ]

    best = None

    for a in prev_overlap:
        for b in curr_overlap:
            score = similarity(a["text"], b["text"])

            if best is None or score > best[0]:
                best = (score, a, b)

    # Для P0 действуем консервативно:
    # cut разрешён только при практически уверенном совпадении.
    # При сомнении ничего не отрезаем — небольшой overlap-дубль
    # безопаснее потенциальной потери речи.
    if best is None or best[0] < 0.94:
        return fallback, None

    score, a, b = best

    # Практически одна и та же реплика существует в обоих chunks.
    # Оставляем вариант из предыдущего chunk и начинаем
    # следующий после её глобального конца.
    cut = max(
        chunk_start,
        min(chunk_start + OVERLAP + 3, a["end"])
    )

    return cut, score


def fmt(seconds):
    seconds = max(0, int(round(seconds)))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def merge(chunk_parts, overlap):
    if not chunk_parts:
        return [], []

    merged = list(chunk_parts[0])
    diagnostics = []

    # Глобальные speaker IDs начинаем с первого chunk.
    speaker_global = {}
    next_speaker = 0

    for x in merged:
        sp = x["speaker"]
        if sp not in speaker_global:
            speaker_global[sp] = f"Спикер {next_speaker}"
            next_speaker += 1
        x["global_speaker"] = speaker_global[sp]

    previous_original = chunk_parts[0]

    for index in range(1, len(chunk_parts)):
        current = chunk_parts[index]

        if not current:
            continue

        mapping, mapping_evidence = map_speakers(
            previous_original,
            current,
        )

        # Переносим найденное соответствие на уже глобальные имена.
        previous_labels = {}

        for p in previous_original:
            if "global_speaker" in p:
                previous_labels[p["speaker"]] = p["global_speaker"]

        # Внутри одного chunk один local speaker должен всегда
        # получать один и тот же global speaker ID.
        current_labels = {}

        for x in current:
            local = x["speaker"]

            if local in current_labels:
                x["global_speaker"] = current_labels[local]
            elif local in mapping and mapping[local] in previous_labels:
                global_label = previous_labels[mapping[local]]
                current_labels[local] = global_label
                x["global_speaker"] = global_label
            else:
                global_label = f"Спикер {next_speaker}"
                next_speaker += 1
                current_labels[local] = global_label
                x["global_speaker"] = global_label

        boundary = current[0]["chunk_start"]

        keep, merge_v21 = merge_v21_verified(
            previous_original,
            current,
            merged,
            boundary,
            overlap,
        )

        diagnostics.append({
            "chunk": index + 1,
            "cut": merge_v21.get("cut"),
            "match_score": None,
            "merge_v21": merge_v21,
            "speaker_mapping": mapping,
            "speaker_mapping_evidence": mapping_evidence,
            "speaker_mapping_confidence": (
                "matched"
                if mapping
                else "unresolved"
            ),
        })

        merged.extend(keep)

        previous_original = current

    merged.sort(key=lambda x: (x["start"], x["end"]))
    merged, removed_duplicates = deduplicate_merged(merged)

    if removed_duplicates:
        diagnostics.append({
            "deduplication": {
                "removed": removed_duplicates,
            }
        })

    return merged, diagnostics



# MERGE V2.1 — значения из проверенного merge_v2_prototype.py
MERGE_V21_TIME_TOL = 0.35
MERGE_V21_WINDOW = 12
MERGE_V21_MIN_MATCHES = 8
MERGE_V21_MIN_RATIO = 0.67
MERGE_V21_MAX_OLD_FRAGMENT_WORDS = 3
MERGE_V21_MIN_NEW_EXTRA_WORDS = 2

def norm(text):
    return "".join(
        re.findall(
            r"[0-9A-Za-zА-Яа-яЁё]+",
            str(text).lower(),
        )
    )


def _merge_v21_prepare_rows(rows):
    """
    Adapter production row -> проверенный формат merge_v2_prototype.

    Исходные production rows НЕ изменяются.
    """
    result = []

    for row in rows:
        prepared = dict(row)
        prepared_words = []

        chunk_start = float(row["chunk_start"])

        for word_index, w in enumerate(row.get("words") or []):
            try:
                start = (
                    chunk_start
                    + sec(w["startOffset"])
                )
                end = (
                    chunk_start
                    + sec(w["endOffset"])
                )
            except Exception:
                continue

            token = (
                w.get("word")
                or w.get("text")
                or w.get("token")
                or ""
            )

            if not token:
                continue

            prepared_words.append({
                "text": token,
                "norm": norm(token),
                "start": start,
                "end": end,
                "mid": (start + end) / 2,
                "word_index": word_index,
                "_raw_word": w,
            })

        prepared["words"] = prepared_words

        # part_index отсутствует в production extract_parts.
        # Для однозначной идентификации используем стабильный
        # индекс исходной реплики внутри chunk.
        prepared["_merge_row_id"] = (
            row.get("chunk"),
            row.get("start"),
            row.get("end"),
            row.get("speaker"),
            row.get("text"),
        )

        result.append(prepared)

    result.sort(
        key=lambda x: (x["start"], x["end"])
    )

    return result


def _merge_v21_restore_row(row):
    """
    Возвращаем prepared row обратно в production-формат.
    """
    restored = dict(row)

    prepared_words = restored.get("words") or []

    restored["words"] = [
        w["_raw_word"]
        for w in prepared_words
        if "_raw_word" in w
    ]

    restored.pop("_merge_row_id", None)

    if prepared_words:
        restored["start"] = prepared_words[0]["start"]
        restored["end"] = prepared_words[-1]["end"]

        chunk_start = float(restored["chunk_start"])

        restored["local_start"] = (
            restored["start"] - chunk_start
        )
        restored["local_end"] = (
            restored["end"] - chunk_start
        )

    return restored


def norm_words(words):
    return [
        norm(w["text"])
        for w in words
        if norm(w["text"])
    ]

def flatten_words(rows, start=None, end=None):
    result = []

    for row_index, row in enumerate(rows):
        for w in row["words"]:
            if (
                start is not None
                and w["mid"] < start
            ):
                continue

            if (
                end is not None
                and w["mid"] > end
            ):
                continue

            item = dict(w)
            item["row_index"] = row_index
            result.append(item)

    result.sort(
        key=lambda x: (x["start"], x["end"])
    )

    return result

def pair_by_time(old_words, new_words):
    used = set()
    pairs = []

    for old in old_words:
        candidates = []

        for ni, new in enumerate(new_words):
            if ni in used:
                continue

            delta = abs(
                old["mid"] - new["mid"]
            )

            if delta > MERGE_V21_TIME_TOL:
                continue

            same = (
                bool(old["norm"])
                and old["norm"] == new["norm"]
            )

            candidates.append(
                (
                    0 if same else 1,
                    delta,
                    ni,
                    new,
                    same,
                )
            )

        if not candidates:
            pairs.append({
                "old": old,
                "new": None,
                "same": False,
            })
            continue

        candidates.sort(
            key=lambda x: (x[0], x[1])
        )

        _, _, ni, new, same = candidates[0]

        used.add(ni)

        pairs.append({
            "old": old,
            "new": new,
            "same": same,
        })

    return pairs

def find_stable_cut(pairs):
    if len(pairs) < MERGE_V21_WINDOW:
        return None

    best = None

    for start in range(
        len(pairs) - MERGE_V21_WINDOW + 1
    ):
        window = pairs[
            start:start + MERGE_V21_WINDOW
        ]

        exact = [
            p
            for p in window
            if (
                p["new"] is not None
                and p["same"]
            )
        ]

        ratio = len(exact) / MERGE_V21_WINDOW

        if (
            len(exact) < MERGE_V21_MIN_MATCHES
            or ratio < MERGE_V21_MIN_RATIO
        ):
            continue

        positions = [
            j
            for j, p in enumerate(window)
            if (
                p["new"] is not None
                and p["same"]
            )
        ]

        span = (
            positions[-1]
            - positions[0]
            + 1
        )

        if span < MERGE_V21_MIN_MATCHES:
            continue

        last = max(
            exact,
            key=lambda p: p["new"]["end"],
        )

        candidate = {
            "cut": last["new"]["end"],
            "matches": len(exact),
            "ratio": ratio,
            "window_start":
                window[0]["old"]["start"],
            "window_end":
                window[-1]["old"]["end"],
            "last_word":
                last["new"]["text"],
        }

        if (
            best is None
            or candidate["cut"] > best["cut"]
        ):
            best = candidate

    return best

def find_fragment_replacement(
    previous_rows,
    current_rows,
    cut,
):
    """
    Ищем очень консервативный случай:

      OLD: Ну
      NEW: Ну, у нас есть формирование BRD.

    Требования:
    - OLD <= 3 слов;
    - OLD пересекает область cut;
    - NEW начинается почти одновременно с OLD;
    - все нормализованные OLD-слова являются
      точным префиксом NEW;
    - NEW длиннее минимум на 2 слова.
    """

    candidates = []

    for oi, old in enumerate(previous_rows):
        old_words = norm_words(old["words"])

        if not old_words:
            continue

        if (
            len(old_words)
            > MERGE_V21_MAX_OLD_FRAGMENT_WORDS
        ):
            continue

        # Короткий OLD должен находиться
        # непосредственно у найденной границы.
        if not (
            old["start"] <= cut + MERGE_V21_TIME_TOL
            and old["end"] >= cut - 2.0
        ):
            continue

        for ni, new in enumerate(current_rows):
            new_words = norm_words(new["words"])

            if (
                len(new_words)
                < len(old_words)
                + MERGE_V21_MIN_NEW_EXTRA_WORDS
            ):
                continue

            start_delta = abs(
                old["start"] - new["start"]
            )

            if start_delta > MERGE_V21_TIME_TOL:
                continue

            if (
                new_words[:len(old_words)]
                != old_words
            ):
                continue

            candidates.append({
                "old_index": oi,
                "new_index": ni,
                "old": old,
                "new": new,
                "start_delta": start_delta,
            })

    if not candidates:
        return None

    # Самый близкий старт; при равенстве —
    # наиболее полная NEW-реплика.
    candidates.sort(
        key=lambda x: (
            x["start_delta"],
            -len(x["new"]["words"]),
        )
    )

    return candidates[0]

def words_to_text(words):
    return " ".join(
        w["text"] for w in words
    ).strip()

def trim_current_rows(rows, cut):
    result = []
    removed_words = 0
    trimmed_rows = 0
    first_kept = None

    for row in rows:
        if row["end"] <= cut + 1e-6:
            removed_words += len(
                row["words"]
            )
            continue

        if row["start"] >= cut - 1e-6:
            result.append(dict(row))

            if (
                first_kept is None
                and row["words"]
            ):
                first_kept = row["words"][0]

            continue

        kept_words = [
            w
            for w in row["words"]
            if w["start"] >= cut - 1e-6
        ]

        removed_words += (
            len(row["words"])
            - len(kept_words)
        )

        if not kept_words:
            continue

        trimmed_rows += 1

        new_row = dict(row)
        new_row["words"] = kept_words
        new_row["start"] = (
            kept_words[0]["start"]
        )
        new_row["end"] = (
            kept_words[-1]["end"]
        )
        new_row["text"] = (
            words_to_text(kept_words)
        )

        result.append(new_row)

        if first_kept is None:
            first_kept = kept_words[0]

    return (
        result,
        removed_words,
        trimmed_rows,
        first_kept,
    )


def merge_v21_verified(
    previous_original,
    current,
    merged,
    boundary,
    overlap,
):
    previous = _merge_v21_prepare_rows(
        previous_original
    )
    current_prepared = _merge_v21_prepare_rows(
        current
    )
    merged_prepared = _merge_v21_prepare_rows(
        merged
    )

    old_words = flatten_words(
        previous,
        boundary - 5,
        boundary + overlap + 5,
    )

    new_words = flatten_words(
        current_prepared,
        boundary - 5,
        boundary + overlap + 5,
    )

    pairs = pair_by_time(
        old_words,
        new_words,
    )

    stable = find_stable_cut(pairs)

    if stable is None:
        # В проверенном prototype при отсутствии безопасного cut
        # сохраняется CURRENT целиком.
        return (
            list(current),
            {
                "mode": "keep_all_no_safe_cut",
                "cut": None,
            },
        )

    replacement = find_fragment_replacement(
        previous,
        current_prepared,
        stable["cut"],
    )

    if replacement is not None:
        old = replacement["old"]
        new = replacement["new"]

        old_id = old["_merge_row_id"]
        new_id = new["_merge_row_id"]

        removed_old = False

        for j in range(
            len(merged_prepared) - 1,
            -1,
            -1,
        ):
            if (
                merged_prepared[j]["_merge_row_id"]
                == old_id
            ):
                del merged_prepared[j]
                removed_old = True
                break

        if not removed_old:
            raise RuntimeError(
                "MERGE V2.1: OLD fragment "
                "не найден в merged"
            )

        # Синхронизируем удаление обратно в настоящий merged.
        for j in range(
            len(merged) - 1,
            -1,
            -1,
        ):
            candidate_id = (
                merged[j].get("chunk"),
                merged[j].get("start"),
                merged[j].get("end"),
                merged[j].get("speaker"),
                merged[j].get("text"),
            )

            if candidate_id == old_id:
                del merged[j]
                break

        kept_prepared = []

        for row in current_prepared:
            if row["_merge_row_id"] == new_id:
                kept_prepared.append(dict(row))
                continue

            if row["end"] <= stable["cut"]:
                continue

            if row["start"] >= stable["cut"]:
                kept_prepared.append(dict(row))
                continue

            trimmed, _, _, _ = trim_current_rows(
                [row],
                stable["cut"],
            )
            kept_prepared.extend(trimmed)

        kept = [
            _merge_v21_restore_row(row)
            for row in kept_prepared
        ]

        return (
            kept,
            {
                "mode": "short_fragment_replacement",
                "cut": stable["cut"],
                "matches": stable["matches"],
                "window": MERGE_V21_WINDOW,
                "ratio": stable["ratio"],
                "old_fragment": old["text"],
                "new_full": new["text"],
            },
        )

    kept_prepared, removed, trimmed, first_kept = (
        trim_current_rows(
            current_prepared,
            stable["cut"],
        )
    )

    kept = [
        _merge_v21_restore_row(row)
        for row in kept_prepared
    ]

    return (
        kept,
        {
            "mode": "word_level_cut",
            "cut": stable["cut"],
            "matches": stable["matches"],
            "window": MERGE_V21_WINDOW,
            "ratio": stable["ratio"],
            "removed_words": removed,
            "trimmed_rows": trimmed,
        },
    )


def deduplicate_merged(rows):
    """Удаляем только очень уверенные overlap-дубли."""
    result = []
    removed = 0

    for row in rows:
        duplicate = False

        # Дубликат от overlap обычно находится совсем рядом.
        for previous in reversed(result[-4:]):
            if abs(row["start"] - previous["start"]) > OVERLAP + 5:
                continue

            score = similarity(row["text"], previous["text"])

            same_text = (
                " ".join(normalize_words(row["text"]))
                == " ".join(normalize_words(previous["text"]))
            )

            if same_text or score >= 0.94:
                duplicate = True
                removed += 1
                break

        if not duplicate:
            result.append(row)

    return result, removed


def assess_quality(rows, total_duration, diagnostics=None):
    """
    Контроль полноты итоговой расшифровки.

    Важно:
    обычная пауза в разговоре сама по себе не является ошибкой.
    Отдельно контролируем стыки chunks, где риск технической
    потери текста выше всего.
    """
    severe = []
    warnings = []
    boundary_warnings = []

    if not rows:
        severe.append("Gemini не вернул ни одной валидной реплики")
        return {
            "ok": False,
            "severe": severe,
            "warnings": warnings,
            "boundary_warnings": boundary_warnings,
        }

    # Общие временные провалы.
    # > 3 минут считаем серьёзным подозрением.
    # > 90 секунд показываем как предупреждение.
    for previous, current in zip(rows, rows[1:]):
        gap = current["start"] - previous["end"]

        if gap > 180:
            severe.append(
                "подозрительный провал речи "
                f"{fmt(previous['end'])}–{fmt(current['start'])} "
                f"({int(round(gap))} сек.)"
            )
        elif gap > 90:
            warnings.append(
                "длинный промежуток без распознанной речи "
                f"{fmt(previous['end'])}–{fmt(current['start'])} "
                f"({int(round(gap))} сек.)"
            )

    if rows[0]["start"] > 180:
        warnings.append(
            f"первая реплика начинается только в {fmt(rows[0]['start'])}"
        )

    tail_gap = total_duration - rows[-1]["end"]

    if tail_gap > 180:
        warnings.append(
            "последняя реплика заканчивается за "
            f"{int(round(tail_gap))} сек. до конца аудио"
        )

    # Контроль каждого стыка chunks.
    for item in diagnostics or []:
        if not isinstance(item, dict):
            continue

        chunk = item.get("chunk")
        if chunk is None:
            continue

        score = item.get("match_score")

        if score is None:
            boundary_warnings.append(
                f"стык перед chunk {chunk}: "
                "не найдено текстового совпадения в overlap"
            )
        elif score < 0.35:
            boundary_warnings.append(
                f"стык перед chunk {chunk}: "
                f"слабое совпадение overlap ({score:.3f})"
            )

    warnings.extend(boundary_warnings)

    return {
        "ok": not severe,
        "severe": severe,
        "warnings": warnings,
        "boundary_warnings": boundary_warnings,
    }


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Расшифровка аудио; --output-dir включает managed mode."
    )
    parser.add_argument("audio", help="исходный аудиофайл")
    parser.add_argument("--output-dir", type=Path, help="каталог результата (managed mode)")
    parser.add_argument("--cache-dir", type=Path, help="каталог chunks/cache (managed mode)")
    parser.add_argument(
        "--expected-source-sha256", help="ожидаемый SHA-256 исходного файла (managed mode)"
    )
    args = parser.parse_args(argv)
    managed_options = (args.output_dir, args.cache_dir, args.expected_source_sha256)
    if any(value is not None for value in managed_options) and not all(
        value is not None for value in managed_options
    ):
        parser.error("managed mode требует --output-dir, --cache-dir и --expected-source-sha256")
    if args.expected_source_sha256 is not None and not re.fullmatch(
        r"[0-9a-fA-F]{64}", args.expected_source_sha256
    ):
        parser.error("--expected-source-sha256 должен быть 64-символьным hex SHA-256")
    return args


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_source_hash(source, expected):
    if file_sha256(source) != expected.lower():
        raise RuntimeError("SHA-256 исходного файла не совпадает; managed run остановлен")


def json_sha256(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_json_atomic(path, data):
    staged = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".cache-", delete=False
    )
    try:
        with staged:
            json.dump(data, staged, ensure_ascii=False, sort_keys=True, indent=2)
            staged.flush()
            os.fsync(staged.fileno())
        os.replace(staged.name, path)
    finally:
        Path(staged.name).unlink(missing_ok=True)


def cache_identity(source, expected_sha, vocabulary):
    identity = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "cache_format_version": CACHE_FORMAT_VERSION,
        "source_sha256": expected_sha.lower(),
        "source_size_bytes": source.stat().st_size,
        "backend": "gemini-generateContent",
        "model": MODEL,
        "request_api": API,
        "upload_api": UPLOAD_API,
        "chunk_seconds": CHUNK,
        "overlap_seconds": OVERLAP,
        "step_seconds": STEP,
        "audio_preprocessing": {
            "ffmpeg_sha256": file_sha256(Path(FFMPEG)),
            "ffprobe_sha256": file_sha256(Path(FFPROBE)),
            "seek_before_input": True,
            "audio_args": FFMPEG_AUDIO_ARGS,
            "output_suffix": ".m4a",
        },
        "request_sha256": json_sha256(transcription_payload("<file-uri>", vocabulary)),
        "vocabulary_sha256": json_sha256(vocabulary),
    }
    if len(MODEL_ORDER) > 1:
        identity["model_order"] = MODEL_ORDER
    return identity


def configure_managed_models(root):
    global MODEL, MODEL_ORDER, API
    config, _ = load_config(root)
    MODEL_ORDER = stage_models(config, "transcription")
    MODEL = MODEL_ORDER[0]
    API = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"


def prepare_managed_cache(cache_dir, identity):
    manifest_path = cache_dir / "manifest.json"
    generations = cache_dir / "generations"
    reject_symlink(manifest_path)
    reject_symlink(generations)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = None

    if isinstance(manifest, dict):
        generation = manifest.get("generation")
        if isinstance(generation, str) and re.fullmatch(r"[0-9a-f]{32}", generation):
            if manifest == {**identity, "generation": generation}:
                path = generations / generation
                reject_symlink(path)
                if path.is_dir():
                    return path

    generations.mkdir(exist_ok=True)
    generation = uuid.uuid4().hex
    path = generations / generation
    path.mkdir()
    write_json_atomic(manifest_path, {**identity, "generation": generation})
    return path


def main():
    args = parse_args(sys.argv[1:])
    source = Path(args.audio).expanduser().resolve()

    if not source.is_file():
        raise RuntimeError(f"Файл не найден: {source}")

    managed = args.output_dir is not None
    if managed and os.environ.get("SMALL_TRANSCRIBER_STORAGE_ROOT"):
        configure_managed_models(os.environ["SMALL_TRANSCRIBER_STORAGE_ROOT"])
    if managed:
        check_source_hash(source, args.expected_source_sha256)

    if not Path(FFMPEG).exists() or not Path(FFPROBE).exists():
        raise RuntimeError(
            "Не найден ffmpeg/ffprobe в /usr/local/bin"
        )

    key = get_key()

    outdir = args.output_dir.expanduser().resolve() if managed else source.parent / source.stem
    outdir.mkdir(parents=managed, exist_ok=True)

    # Все промежуточные артефакты складываем отдельно,
    # чтобы в основной папке оставались только результаты.
    service_dir = outdir / "_service"
    if managed:
        reject_symlink(service_dir)
    service_dir.mkdir(exist_ok=True)
    cache_root = args.cache_dir.expanduser().resolve() if managed else service_dir
    cache_dir = cache_root
    if managed:
        cache_dir.mkdir(parents=True, exist_ok=True)
        vocabulary = load_vocabulary()
        cache_dir = prepare_managed_cache(
            cache_dir, cache_identity(source, args.expected_source_sha256, vocabulary)
        )

    log("=" * 60)
    log("GEMINI TRANSCRIBE")
    progress_stage("transcription", "progress", "preparing_audio", "Подготовка аудио…")
    log(f"Исходник: {source}")
    log(f"Результат: {outdir}")
    log("=" * 60)

    chunks = split_audio(
        source, cache_dir, managed=managed,
        expected_source_sha=args.expected_source_sha256 if managed else None,
    )

    chunk_parts = []

    for number, (audio, chunk_start) in enumerate(chunks, 1):
        progress_stage("transcription", "progress", "transcription_chunk",
                       f"Фрагмент {number} из {len(chunks)}", chunk=number, chunks=len(chunks))
        json_path = audio.with_name(
            audio.stem + "_gemini.json"
        )

        txt_path = audio.with_name(
            audio.stem + "_transcript.txt"
        )
        if managed:
            reject_symlink(json_path)
            reject_symlink(txt_path)

        log()
        log(f"=== Часть {number}/{len(chunks)} ===")

        data = None

        if json_path.exists() and json_path.stat().st_size:
            try:
                with json_path.open(encoding="utf-8") as f:
                    candidate = json.load(f)

                if (
                    "error" not in candidate
                    and candidate.get("candidates")
                ):
                    data = candidate
                    log("JSON уже существует — Gemini пропускаю")
            except Exception:
                pass

        if data is None:
            log("Загрузка в Gemini...")
            uri = upload_audio(audio, key)

            log("Распознавание...")
            data = transcribe(uri, key, vocabulary) if managed else transcribe(uri, key)

            if managed:
                write_json_atomic(json_path, data)
            else:
                with json_path.open("w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

        parts = extract_parts(
            data,
            number,
            chunk_start,
        )

        with txt_path.open("w", encoding="utf-8") as f:
            for x in parts:
                f.write(
                    f"[{fmt(x['local_start'])}–"
                    f"{fmt(x['local_end'])}] "
                    f"{x['speaker']}: {x['text']}\n\n"
                )

        tokens = (
            data.get("usageMetadata", {})
            .get("promptTokenCount")
        )

        log(
            f"Реплик: {len(parts)}, "
            f"input tokens: {tokens}"
        )

        chunk_parts.append(parts)

    log()
    log("Склеиваю части...")
    progress_stage("transcription", "progress", "merging_transcript", "Объединение расшифровки…")

    merged, diagnostics = merge(chunk_parts, OVERLAP)
    total_duration = duration(source)
    quality = assess_quality(
        merged,
        total_duration,
        diagnostics,
    )

    log()
    log("Проверка качества расшифровки:")

    if quality["ok"]:
        log("  OK: критических провалов не обнаружено")
    else:
        for issue in quality["severe"]:
            log("  КРИТИЧНО: " + issue)

    for warning in quality["warnings"]:
        log("  ПРЕДУПРЕЖДЕНИЕ: " + warning)

    final_txt = outdir / (
        source.stem + "_ПОЛНАЯ_РАСШИФРОВКА.txt"
    )

    staged_txt = None
    if managed:
        staged_txt = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=outdir, prefix=".transcript-", delete=False
        )
        staged_txt.close()
    try:
        target = Path(staged_txt.name) if managed else final_txt
        with target.open("w", encoding="utf-8") as f:
            f.write(source.stem + "\n")
            f.write("=" * len(source.stem) + "\n\n")

            for x in merged:
                f.write(
                    f"[{fmt(x['start'])}–{fmt(x['end'])}] "
                    f"{x['global_speaker']}:\n"
                    f"{x['text']}\n\n"
                )

        diag = service_dir / "merge_diagnostics.json"
        if managed:
            reject_symlink(diag)

        with diag.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "boundaries": diagnostics,
                    "quality": quality,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        if managed:
            try:
                check_source_hash(source, args.expected_source_sha256)
            except RuntimeError:
                (cache_root / "manifest.json").unlink(missing_ok=True)
                raise
    except BaseException:
        if staged_txt is not None:
            Path(staged_txt.name).unlink(missing_ok=True)
        raise

    # Интеллектуальный протокол намеренно НЕ запускается здесь.
    #
    # Транскрипция — самостоятельный P0-этап.
    # Она должна завершаться успешно независимо от доступности,
    # квот и ошибок моделей, используемых для анализа текста.
    #
    # Протокол запускается отдельно по готовому
    # *_ПОЛНАЯ_РАСШИФРОВКА.txt.

    try:
        log()
        log("=" * 60)
        log("ГОТОВО")
        log(f"Реплик в результате: {len(merged)}")
        log(f"Расшифровка: {final_txt}")
        log(f"Диагностика склейки: {diag}")
        log("=" * 60)

        if managed:
            os.replace(target, final_txt)
        else:
            subprocess.run(["/usr/bin/open", str(outdir)])
    except BaseException:
        if managed:
            target.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print()
        print("ОШИБКА:", e, file=sys.stderr)
        sys.exit(1)
