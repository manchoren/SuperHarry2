import tomllib
from pathlib import PurePosixPath

from django import forms
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from .text_skills import SKILLS_DIRECTORY
from .models import VmSkill


class SkillForm(forms.ModelForm):
    class Meta:
        model = VmSkill
        fields = ("name", "purpose", "instructions", "files", "dependencies")
        help_texts = {
            "files": 'JSON object mapping relative file names to text, e.g. {"main.py": "print(1)"}.',
            "dependencies": 'JSON array of dependency specifications, e.g. ["reportlab==4.0.0"].',
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
        if not isinstance(files, dict):
            raise forms.ValidationError("Enter a JSON object of file names and text.")
        for name, content in files.items():
            path = PurePosixPath(name)
            if (not name or "\\" in name or path.is_absolute() or
                any(part in (".", "..") for part in name.split("/")) or
                not isinstance(content, str)):
                raise forms.ValidationError("Files must use safe relative names and text content.")
        return files

    def clean_dependencies(self):
        dependencies = self.cleaned_data.get("dependencies")
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list) or any(
            not isinstance(item, str) or not item.strip() for item in dependencies
        ):
            raise forms.ValidationError("Enter a JSON array of dependency specifications.")
        return dependencies


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
    skills.extend({"name": skill.name, "purpose": skill.purpose, "origin": "VM (draft)",
                   "editable": is_admin or skill.owner_id == request.user.pk}
                  for skill in VmSkill.objects.order_by("name"))
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


@never_cache
@login_required
def detail(request, name):
    skill = get_object_or_404(VmSkill, name=name)
    return render(request, "skills/detail.html", {"skill": skill})


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
    skill.delete()
    return redirect("skills")
