import base64
import re
import tomllib
from uuid import uuid4

from django import forms
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from .text_skills import SKILLS_DIRECTORY
from .models import SkillOutbox, SkillPreparation, SkillRun, VmSkill
from .skill_files import validate_skill_files
from .skill_jobs import dispatch_one, encode_job, result, skill_fingerprint, spool


class SkillForm(forms.ModelForm):
    class Meta:
        model = VmSkill
        fields = ("name", "purpose", "instructions", "files", "dependencies")
        help_texts = {
            "files": 'JSON object mapping relative file names to text, e.g. {"main.py": "print(1)"}.',
            "dependencies": 'JSON array of Python package names or exact versions, e.g. ["reportlab==4.0.0"].',
        }

    def clean_name(self):
        name = self.cleaned_data["name"]
        if (SKILLS_DIRECTORY / f"{name}.md").exists():
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
    if (SKILLS_DIRECTORY / f"{skill.name}.md").exists():
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
    repository_paths = sorted(SKILLS_DIRECTORY.glob("*.md"))
    repository_names = {path.stem for path in repository_paths}
    for path in repository_paths:
        try:
            prefix, metadata, instructions = path.read_text(encoding="utf-8").split("+++", 2)
            manifest = tomllib.loads(metadata)
            purpose = manifest["purpose"]
            if (
                prefix.strip() or not isinstance(purpose, str)
                or not purpose.strip() or not instructions.strip()
            ):
                continue
        except (OSError, UnicodeError, ValueError, KeyError):
            continue
        skills.append({"name": path.stem, "purpose": purpose, "origin": "Repository"})
    is_admin = request.user.is_superuser or request.user.groups.filter(name="Administrators").exists()
    skills.extend({"name": skill.name, "purpose": skill.purpose, "origin": "VM", "is_vm": True,
                   "status": preparation_status(skill), "editable": is_admin or skill.owner_id == request.user.pk}
                  for skill in VmSkill.objects.select_related("preparation").defer("preparation__payload").order_by("name"))
    vm_names = set(VmSkill.objects.values_list("name", flat=True))
    for skill in skills:
        skill["conflict"] = skill["name"] in repository_names and skill["name"] in vm_names
    return render(request, "skills/catalog.html", {"skills": skills})


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
    if (SKILLS_DIRECTORY / f"{skill.name}.md").exists():
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
def run(request, name):
    skill = get_object_or_404(VmSkill, name=name)
    directory = spool()
    if directory is None:
        return HttpResponse("Skill execution is not configured.", status=503)
    if (SKILLS_DIRECTORY / f"{skill.name}.md").exists():
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
    payload = {**preparation.payload, "operation": "run", "input": input_text}
    identifier = uuid4()
    try:
        encode_job(payload)
        with transaction.atomic():
            execution = SkillRun.objects.create(skill=skill, skill_name=skill.name, owner=request.user, job_id=identifier)
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
    outcome = result(spool(), job.job_id)
    delivery = SkillOutbox.objects.filter(job_id=job.job_id).values_list("state", flat=True).first()
    directory = spool()
    if delivery == "publishing" and directory is not None and any(
        (directory / f"{job.job_id}.{suffix}").exists() for suffix in ("skill-input", "skill-running")
    ):
        delivery = "pending"
    return render(request, "skills/run_detail.html", {"job": job, "outcome": outcome, "delivery": delivery})


@never_cache
@login_required
@require_safe
def repository_detail(request, name):
    try:
        definition = (SKILLS_DIRECTORY / f"{name}.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise Http404 from None
    return render(request, "skills/repository_detail.html", {"name": name, "definition": definition})


@never_cache
@login_required
def edit(request, name):
    skill = get_object_or_404(manageable_skills(request.user), name=name)
    if request.method == "POST" and has_active_jobs(skill):
        return HttpResponse("Wait for active skill jobs to finish.", status=409)
    form = SkillForm(request.POST or None, instance=skill)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("skills")
    return render(request, "skills/form.html", {"form": form})


@never_cache
@login_required
@require_POST
def delete(request, name):
    skill = get_object_or_404(manageable_skills(request.user), name=name)
    if has_active_jobs(skill):
        return HttpResponse("Wait for active skill jobs to finish.", status=409)
    skill.delete()
    return redirect("skills")
