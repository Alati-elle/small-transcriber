"""Validated, private user settings for the managed pipeline only."""

import copy
import json
import os
import re
import tempfile
from pathlib import Path


DEFAULT_MODELS = ["gemini-3.5-transcribe", "gemini-3.6-flash",
                  "gemini-3.8-flash", "gemini-3.5-flash"]
STAGES = ("transcription", "speaker_normalization", "name_detection",
          "protocol_generation")
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def defaults():
    return {
        "schema_version": 1,
        "gemini": {
            "quota_timezone": "America/Los_Angeles",
            "known_models": list(DEFAULT_MODELS),
            "models": {model: {"rpd": None, "rpm": None, "tpm": None}
                       for model in DEFAULT_MODELS},
            "stages": {
                "transcription": {"primary_model": DEFAULT_MODELS[0], "fallback_models": []},
                **{stage: {"primary_model": DEFAULT_MODELS[1],
                           "fallback_models": DEFAULT_MODELS[2:3] if stage != "protocol_generation"
                           else DEFAULT_MODELS[2:]}
                   for stage in STAGES[1:]},
            },
        },
    }


def validate(data):
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported config schema")
    gemini = data.get("gemini")
    if not isinstance(gemini, dict) or gemini.get("quota_timezone") != "America/Los_Angeles":
        raise ValueError("Invalid quota timezone")
    known = gemini.get("known_models")
    if not isinstance(known, list) or not known or len(known) > 50 or any(
            not isinstance(model, str) or not MODEL_ID.fullmatch(model) for model in known
    ) or len(set(known)) != len(known):
        raise ValueError("Invalid known models")
    models = gemini.get("models")
    if not isinstance(models, dict) or any(
            not isinstance(model, str) or not MODEL_ID.fullmatch(model) or
            not isinstance(limits, dict) or any(
                field not in limits or (limits[field] is not None and
                (type(limits[field]) is not int or not 0 < limits[field] <= 1_000_000_000))
                for field in ("rpd", "rpm", "tpm"))
            for model, limits in models.items()):
        raise ValueError("Invalid manual limits")
    stages = gemini.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("Missing stages")
    for stage in STAGES:
        selection = stages.get(stage)
        if not isinstance(selection, dict):
            raise ValueError("Missing stage selection")
        order = [selection.get("primary_model"), *selection.get("fallback_models", [])] if isinstance(
            selection.get("fallback_models"), list) else []
        if not order or len(order) > 10 or any(
                not isinstance(model, str) or model not in known for model in order) or len(set(order)) != len(order):
            raise ValueError("Invalid stage model order")
    return data


def load(root):
    """Return (resolved config, warning). Missing/malformed files are never changed."""
    path = Path(root) / "config.json"
    try:
        if path.is_symlink():
            raise ValueError("Config is a symlink")
        data = json.loads(path.read_text(encoding="utf-8"))
        return validate(data), None
    except FileNotFoundError:
        return defaults(), None
    except (OSError, ValueError, TypeError, KeyError):
        return defaults(), "Файл настроек повреждён; используются значения по умолчанию."


def save(root, data):
    validate(data)
    root = Path(root)
    if root.is_symlink():
        raise ValueError("Config root is a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    path = root / "config.json"
    if path.is_symlink():
        raise ValueError("Config is a symlink")
    fd, temp = tempfile.mkstemp(prefix=".config-", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def stage_models(config, stage):
    selection = config["gemini"]["stages"][stage]
    return [selection["primary_model"], *selection["fallback_models"]]


def reset_limits(config):
    result = copy.deepcopy(config)
    for limits in result["gemini"]["models"].values():
        for field in ("rpd", "rpm", "tpm"):
            limits[field] = None
    return result
