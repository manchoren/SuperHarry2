"""Bounded spool protocol shared by Django and the host skill worker."""
import hashlib
import base64
import json
import re
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import F
from django.utils import timezone

from .csv_protocol import publish, read_limited
from .models import ArtifactDelivery, ArtifactGroup, SkillArtifact, SkillOutbox, SkillPreparation, SkillRun


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
                if (owner_job is None or (owner_job.skill_id is None and not owner_job.repository_skill) or owner_job.job_id != job_id
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
    if outcome and not run.artifacts_imported:
        import_artifacts(directory, run, outcome)
    elif outcome and run.artifacts_imported and directory is not None:
        discard_artifact_spool(directory / f"{run.job_id}.skill-artifacts")
    return outcome


def discard_artifact_spool(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def discard_orphan_artifacts(directory):
    if directory is None:
        return
    for path in directory.glob("*.skill-artifacts"):
        try:
            identifier = UUID(path.name.removesuffix(".skill-artifacts"))
        except ValueError:
            continue
        if not (directory / f"{identifier}.skill-result").exists() or (directory / f"{identifier}.skill-running").exists():
            continue
        if not SkillRun.objects.filter(job_id=identifier).exists():
            discard_artifact_spool(path)


def artifact_group_label(name):
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    stem = re.sub(r"_ver(?:[2-9]|[1-9][0-9]+)$", "", stem, flags=re.IGNORECASE)
    return stem + ("." + extension if extension else "")


def import_artifacts(directory, run, outcome):
    if directory is None:
        return
    path = directory / f"{run.job_id}.skill-artifacts"
    try:
        raw = read_limited(path, 3 * 1024 * 1024)
    except FileNotFoundError:
        raw = None
    except (OSError, ValueError):
        return
    try:
        envelope = json.loads(raw) if raw is not None else {"generation": outcome.get("generation", 0), "files": []}
        files = envelope["files"]
        if not isinstance(files, list) or len(files) > 5 or envelope.get("generation", 0) != outcome.get("generation", 0):
            return
        decoded = []
        names = set()
        total = 0
        for item in files:
            name = item["name"]
            if not isinstance(name, str) or not name or len(name.encode("utf-8")) > 255 or name in names or any(char in name for char in ("/", "\\", "\x00")):
                raise ValueError
            content = base64.b64decode(item["data"], validate=True)
            total += len(content)
            if total > 2 * 1024 * 1024:
                raise ValueError
            names.add(name)
            decoded.append((name, content))
        with transaction.atomic():
            SkillRun.objects.filter(pk=run.pk).update(artifacts_imported=F("artifacts_imported"))
            current = SkillRun.objects.select_for_update().filter(pk=run.pk).first()
            if current is None:
                return
            if not current.artifacts_imported:
                for name, content in decoded:
                    artifact = None
                    group = None
                    if current.reply_id:
                        conversation = current.reply.conversation
                        matches = SkillArtifact.objects.filter(run__owner=current.owner, run__reply__conversation=conversation, name=name).order_by("pk")
                        selected = list(matches.filter(pk__in=current.selected_artifact_ids)[:2])
                        if len(selected) == 1:
                            artifact = selected[0]
                        elif not selected:
                            existing = list(matches[:2])
                            if len(existing) == 1:
                                artifact = existing[0]
                    partial = outcome.get("status") != "complete"
                    if artifact is not None:
                        if bytes(artifact.payload) == content:
                            continue
                        artifact.payload = content
                        artifact.partial = partial
                        artifact.updated_at = timezone.now()
                        artifact.save(update_fields=("payload", "partial", "updated_at"))
                    else:
                        if current.reply_id:
                            label = artifact_group_label(name)
                            key = hashlib.sha256(label.casefold().encode("utf-8")).hexdigest()
                            group, _ = ArtifactGroup.objects.get_or_create(owner=current.owner, conversation=conversation, automatic_key=key, defaults={"name": label})
                        artifact = SkillArtifact.objects.create(run=current, name=name, payload=content, partial=partial, group=group)
                    ArtifactDelivery.objects.update_or_create(run=current, name=name, defaults={"artifact": artifact, "partial": partial})
                current.artifacts_imported = True
                current.save(update_fields=("artifacts_imported",))
            run.artifacts_imported = True
            if raw is not None:
                transaction.on_commit(lambda: discard_artifact_spool(path))
    except (DatabaseError, KeyError, TypeError, ValueError, UnicodeError, OSError):
        return
