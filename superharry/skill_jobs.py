"""Bounded spool protocol shared by Django and the host skill worker."""
import hashlib
import json
from pathlib import Path

from django.conf import settings

from .csv_protocol import publish, read_limited


MAX_JOB_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 100 * 1024


def spool():
    return Path(settings.SKILL_SPOOL) if settings.SKILL_SPOOL else None


def skill_fingerprint(skill):
    value = [skill.name, skill.purpose, skill.instructions, skill.files, skill.dependencies]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def enqueue(directory, job_id, payload):
    data = json.dumps(payload, ensure_ascii=True).encode("ascii")
    if len(data) > MAX_JOB_BYTES:
        raise ValueError("Skill job exceeds the size limit.")
    publish(directory / f"{job_id}.skill-input", data, replace=False)


def result(directory, job_id):
    if directory is None:
        return None
    try:
        value = json.loads(read_limited(directory / f"{job_id}.skill-result", MAX_RESULT_BYTES))
        if not isinstance(value, dict) or value.get("status") not in ("complete", "failed", "timeout"):
            return {"status": "failed"}
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"status": "failed"}
