from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .models import ArtifactGroup, ChatInput, FileAccessAudit, RemovedChatInput, SkillArtifact


def administrator(user):
    return user.is_superuser or user.groups.filter(name="Administrators").exists()


def audit_entries(actor, action, inputs=(), artifacts=()):
    if not administrator(actor):
        return
    entries = [FileAccessAudit(actor=actor, owner_id=item.owner_id, action=action, file_kind="input", file_id=item.pk, file_name=item.name) for item in inputs]
    entries += [FileAccessAudit(actor=actor, owner_id=item.run.owner_id, action=action, file_kind="artifact", file_id=item.pk, file_name=item.name) for item in artifacts]
    FileAccessAudit.objects.bulk_create(entries)


def owner_for(request, *, required=False):
    if not administrator(request.user):
        return request.user
    value = request.GET.get("owner", "")
    if not value:
        if required:
            raise ValueError("Select a user before deleting by period.")
        return None
    if not value.isdigit():
        raise Http404
    return get_object_or_404(get_user_model(), pk=int(value))


def visible_file(request, kind, identifier):
    is_admin = administrator(request.user)
    if kind == "input":
        query = ChatInput.objects.select_related("owner", "message__conversation")
        if not is_admin:
            query = query.filter(owner=request.user)
        return get_object_or_404(query, pk=identifier)
    if kind == "artifact":
        query = SkillArtifact.objects.select_related("run__owner", "run__reply__conversation", "group")
        if not is_admin:
            query = query.filter(run__owner=request.user)
        return get_object_or_404(query, pk=identifier)
    raise Http404


@never_cache
@login_required
@require_GET
def history(request):
    owner = owner_for(request)
    inputs = ChatInput.objects.select_related("owner", "message__conversation").defer("payload")
    artifacts = SkillArtifact.objects.select_related("run__owner", "run__reply__conversation", "group").defer("payload")
    if owner is not None:
        inputs = inputs.filter(owner=owner)
        artifacts = artifacts.filter(run__owner=owner)
    items = []
    for item in inputs:
        items.append({"kind": "input", "id": item.pk, "name": item.name, "date": item.created_at, "owner": item.owner,
                      "conversation": item.message.conversation if item.message_id else None, "object": item})
    for item in artifacts:
        items.append({"kind": "artifact", "id": item.pk, "name": item.name, "date": item.updated_at, "owner": item.run.owner,
                      "conversation": item.run.reply.conversation if item.run.reply_id else None, "object": item})
    items.sort(key=lambda item: (item["date"] is not None, item["date"] or datetime.min.replace(tzinfo=timezone.get_current_timezone()), item["id"]), reverse=True)
    page = Paginator(items, 50).get_page(request.GET.get("page"))
    audit_entries(request.user, "view", [item["object"] for item in page if item["kind"] == "input"],
                  [item["object"] for item in page if item["kind"] == "artifact"])
    return render(request, "file_history.html", {"page": page, "selected_owner": owner, "is_admin": administrator(request.user),
                  "users": get_user_model().objects.order_by("username") if administrator(request.user) else ()})


@never_cache
@login_required
@require_GET
def download(request, kind, identifier):
    item = visible_file(request, kind, identifier)
    audit_entries(request.user, "download", [item] if kind == "input" else (), [item] if kind == "artifact" else ())
    return HttpResponse(bytes(item.payload), content_type="application/octet-stream", headers={
        "Content-Disposition": content_disposition_header(True, item.name), "X-Content-Type-Options": "nosniff",
    })


def period_start(period, now):
    if period == "hour":
        return now - timedelta(hours=1)
    if period == "today":
        return timezone.make_aware(datetime.combine(timezone.localtime(now).date(), time.min), timezone.get_current_timezone())
    if period == "week":
        return now - timedelta(days=7)
    if period == "month":
        return now - timedelta(days=30)
    if period == "all":
        return None
    raise ValueError("Choose a valid period.")


def delete_inputs(inputs):
    for item in inputs:
        if item.message_id:
            RemovedChatInput.objects.create(message_id=item.message_id, name=item.name)
    ChatInput.objects.filter(pk__in=[item.pk for item in inputs]).delete()


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def delete(request):
    if request.method == "GET":
        kind = request.GET.get("kind")
        if kind:
            identifier = request.GET.get("id", "")
            if not identifier.isdigit():
                raise Http404
            item = visible_file(request, kind, int(identifier))
            owner = item.owner if kind == "input" else item.run.owner
            inputs = [item] if kind == "input" else []
            artifacts = list(SkillArtifact.objects.filter(group=item.group, run__owner=owner).select_related("run__owner").defer("payload")) if kind == "artifact" and item.group_id else ([item] if kind == "artifact" else [])
        else:
            try:
                owner = owner_for(request, required=True)
                start = period_start(request.GET.get("period", ""), timezone.now())
            except ValueError as error:
                return HttpResponse(str(error), status=400)
            inputs_query = ChatInput.objects.filter(owner=owner)
            artifacts_query = SkillArtifact.objects.filter(run__owner=owner)
            if start is not None:
                inputs_query = inputs_query.filter(created_at__gte=start)
                artifacts_query = artifacts_query.filter(updated_at__gte=start)
            inputs = list(inputs_query.select_related("owner").defer("payload"))
            selected = list(artifacts_query.select_related("run__owner", "group").defer("payload"))
            groups = {item.group_id for item in selected if item.group_id}
            ids = {item.pk for item in selected}
            ids.update(SkillArtifact.objects.filter(group_id__in=groups, run__owner=owner).values_list("pk", flat=True))
            artifacts = list(SkillArtifact.objects.filter(pk__in=ids, run__owner=owner).select_related("run__owner", "group").defer("payload"))
        if owner is None:
            raise Http404
        snapshot = {"actor": request.user.pk, "owner": owner.pk, "inputs": [item.pk for item in inputs],
                    "artifacts": [{"id": item.pk, "group": item.group_id, "updated": item.updated_at.isoformat() if item.updated_at else None} for item in artifacts]}
        audit_entries(request.user, "view", inputs, artifacts)
        token = signing.dumps(snapshot, salt="superharry.file-delete", compress=True)
        return render(request, "file_delete_confirm.html", {"owner": owner, "inputs": inputs, "artifacts": artifacts,
                      "count": len(inputs) + len(artifacts), "token": token})

    try:
        snapshot = signing.loads(request.POST.get("token", ""), salt="superharry.file-delete", max_age=600)
        if snapshot["actor"] != request.user.pk or (snapshot["owner"] != request.user.pk and not administrator(request.user)):
            raise ValueError
        target = snapshot["owner"]
        input_ids = snapshot["inputs"]
        artifact_map = {item["id"]: (item["group"], item["updated"]) for item in snapshot["artifacts"]}
        with transaction.atomic():
            ChatInput.objects.filter(owner_id=target, pk__in=input_ids).update(name=F("name"))
            SkillArtifact.objects.filter(run__owner_id=target, pk__in=artifact_map).update(name=F("name"))
            inputs = list(ChatInput.objects.filter(owner_id=target, pk__in=input_ids).select_related("owner").defer("payload"))
            artifacts = list(SkillArtifact.objects.filter(run__owner_id=target, pk__in=artifact_map).select_related("run__owner", "group").defer("payload"))
            actual = {item.pk: (item.group_id, item.updated_at.isoformat() if item.updated_at else None) for item in artifacts}
            if len(inputs) != len(input_ids) or actual != artifact_map:
                return HttpResponse("The file list changed. Review the deletion again.", status=409)
            group_ids = {item.group_id for item in artifacts if item.group_id}
            members = set(SkillArtifact.objects.filter(group_id__in=group_ids).values_list("pk", flat=True))
            if not members.issubset(set(artifact_map)):
                return HttpResponse("The file list changed. Review the deletion again.", status=409)
            audit_entries(request.user, "delete", inputs, artifacts)
            delete_inputs(inputs)
            SkillArtifact.objects.filter(pk__in=artifact_map).delete()
            for group in ArtifactGroup.objects.filter(pk__in=group_ids):
                if not group.artifacts.exists():
                    group.delete()
        return redirect('file-history')
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return HttpResponse("This deletion confirmation is invalid or expired.", status=400)


@never_cache
@login_required
@require_GET
def audit_log(request):
    if not administrator(request.user):
        raise Http404
    entries = FileAccessAudit.objects.select_related("actor", "owner").order_by("-occurred_at", "-pk")
    return render(request, "file_audit.html", {"page": Paginator(entries, 100).get_page(request.GET.get("page"))})
