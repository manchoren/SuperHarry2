import base64
import hashlib
import json
import os
import re
from functools import wraps
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import OperationalError, transaction
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from .text_skills import SKILLS_DIRECTORY, parse_definition, repository_definitions, repository_path
from .models import SkillArtifact, SkillOutbox, SkillPreparation, SkillRun, VmSkill
from .skill_files import validate_skill_files
from .skill_jobs import dispatch_lifecycle, dispatch_one, encode_job, pending_network_requests, result, run_result, skill_fingerprint, spool
from .csv_protocol import read_limited


def retry_when_database_busy(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except OperationalError as error:
            if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                raise
            return HttpResponse("The skill is busy; please retry.", status=503)
    return wrapped


class SkillForm(forms.ModelForm):
    class Meta:
        model = VmSkill
        fields = ("name", "purpose", "instructions", "files", "dependencies")
        help_texts = {
            "files": 'JSON object mapping relative file names to text, e.g. {"main.py": "print(1)"}.',
            "dependencies": 'JSON array of Python package names or exact versions, e.g. ["reportlab==4.0.0"].',
            "instructions": "Selected conversation files are listed in the SKILL_INPUT_MANIFEST environment variable as JSON objects with id, name, and sandbox path.",
        }

    def clean_name(self):
        name = self.cleaned_data["name"]
        if repository_path(SKILLS_DIRECTORY, name).exists():
            raise forms.ValidationError("This name belongs to a repository skill.")
        return name

    def clean_files(self):
        files = self.cleaned_data.get("files")
        if files is None:
            files = {}
        try:
            return validate_skill_files(files)
        except ValueError as error:
            raise forms.ValidationError(str(error)) from error

    def clean_dependencies(self):
        dependencies = self.cleaned_data.get("dependencies")
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(
            not isinstance(item, str) or not item.strip() for item in dependencies
        ):
            raise forms.ValidationError("Enter a JSON array of dependency specifications.")
        return dependencies


def preparation_status(skill):
    if repository_path(SKILLS_DIRECTORY, skill.name).exists():
        return "Name conflict"
    preparation = getattr(skill, "preparation", None)
    if not preparation or preparation.fingerprint != skill_fingerprint(skill):
        return "Preparation required"
    directory = spool()
    if directory is None:
        return "Preparation unavailable"
    outcome = result(directory, preparation.job_id)
    if outcome is None:
        if any((directory / f"{preparation.job_id}.{suffix}").exists() for suffix in ("skill-input", "skill-running")):
            return "Preparation pending"
        delivery = SkillOutbox.objects.filter(job_id=preparation.job_id).values_list("state", flat=True).first()
        if delivery == "pending":
            return "Delivery pending"
        if delivery == "publishing":
            return "Delivery outcome uncertain or publication in progress; operator investigation required"
        outcome = result(directory, preparation.job_id)
        if outcome is None:
            return "Preparation failed"
    if outcome.get("status") == "complete":
        if outcome.get("wheel_cache") and not (directory / f"{preparation.job_id}.skill-wheels").is_file():
            return "Prepared dependencies unavailable; prepare again"
        return "Prepared"
    if outcome.get("status") == "timeout":
        return "Preparation timed out"
    if outcome.get("reason") == "dependency_preparation_failed":
        return "Dependency preparation failed; check the supplied wheels and versions"
    if outcome.get("reason") == "cleanup_unverified":
        return "Worker cleanup requires recovery"
    if outcome.get("reason") == "invalid_request":
        return "Invalid skill files or dependency specifications"
    if outcome.get("reason") == "file_preparation_failed":
        return "Skill files could not be prepared"
    return "Preparation failed"


@never_cache
@login_required
def catalog(request):
    skills = []
    repository_paths = repository_definitions(SKILLS_DIRECTORY)
    repository_names = set(repository_paths)
    for name, path in repository_paths.items():
        try:
            definition = parse_definition(path)
        except (OSError, UnicodeError, ValueError, KeyError):
            continue
        skills.append({"name": name, "purpose": definition["purpose"], "origin": "Repository"})
    is_admin = request.user.is_superuser or request.user.groups.filter(name="Administrators").exists()
    skills.extend({"name": skill.name, "purpose": skill.purpose, "origin": "VM", "is_vm": True,
                   "status": preparation_status(skill), "editable": is_admin or skill.owner_id == request.user.pk}
                  for skill in VmSkill.objects.select_related("preparation").defer("preparation__payload").order_by("name"))
    vm_names = set(VmSkill.objects.values_list("name", flat=True))
    for skill in skills:
        skill["conflict"] = skill["name"] in repository_names and skill["name"] in vm_names
    notices = SkillRun.objects.filter(owner=request.user).exclude(lifecycle="").order_by("-pk")
    return render(request, "skills/catalog.html", {"skills": skills, "run_notices": notices})


@never_cache
@login_required
def create(request):
    form = SkillForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        skill = form.save(commit=False)
        skill.owner = request.user
        skill.save()
        return redirect("skills")
    return render(request, "skills/form.html", {"form": form})


def manageable_skills(user):
    skills = VmSkill.objects.all()
    if not (user.is_superuser or user.groups.filter(name="Administrators").exists()):
        skills = skills.filter(owner=user)
    return skills


def has_active_jobs(skill):
    identifiers = list(SkillRun.objects.filter(skill=skill).values_list("job_id", flat=True))
    preparation = SkillPreparation.objects.defer("payload").filter(skill=skill).first()
    if preparation:
        identifiers.append(preparation.job_id)
    if not identifiers:
        return False
    if SkillOutbox.objects.filter(job_id__in=identifiers).exists():
        return True
    directory = spool()
    if directory is None:
        return True
    return any(
        (directory / f"{identifier}.{suffix}").exists()
        for identifier in identifiers for suffix in ("skill-input", "skill-running")
    )


def original_run_payload(directory, run):
    if run.execution_payload:
        return run.execution_payload.copy()
    outbox = SkillOutbox.objects.filter(job_id=run.job_id).first()
    if outbox:
        return outbox.payload.copy()
    if directory is not None:
        for suffix in ("skill-input", "skill-running", "skill-input", "skill-running"):
            try:
                payload = json.loads(read_limited(directory / f"{run.job_id}.{suffix}", 8 * 1024 * 1024))
                if isinstance(payload, dict) and isinstance(payload.get("input"), str):
                    return payload
            except (OSError, ValueError):
                pass
    return {}


@never_cache
@login_required
def detail(request, name):
    skill = get_object_or_404(
        VmSkill.objects.select_related("preparation").defer("preparation__payload"), name=name,
    )
    can_manage = manageable_skills(request.user).filter(pk=skill.pk).exists()
    return render(request, "skills/detail.html", {
        "skill": skill, "can_manage": can_manage, "preparation_status": preparation_status(skill),
        "preparation_configured": spool() is not None,
        "preparation_job_id": skill.preparation.job_id if getattr(skill, "preparation", None) else None,
    })


@never_cache
@login_required
@require_POST
def prepare(request, name):
    skill = get_object_or_404(manageable_skills(request.user), name=name)
    if repository_path(SKILLS_DIRECTORY, skill.name).exists():
        return HttpResponse("Resolve the skill name conflict before preparation.", status=409)
    directory = spool()
    if directory is None:
        return HttpResponse("Skill preparation is not configured.", status=503)
    previous = SkillPreparation.objects.defer("payload").filter(skill=skill).first()
    if previous and (SkillOutbox.objects.filter(job_id=previous.job_id).exists() or any((directory / f"{previous.job_id}.{suffix}").exists() for suffix in ("skill-input", "skill-running"))):
        return HttpResponse("Wait for the current preparation or worker recovery.", status=409)
    entrypoint = request.POST.get("entrypoint", "")
    if entrypoint not in skill.files:
        return HttpResponse("Choose an existing skill file as the entrypoint.", status=400)
    wheels = {}
    total = 0
    for incoming in request.FILES.getlist("wheels"):
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", incoming.name) or incoming.name in wheels:
            return HttpResponse("Choose uniquely named wheel files.", status=400)
        if incoming.size > 6 * 1024 * 1024:
            return HttpResponse("Wheel files exceed the size limit.", status=400)
        content = incoming.read(6 * 1024 * 1024 + 1)
        total += len(content)
        if total > 6 * 1024 * 1024:
            return HttpResponse("Wheel files exceed the size limit.", status=400)
        wheels[incoming.name] = base64.b64encode(content).decode("ascii")
    payload = {
        "operation": "prepare", "files": skill.files, "entrypoint": entrypoint, "input": "",
        "dependencies": skill.dependencies, "wheels": wheels,
    }
    identifier = uuid4()
    try:
        encode_job(payload)
        with transaction.atomic():
            preparation, _ = SkillPreparation.objects.update_or_create(skill=skill, defaults={
                "job_id": identifier, "fingerprint": skill_fingerprint(skill), "payload": payload,
            })
            SkillOutbox.objects.create(job_id=identifier, payload=payload, preparation=preparation, requested_by=request.user)
            transaction.on_commit(lambda: dispatch_one(identifier))
    except ValueError:
        return HttpResponse("Skill job exceeds the size limit.", status=400)
    except OSError:
        return HttpResponse("The skill worker could not accept the job.", status=503)
    return redirect("skill-detail", name=skill.name)


@never_cache
@login_required
@require_POST
@retry_when_database_busy
@transaction.atomic
def run(request, name):
    skill = get_object_or_404(VmSkill, name=name)
    if not VmSkill.objects.filter(pk=skill.pk).update(owner_id=F("owner_id")):
        raise Http404
    skill.refresh_from_db()
    directory = spool()
    if directory is None:
        return HttpResponse("Skill execution is not configured.", status=503)
    if repository_path(SKILLS_DIRECTORY, skill.name).exists():
        return HttpResponse("Resolve the skill name conflict before execution.", status=409)
    preparation = SkillPreparation.objects.filter(skill=skill).first()
    if not preparation or preparation.fingerprint != skill_fingerprint(skill):
        return HttpResponse("Prepare this skill before running it.", status=409)
    outcome = result(directory, preparation.job_id)
    if not outcome or outcome.get("status") != "complete":
        return HttpResponse("Prepare this skill before running it.", status=409)
    input_text = request.POST.get("input", "")
    if len(input_text.encode("utf-8")) > 256 * 1024:
        return HttpResponse("Skill input exceeds the size limit.", status=400)
    try:
        wheel_file = directory / f"{preparation.job_id}.skill-wheels"
        if outcome.get("wheel_cache") or wheel_file.exists():
            wheels = json.loads(read_limited(wheel_file, 8 * 1024 * 1024))
            if not isinstance(wheels, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in wheels.items()):
                raise ValueError("Invalid prepared dependencies")
        else:
            wheels = preparation.payload.get("wheels", {})
    except (OSError, ValueError):
        return HttpResponse("Prepared dependencies are unavailable; prepare this skill again.", status=409)
    payload = {**preparation.payload, "operation": "run", "input": input_text, "wheels": wheels,
               "output_max_count": settings.SKILL_OUTPUT_MAX_COUNT, "output_max_bytes": settings.SKILL_OUTPUT_MAX_BYTES}
    selected_files = getattr(request, "skill_input_files", [])
    if selected_files:
        if len(selected_files) > 5 or sum(len(item.payload) for item in selected_files) > 4 * 1024 * 1024:
            return HttpResponse("Selected files exceed the skill input limit.", status=400)
        payload["input_files"] = [{"id": f"artifact-{item.pk}" if isinstance(item, SkillArtifact) else item.pk, "name": item.name, "data": base64.b64encode(bytes(item.payload)).decode("ascii")} for item in selected_files]
        payload["requires_file_inputs"] = True
    identifier = uuid4()
    try:
        encode_job(payload)
        with transaction.atomic():
            retained_payload = {key: value for key, value in payload.items() if key != "input_files"}
            execution = SkillRun.objects.create(skill=skill, skill_name=skill.name, owner=request.user, job_id=identifier, execution_payload=retained_payload,
                                                selected_artifact_ids=[item.pk for item in selected_files if isinstance(item, SkillArtifact)])
            SkillOutbox.objects.create(job_id=identifier, payload=payload, execution=execution, requested_by=request.user)
            transaction.on_commit(lambda: dispatch_one(identifier))
    except ValueError:
        return HttpResponse("Skill job exceeds the size limit.", status=400)
    except OSError:
        return HttpResponse("The skill worker could not accept the job.", status=503)
    return redirect("skill-run-detail", job_id=identifier)


@never_cache
@login_required
@require_safe
def run_detail(request, job_id):
    job = get_object_or_404(SkillRun, job_id=job_id, owner=request.user)
    outcome = run_result(spool(), job)
    delivery = SkillOutbox.objects.filter(job_id=job.job_id).values_list("state", flat=True).first()
    directory = spool()
    if delivery == "publishing" and directory is not None and any(
        (directory / f"{job.job_id}.{suffix}").exists() for suffix in ("skill-input", "skill-running")
    ):
        delivery = "pending"
    return render(request, "skills/run_detail.html", {
        "job": job, "outcome": outcome, "delivery": delivery,
        "network_requests": pending_network_requests(directory, job.job_id) if outcome is None and running_is_current(directory, job) else [],
    })


@never_cache
@login_required
@require_safe
def artifact_status(request, job_id):
    job = get_object_or_404(SkillRun, job_id=job_id, owner=request.user)
    outcome = run_result(spool(), job)
    return JsonResponse({
        "pending": outcome is None or not job.artifacts_imported,
        "status": outcome.get("status") if outcome else None,
        "artifacts": [{"name": delivery.name, "partial": delivery.partial, "removed": delivery.artifact_id is None,
                       "url": reverse("skill-artifact-download", args=(delivery.artifact_id,)) if delivery.artifact_id else None}
                      for delivery in job.artifact_deliveries.all()],
    })


@never_cache
@login_required
@require_safe
def artifact_download(request, pk):
    artifact = get_object_or_404(SkillArtifact, pk=pk, run__owner=request.user)
    return HttpResponse(bytes(artifact.payload), content_type="application/octet-stream", headers={
        "Content-Disposition": content_disposition_header(True, artifact.name),
        "X-Content-Type-Options": "nosniff",
    })


@never_cache
@login_required
@require_POST
def network_decision(request, job_id, request_id):
    job = get_object_or_404(SkillRun, job_id=job_id, owner=request.user)
    directory = spool()
    choice = request.POST.get("decision")
    if choice not in ("approve", "deny"):
        return HttpResponse("Invalid decision.", status=400)
    if (directory is None or not running_is_current(directory, job)
            or run_result(directory, job) is not None):
        return HttpResponse("This request is no longer pending.", status=409)
    path = directory / f"{job_id}.{request_id}.network-request"
    try:
        raw = read_limited(path, 64 * 1024)
        digest = hashlib.sha256(raw).hexdigest()
        if request.POST.get("digest") != digest:
            return HttpResponse("The request has changed.", status=409)
        if not any(action["id"] == request_id and action["digest"] == digest
                   for action in pending_network_requests(directory, job_id)):
            return HttpResponse("This request is no longer pending.", status=409)
        data = json.dumps({"decision": choice, "digest": digest}).encode("ascii")
        with NamedTemporaryFile(dir=directory, delete=False) as handle:
            handle.write(data)
            temp = Path(handle.name)
        try:
            temp.chmod(0o644)
            os.link(temp, directory / f"{job_id}.{request_id}.network-decision")
        finally:
            temp.unlink(missing_ok=True)
    except (OSError, ValueError):
        return HttpResponse("This request is no longer pending.", status=409)
    return redirect("skill-run-detail", job_id=job.job_id)


def running_is_current(directory, job):
    if directory is None or job.lifecycle == "deleted":
        return False
    if job.lifecycle_generation == 0:
        return (directory / f"{job.job_id}.skill-running").is_file()
    try:
        request = json.loads(read_limited(directory / f"{job.job_id}.skill-running", 8 * 1024 * 1024))
        return request.get("_lifecycle_generation", 0) == job.lifecycle_generation
    except (OSError, ValueError, AttributeError):
        return False


@never_cache
@login_required
@require_safe
def repository_detail(request, name):
    try:
        definition = repository_path(SKILLS_DIRECTORY, name).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise Http404 from None
    return render(request, "skills/repository_detail.html", {"name": name, "definition": definition})


@never_cache
@login_required
@retry_when_database_busy
def edit(request, name):
    skill = get_object_or_404(manageable_skills(request.user), name=name)
    form = SkillForm(request.POST or None, instance=skill)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            if not VmSkill.objects.filter(pk=skill.pk).update(owner_id=F("owner_id")):
                raise Http404
            skill.refresh_from_db()
            form = SkillForm(request.POST, instance=skill)
            if not form.is_valid():
                return render(request, "skills/form.html", {"form": form})
            directory = spool()
            preparation = SkillPreparation.objects.filter(skill=skill).first()
            if preparation and (SkillOutbox.objects.filter(job_id=preparation.job_id).exists() or
                    directory is None or any((directory / f"{preparation.job_id}.{suffix}").exists()
                                             for suffix in ("skill-input", "skill-running"))):
                return HttpResponse("Wait for active skill jobs to finish.", status=409)
            active = [run for run in SkillRun.objects.filter(skill=skill)
                      if run_result(directory, run) is None]
            updates = []
            for run in active:
                uncertain = run.lifecycle_uncertain or (not run.execution_payload and directory is not None and (
                    (directory / f"{run.job_id}.skill-running").exists() or
                    (not (directory / f"{run.job_id}.skill-input").exists() and
                     not SkillOutbox.objects.filter(job_id=run.job_id).exists())
                ))
                payload = original_run_payload(directory, run)
                if payload.get("entrypoint") not in form.cleaned_data["files"]:
                    payload = {}
                else:
                    if payload.get("dependencies") != form.cleaned_data["dependencies"]:
                        payload["wheels"] = {}
                    payload["files"] = form.cleaned_data["files"]
                    payload["dependencies"] = form.cleaned_data["dependencies"]
                try:
                    encode_job({"action": "updated", "generation": run.lifecycle_generation + 1, "payload": payload, "uncertain": uncertain})
                except ValueError:
                    return HttpResponse("Updated skill job exceeds the size limit.", status=400)
                updates.append((run, payload, uncertain))
            for run, payload, uncertain in updates:
                run.execution_payload = payload
                run.lifecycle_uncertain = uncertain
                run.lifecycle = "updated"
                run.lifecycle_generation += 1
                run.artifacts_imported = False
                run.save(update_fields=("execution_payload", "lifecycle", "lifecycle_generation", "lifecycle_uncertain", "artifacts_imported"))
                SkillOutbox.objects.filter(job_id=run.job_id).delete()
                transaction.on_commit(lambda identifier=run.job_id: dispatch_lifecycle(identifier))
            form.save()
        return redirect("skills")
    return render(request, "skills/form.html", {"form": form})


@never_cache
@login_required
@require_POST
@retry_when_database_busy
def delete(request, name):
    with transaction.atomic():
        skill = get_object_or_404(manageable_skills(request.user), name=name)
        # SQLite needs a write to serialize this transition with a publisher.
        VmSkill.objects.filter(pk=skill.pk).update(owner_id=F("owner_id"))
        directory = spool()
        preparation = SkillPreparation.objects.filter(skill=skill).first()
        if preparation and (SkillOutbox.objects.filter(job_id=preparation.job_id).exists() or
                directory is None or any((directory / f"{preparation.job_id}.{suffix}").exists()
                                         for suffix in ("skill-input", "skill-running"))):
            return HttpResponse("Wait for active skill jobs to finish.", status=409)
        runs = list(SkillRun.objects.filter(skill=skill))
        if directory is None and runs:
            return HttpResponse("Skill execution is not configured.", status=503)
        for run in runs:
            if run_result(directory, run) is None:
                SkillOutbox.objects.filter(job_id=run.job_id).delete()
                run.lifecycle = "deleted"
                run.execution_payload = {}
                run.lifecycle_generation += 1
                run.save(update_fields=("lifecycle", "lifecycle_generation", "execution_payload"))
                transaction.on_commit(lambda identifier=run.job_id: dispatch_lifecycle(identifier))
        skill.delete()
    return redirect("skills")
