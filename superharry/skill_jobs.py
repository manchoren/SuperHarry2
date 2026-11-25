"""Bounded spool protocol shared by Django and the host skill worker."""
import hashlib
import json
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import F

from .csv_protocol import publish, read_limited
from .models import SkillOutbox, SkillPreparation, SkillRun


MAX_JOB_BYTES = 8 * 1024 * 1024
MAX_RESULT_BYTES = 100 * 1024


def spool():
    return Path(settings.SKILL_SPOOL) if settings.SKILL_SPOOL else None


def pending_network_requests(directory, job_id):
    if directory is None:
        return []
    pending = []
    for path in directory.glob(f"{job_id}.*.network-request"):
        try:
            request_id = UUID(path.name.split(".")[1])
            if path.name != f"{job_id}.{request_id}.network-request":
                continue
            if (directory / f"{job_id}.{request_id}.network-decision").exists():
                continue
            raw = read_limited(path, 64 * 1024)
            action = json.loads(raw)
            if (not isinstance(action, dict) or set(action) not in ({"method", "url", "body"}, {"method", "url", "body", "headers"})
                    or any(not isinstance(action[key], str) for key in ("method", "url", "body"))
                    or not isinstance(action.get("headers", {}), dict)
                    or any(not isinstance(k, str) or not isinstance(v, str) for k, v in action.get("headers", {}).items())):
                continue
            pending.append({
                "id": request_id, "digest": hashlib.sha256(raw).hexdigest(), **action,
                "display_url": json.dumps(action["url"], ensure_ascii=True),
                "display_body": json.dumps(action["body"], ensure_ascii=True),
                "display_headers": json.dumps(action.get("headers", {}), ensure_ascii=True, sort_keys=True),
            })
        except (OSError, ValueError, IndexError):
            continue
    return pending


def skill_fingerprint(skill):
    value = [skill.name, skill.purpose, skill.instructions, skill.files, skill.dependencies]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def encode_job(payload):
    data = json.dumps(payload, ensure_ascii=True).encode("ascii")
    if len(data) > MAX_JOB_BYTES:
        raise ValueError("Skill job exceeds the size limit.")
    return data


def enqueue(directory, job_id, payload):
    publish(directory / f"{job_id}.skill-input", encode_job(payload), replace=False)


def dispatch_one(job_id):
    directory = spool()
    if directory is None or connection.in_atomic_block:
        return False
    try:
        claimed = bool(SkillOutbox.objects.filter(job_id=job_id, state="pending").update(state="publishing"))
        with transaction.atomic():
            # SQLite ignores select_for_update; the no-op update also obtains a write lock.
            if not SkillOutbox.objects.filter(job_id=job_id, state="publishing").update(state=F("state")):
                return False
            job = SkillOutbox.objects.select_for_update().filter(job_id=job_id, state="publishing").first()
            if job is None:
                return False
            if any((directory / f"{job_id}.{suffix}").exists()
                   for suffix in ("skill-input", "skill-running", "skill-result")):
                job.delete()
                return True
            if job.preparation_id is not None:
                owner_job = SkillPreparation.objects.select_for_update().filter(pk=job.preparation_id).first()
                if owner_job is None or owner_job.job_id != job_id:
                    job.delete()
                    return False
            else:
                owner_job = SkillRun.objects.select_for_update().filter(pk=job.execution_id).first()
                if (owner_job is None or owner_job.skill_id is None or owner_job.job_id != job_id
                        or owner_job.owner_id != job.requested_by_id):
                    if owner_job is not None:
                        publish(directory / f"{job_id}.skill-result", b'{"status":"failed","reason":"skill_removed"}', replace=False)
                    job.delete()
                    return False
            if not claimed:
                return False
            enqueue(directory, job_id, job.payload)
            job.delete()
            return True
    except (OSError, ValueError, DatabaseError):
        return False


def dispatch_pending():
    ids = list(SkillOutbox.objects.order_by("state", "job_id").values_list("job_id", flat=True))
    delivered = sum(dispatch_one(job_id) for job_id in ids)
    for job_id in SkillRun.objects.exclude(lifecycle="").values_list("job_id", flat=True):
        dispatch_lifecycle(job_id)
    return delivered


def dispatch_lifecycle(job_id):
    directory = spool()
    if directory is None or connection.in_atomic_block:
        return False
    try:
        with transaction.atomic():
            if not SkillRun.objects.filter(job_id=job_id).exclude(lifecycle="").update(lifecycle=F("lifecycle")):
                return False
            run = SkillRun.objects.get(job_id=job_id)
            outcome = result(directory, job_id)
            if outcome and outcome.get("generation", 0) >= run.lifecycle_generation:
                return True
            for suffix in ("skill-control", "skill-applying", "skill-input", "skill-running"):
                try:
                    queued = json.loads(read_limited(directory / f"{job_id}.{suffix}", MAX_JOB_BYTES))
                    generation = queued.get("generation", 0) if suffix in ("skill-control", "skill-applying") else queued.get("_lifecycle_generation", 0)
                    if generation >= run.lifecycle_generation:
                        return True
                except (OSError, ValueError, AttributeError, TypeError):
                    continue
            outcome = result(directory, job_id)
            if outcome and outcome.get("generation", 0) >= run.lifecycle_generation:
                return True
            control = {"action": run.lifecycle, "generation": run.lifecycle_generation}
            if run.lifecycle == "updated":
                control["payload"] = run.execution_payload
                control["uncertain"] = run.lifecycle_uncertain
            publish(directory / f"{job_id}.skill-control", json.dumps(control, ensure_ascii=True).encode("ascii"), replace=True)
            return True
    except (OSError, ValueError, DatabaseError):
        return False


def resolve_uncertain(job_id):
    directory = spool()
    if directory is None:
        raise ValueError("Skill spool is not configured.")
    with transaction.atomic():
        # This update locks the row against an in-flight publisher, including on SQLite.
        if not SkillOutbox.objects.filter(job_id=job_id, state="publishing").update(state="resolving"):
            raise ValueError("The job is not awaiting uncertain-delivery resolution.")
        job = SkillOutbox.objects.select_for_update().get(job_id=job_id, state="resolving")
        if any((directory / f"{job_id}.{suffix}").exists() for suffix in ("skill-input", "skill-running")):
            raise ValueError("The job is queued or running; do not resolve it yet.")
        outcome = directory / f"{job_id}.skill-result"
        if not outcome.exists():
            publish(outcome, b'{"status":"failed","reason":"delivery_uncertain"}', replace=False)
        job.delete()


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


def run_result(directory, run):
    outcome = result(directory, run.job_id)
    if outcome and run.lifecycle and outcome.get("generation", 0) < run.lifecycle_generation:
        return None
    return outcome
