from django.db import OperationalError, transaction
from django.db.models import F, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .account_state import lock_account
from .accounts import _audit, account_manager_required, manageable_account_targets
from .models import (
    ChatSubmission,
    Message,
    Schedule,
    ScheduleOccurrence,
    SkillOutbox,
    SkillPreparation,
    SkillRun,
    UserProfile,
)


ACTIVE_OCCURRENCES = ("claimed", "running", "awaiting_preparation", "awaiting_skill")


def _due_for_pause(schedule):
    occurrence_due = schedule.occurrences.filter(status__in=ACTIVE_OCCURRENCES).order_by("due_at").values_list(
        "due_at", flat=True
    ).first()
    return occurrence_due or schedule.next_run


def _pause_schedules(account, now):
    schedules = Schedule.objects.filter(owner=account, canceled=False, paused_at__isnull=True)
    paused = 0
    for schedule_id in schedules.values_list("pk", flat=True):
        # The no-op write serializes this transition with the scheduler on SQLite.
        if not Schedule.objects.filter(pk=schedule_id, paused_at__isnull=True).update(
            owner_id=F("owner_id"),
        ):
            continue
        schedule = Schedule.objects.filter(pk=schedule_id, paused_at__isnull=True).first()
        if schedule is None:
            continue
        due = _due_for_pause(schedule)
        if due is None:
            continue
        Schedule.objects.filter(pk=schedule.pk, paused_at__isnull=True).update(
            paused_at=now, paused_next_run=due, next_run=None,
        )
        paused += 1
    return paused


def _interrupt_account_occurrences(account):
    occurrences = ScheduleOccurrence.objects.filter(
        schedule__owner=account, schedule__paused_at__isnull=False,
        status__in=ACTIVE_OCCURRENCES,
    ).select_related("reply")
    for occurrence in occurrences:
        if occurrence.reply_id:
            Message.objects.filter(pk=occurrence.reply_id, in_progress=True).update(
                in_progress=False, complete=False, completed_at=None,
            )
        occurrence.status = "interrupted"
        occurrence.failure_reason = "Execution interrupted because the account was deactivated."
        occurrence.save(update_fields=("status", "failure_reason"))


def _stop_chat_responses(account, now):
    submissions = ChatSubmission.objects.filter(owner=account, active=True).select_related("reply")
    for submission in submissions:
        if submission.cancel_requested_at is None:
            submission.cancel_requested_at = now
        submission.active = False
        submission.save(update_fields=("active", "cancel_requested_at"))
        if submission.reply_id:
            Message.objects.filter(pk=submission.reply_id, in_progress=True).update(
                in_progress=False, complete=False, completed_at=None,
            )


def _worker_result(directory, job_id):
    from .skill_jobs import result

    return result(directory, job_id)


def _request_skill_cancellations(account, *, include_shared_skills=False):
    from .skill_jobs import spool

    directory = spool()
    identifiers = []
    runs = SkillRun.objects.filter(owner=account)
    if include_shared_skills:
        runs = SkillRun.objects.filter(Q(owner=account) | Q(skill__owner=account)).distinct()
    for run in runs:
        outcome = _worker_result(directory, run.job_id)
        if outcome and outcome.get("generation", 0) >= run.lifecycle_generation:
            continue
        fields = []
        if run.lifecycle != "cancelled":
            run.lifecycle = "cancelled"
            run.lifecycle_generation += 1
            fields.extend(("lifecycle", "lifecycle_generation"))
        uncertainty = run.lifecycle_uncertain or bool(
            directory and (directory / f"{run.job_id}.skill-external").exists()
        )
        if uncertainty != run.lifecycle_uncertain:
            run.lifecycle_uncertain = uncertainty
            fields.append("lifecycle_uncertain")
        if fields:
            run.save(update_fields=fields)
        SkillOutbox.objects.filter(job_id=run.job_id).delete()
        identifiers.append((run.job_id, False))

    preparations = SkillPreparation.objects.filter(skill__owner=account).select_related("skill")
    for preparation in preparations:
        outcome = _worker_result(directory, preparation.job_id)
        if outcome and outcome.get("generation", 0) >= preparation.lifecycle_generation:
            continue
        if preparation.lifecycle != "cancelled":
            preparation.lifecycle = "cancelled"
            preparation.lifecycle_generation += 1
            preparation.save(update_fields=("lifecycle", "lifecycle_generation"))
        SkillOutbox.objects.filter(job_id=preparation.job_id).delete()
        identifiers.append((preparation.job_id, True))

    return identifiers


def pending_cancellations(account_id, *, include_shared_skills=False):
    from .skill_jobs import result, spool

    pending_chat = ChatSubmission.objects.filter(
        owner_id=account_id,
        cancel_requested_at__isnull=False,
        cancellation_confirmed_at__isnull=True,
    ).count()
    directory = spool()
    pending_skills = 0
    runs = SkillRun.objects.filter(owner_id=account_id, lifecycle="cancelled")
    if include_shared_skills:
        runs = SkillRun.objects.filter(
            Q(owner_id=account_id) | Q(skill__owner_id=account_id), lifecycle="cancelled"
        ).distinct()
    preparations = SkillPreparation.objects.filter(skill__owner_id=account_id, lifecycle="cancelled")
    for job in list(runs) + list(preparations):
        outcome = result(directory, job.job_id)
        if not outcome or outcome.get("generation", 0) < job.lifecycle_generation:
            pending_skills += 1
    return {"chat": pending_chat, "skills": pending_skills, "total": pending_chat + pending_skills}


def _resume_schedules(account, now):
    from .schedule_time import next_occurrence

    resumed = 0
    schedules = Schedule.objects.filter(owner=account, canceled=False, paused_at__isnull=False)
    for schedule in schedules.select_related("conversation"):
        due = schedule.paused_next_run
        next_run = due
        while next_run is not None and next_run <= now:
            occurrence, _ = ScheduleOccurrence.objects.get_or_create(
                schedule=schedule,
                due_at=next_run,
                defaults={"status": "skipped"},
            )
            if occurrence.status not in ("complete", "failed", "needs_user_input"):
                occurrence.status = "skipped"
                occurrence.failure_reason = "Occurrence skipped while the account was inactive."
                occurrence.save(update_fields=("status", "failure_reason"))
            if schedule.repetition not in ("daily", "weekly") or schedule.wall_time is None:
                next_run = None
            else:
                next_run = next_occurrence(
                    next_run,
                    schedule.wall_time,
                    schedule.timezone,
                    schedule.repetition,
                    schedule.weekday,
                )
        Schedule.objects.filter(pk=schedule.pk, paused_at__isnull=False).update(
            paused_at=None, paused_next_run=None, next_run=next_run,
        )
        resumed += 1
    return resumed


def _lifecycle_result(request, account, operation):
    status = pending_cancellations(account.pk)
    schedule_count = getattr(
        account,
        "_paused_schedule_count" if operation == "deactivated" else "_resumed_schedule_count",
        0,
    )
    return render(
        request,
        "accounts/lifecycle_result.html",
        {"account": account, "operation": operation, "pending": status, "schedule_count": schedule_count},
        status=202 if status["total"] else 200,
    )


@never_cache
@account_manager_required
@require_POST
def deactivate_account(request, pk):
    account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
    try:
        with transaction.atomic():
            if not lock_account(account.pk):
                raise Http404
            account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
            if not account.is_active:
                return HttpResponse("This account is already inactive.", status=409)
            now = timezone.now()
            account.is_active = False
            account.save(update_fields=("is_active",))
            paused = _pause_schedules(account, now)
            _stop_chat_responses(account, now)
            _interrupt_account_occurrences(account)
            _audit(request.user, account, "deactivated")
            identifiers = _request_skill_cancellations(account)
    except OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            return HttpResponse("Account work is busy; the account was not deactivated. Retry shortly.", status=503)
        raise
    from .skill_jobs import dispatch_lifecycle
    for identifier, is_preparation in identifiers:
        dispatch_lifecycle(identifier, preparation=is_preparation)
    account._paused_schedule_count = paused
    return _lifecycle_result(request, account, "deactivated")


@never_cache
@account_manager_required
@require_POST
def reactivate_account(request, pk):
    account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
    if UserProfile.objects.filter(user=account, deletion_pending=True).exists():
        return HttpResponse("Account deletion is pending; it cannot be reactivated.", status=409)
    try:
        with transaction.atomic():
            if not lock_account(account.pk):
                raise Http404
            account = get_object_or_404(manageable_account_targets(request.user), pk=pk)
            if account.is_active:
                return HttpResponse("This account is already active.", status=409)
            now = timezone.now()
            account.is_active = True
            account.save(update_fields=("is_active",))
            resumed = _resume_schedules(account, now)
            _audit(request.user, account, "reactivated")
    except OperationalError as error:
        if "locked" in str(error).lower() or "busy" in str(error).lower():
            return HttpResponse("Account work is busy; the account was not reactivated. Retry shortly.", status=503)
        raise
    account._resumed_schedule_count = resumed
    return _lifecycle_result(request, account, "reactivated")
