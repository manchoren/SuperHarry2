from pathlib import Path

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import DatabaseError, transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .account_lifecycle import (
    _interrupt_account_occurrences,
    _pause_schedules,
    _request_skill_cancellations,
    _stop_chat_responses,
    pending_cancellations,
)
from .account_state import lock_account
from .accounts import _audit, account_manager_required, manageable_account_targets
from .models import (
    AccountAudit as AccountAuditModel,
    ChatInput,
    Conversation,
    CsvSummary,
    FileAccessAudit,
    SavedMemory,
    Schedule,
    SkillPreparation,
    SkillRun,
    UserProfile,
    VmSkill,
)


def _consequences(account):
    owned_skills = VmSkill.objects.filter(owner=account).order_by("name")
    skill_ids = owned_skills.values_list("pk", flat=True)
    shared_users = (
        SkillRun.objects.filter(skill_id__in=skill_ids)
        .exclude(owner=account)
        .values("owner_id")
        .distinct()
        .count()
    )
    return {
        "conversations": Conversation.objects.filter(owner=account).count(),
        "memories": SavedMemory.objects.filter(owner=account).count(),
        "schedules": Schedule.objects.filter(owner=account).count(),
        "uploads": ChatInput.objects.filter(owner=account).count(),
        "results": SkillRun.objects.filter(owner=account).count(),
        "skills": owned_skills.count(),
        "skill_names": list(owned_skills.values_list("name", flat=True)),
        "shared_skill_users": shared_users,
    }


def _owned_job_ids(account):
    return set(SkillRun.objects.filter(owner=account).values_list("job_id", flat=True)) | set(
        SkillPreparation.objects.filter(skill__owner=account).values_list("job_id", flat=True)
    )


def _remove_job_spool_files(account):
    from .skill_jobs import spool

    directory = spool()
    owned_jobs = _owned_job_ids(account)
    if owned_jobs and directory is None:
        raise OSError("Skill storage is unavailable; deletion remains pending.")
    for job_id in owned_jobs:
        for path in directory.glob(f"{job_id}.*"):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)

    shared_job_ids = set(
        SkillRun.objects.filter(skill__owner=account)
        .exclude(owner=account)
        .values_list("job_id", flat=True)
    )
    for job_id in shared_job_ids:
        if directory is None:
            raise OSError("Skill storage is unavailable; deletion remains pending.")
        for path in directory.glob(f"{job_id}.*"):
            suffix = path.name.removeprefix(f"{job_id}.")
            if suffix not in ("skill-result", "skill-artifacts") and (path.is_file() or path.is_symlink()):
                path.unlink(missing_ok=True)

    csv_jobs = list(
        CsvSummary.objects.filter(file__owner=account).values_list("job_id", flat=True)
    )
    csv_directory = Path(settings.CSV_SPOOL) if settings.CSV_SPOOL else None
    if csv_jobs and csv_directory is None:
        raise OSError("CSV result storage is unavailable; deletion remains pending.")
    if csv_jobs:
        for job_id in csv_jobs:
            for suffix in ("result", "input", "running"):
                (csv_directory / f"{job_id}.{suffix}").unlink(missing_ok=True)


def _invalidate_sessions(account_id):
    account_id = str(account_id)
    for session in Session.objects.all().iterator():
        if session.get_decoded().get("_auth_user_id") == account_id:
            session.delete()


def _delete_data(account, actor):
    target_username = account.get_username()
    AccountAuditModel.objects.filter(target=account).update(
        target_id_snapshot=account.pk,
        target_username=target_username,
    )
    AccountAuditModel.objects.filter(actor=account).update(
        actor_id_snapshot=account.pk,
        actor_username=target_username,
    )
    FileAccessAudit.objects.filter(owner=account).update(
        owner_id_snapshot=account.pk,
        owner_username=target_username,
        file_name="",
    )
    FileAccessAudit.objects.filter(actor=account).update(
        actor_id_snapshot=account.pk,
        actor_username=target_username,
        file_name="",
    )
    # A local skill is shared; preserve other users' results while removing
    # the private skill source and preventing any future dispatch.
    shared_runs = SkillRun.objects.filter(skill__owner=account).exclude(owner=account)
    for run in shared_runs:
        payload = dict(run.execution_payload or {})
        payload["files"] = {}
        payload["wheels"] = {}
        run.execution_payload = payload
        run.save(update_fields=("execution_payload",))
    _invalidate_sessions(account.pk)
    _audit(actor, account, "deleted")
    account.delete()


def _pending_response(request, account, status=202):
    return render(
        request,
        "accounts/deletion_pending.html",
        {"account": account, "pending": pending_cancellations(account.pk, include_shared_skills=True)},
        status=status,
    )


@never_cache
@account_manager_required
@require_http_methods(["GET", "POST"])
def delete_account(request, pk):
    account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
    form_error = ""
    if request.method == "GET":
        return render(
            request,
            "accounts/delete.html",
            {"account": account, "consequences": _consequences(account)},
        )
    if request.POST.get("confirmation", "") != account.get_username():
        form_error = "Type the account username exactly to confirm deletion."
        return render(
            request,
            "accounts/delete.html",
            {"account": account, "consequences": _consequences(account), "form_error": form_error},
            status=400,
        )

    try:
        identifiers = []
        with transaction.atomic():
            if not lock_account(account.pk):
                raise Http404
            account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
            profile, _ = UserProfile.objects.get_or_create(user=account)
            profile.deletion_pending = True
            profile.save(update_fields=("deletion_pending",))
            if account.is_active:
                account.is_active = False
                account.save(update_fields=("is_active",))
                now = timezone.now()
                _pause_schedules(account, now)
                _stop_chat_responses(account, now)
                _interrupt_account_occurrences(account)
                _audit(request.user, account, "deactivated")
            stop_confirmed = profile.deletion_stop_confirmed
            if not stop_confirmed:
                identifiers = _request_skill_cancellations(
                    account, include_shared_skills=True
                )

        from .skill_jobs import dispatch_lifecycle

        if not stop_confirmed:
            for identifier, is_preparation in identifiers:
                dispatch_lifecycle(identifier, preparation=is_preparation)
            if pending_cancellations(account.pk, include_shared_skills=True)["total"]:
                return _pending_response(request, account)
            if not UserProfile.objects.filter(user=account).update(deletion_stop_confirmed=True):
                raise DatabaseError("Unable to persist confirmed worker shutdown.")

        _remove_job_spool_files(account)
        try:
            with transaction.atomic():
                if not lock_account(account.pk):
                    raise Http404
                account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
                if not UserProfile.objects.filter(
                    user=account, deletion_pending=True, deletion_stop_confirmed=True
                ).exists():
                    return _pending_response(request, account)
                _delete_data(account, request.user)
        except DatabaseError:
            return HttpResponse(
                "Account data could not be fully removed. The inactive account is retained; retry deletion.",
                status=503,
            )
    except OSError:
        return HttpResponse(
            "Stored job data could not be fully removed. The inactive account is retained; retry deletion.",
            status=503,
        )
    except DatabaseError:
        return HttpResponse(
            "Account work could not be safely stopped. The account is retained; retry deletion.",
            status=503,
        )
    return redirect("account-list")
