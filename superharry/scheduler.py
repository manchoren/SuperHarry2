"""Execute due scheduled chat submissions."""

from hashlib import sha256
from datetime import timedelta
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir
from uuid import NAMESPACE_URL, uuid5
import os
import errno

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .chat import ChatOutcome, execute_message
from .gateway import GatewayFailure, available_routes
from .models import ChatInput, ChatLimits, ChatRoute, ChatSubmission, Message, Schedule, ScheduleOccurrence, UserChatLimits
from .quota import AdmissionDenied, admit
from .schedule_time import next_occurrence
from .text_skills import text_skill


def occurrence_key(occurrence):
    return uuid5(NAMESPACE_URL, f"superharry:schedule-occurrence:{occurrence.pk}")


def advance_schedule(occurrence):
    schedule = occurrence.schedule
    if schedule.repetition not in ("daily", "weekly") or schedule.wall_time is None:
        return
    following = next_occurrence(occurrence.due_at, schedule.wall_time, schedule.timezone,
                                schedule.repetition, schedule.weekday)
    Schedule.objects.filter(pk=schedule.pk, canceled=False, paused_at__isnull=True, next_run__isnull=True).update(next_run=following)


def mark_missed(occurrence):
    occurrence.status = "missed"
    occurrence.reply = occurrence.schedule.conversation.messages.create(
        role=Message.Role.ASSISTANT, content="Scheduled task missed: it could not start within one hour."
    )
    occurrence.save(update_fields=("reply", "status"))
    advance_schedule(occurrence)


@contextmanager
def scheduler_lock():
    name = str(settings.DATABASES["default"]["NAME"])
    if name == ":memory:" or name.startswith("file:"):
        name = str(Path(gettempdir()) / ("superharry-" + sha256(name.encode()).hexdigest()))
    with open(name + ".scheduler.lock", "a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as failure:
            if failure.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        try:
            yield True
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def recover_occurrences():
    from .skill_jobs import result, run_result, spool
    def append_result(occurrence, text):
        if occurrence.reply:
            maximum = ChatLimits.objects.get(pk=1).max_response_characters
            override = UserChatLimits.objects.filter(user_id=occurrence.schedule.owner_id).first()
            if override and override.max_response_characters is not None:
                maximum = override.max_response_characters
            remaining = max(0, maximum - len(occurrence.reply.content))
            occurrence.reply.content += ("\n" + text)[:remaining]
            occurrence.reply.completed_at = timezone.now()
            occurrence.reply.save(update_fields=("content", "completed_at"))

    for occurrence in ScheduleOccurrence.objects.filter(
        status="awaiting_preparation", schedule__paused_at__isnull=True, schedule__owner__is_active=True,
    ).select_related("reply"):
        outcome = result(spool(), occurrence.preparation_job) if occurrence.preparation_job else {"status": "failed"}
        if outcome is None:
            continue
        success = outcome.get("status") == "complete"
        text = "Skill preparation completed." if success else "Skill preparation failed or timed out."
        with transaction.atomic():
            append_result(occurrence, text)
            occurrence.status = "complete" if success else "failed"
            occurrence.failure_reason = "" if success else text
            occurrence.save(update_fields=("status", "failure_reason"))
    for occurrence in ScheduleOccurrence.objects.filter(
        status="awaiting_skill", schedule__paused_at__isnull=True, schedule__owner__is_active=True,
    ).select_related("skill_run", "reply"):
        outcome = run_result(spool(), occurrence.skill_run) if occurrence.skill_run else {"status": "failed"}
        if outcome is None:
            continue
        success = outcome.get("status") == "complete"
        text = outcome.get("output", "") if success else "Skill execution failed or timed out."
        if not success and isinstance(outcome.get("message"), str) and outcome["message"]:
            text += " " + outcome["message"]
        if outcome.get("reason") == "skill_deleted":
            text = "Skill deleted; execution stopped."
        elif outcome.get("reason") == "account_deactivated":
            text = "Account deactivated; skill execution stopped."
            if outcome.get("external_action_uncertain"):
                text += " An external action may already have completed and was not undone."
        elif outcome.get("reason") == "skill_changed_external":
            text = "Skill updated; execution stopped. External action was not repeated."
        elif outcome.get("reason") == "skill_changed_uncertain":
            text = "Skill updated; execution stopped because prior external activity is unknown. The run was not automatically repeated."
        if not isinstance(text, str):
            text = "Skill execution returned an invalid result."
            success = False
        with transaction.atomic():
            append_result(occurrence, text)
            occurrence.status = "complete" if success else "failed"
            occurrence.failure_reason = "" if success else text
            occurrence.save(update_fields=("status", "failure_reason"))
    for occurrence in ScheduleOccurrence.objects.filter(
        status__in=("claimed", "running"), schedule__paused_at__isnull=True, schedule__owner__is_active=True,
    ).select_related("schedule", "reply"):
        with transaction.atomic():
            submission = ChatSubmission.objects.filter(owner=occurrence.schedule.owner, key=occurrence_key(occurrence)).first()
            if submission is None and occurrence.reply_id:
                submission = ChatSubmission.objects.filter(reply_id=occurrence.reply_id).first()
            if submission is None and ChatSubmission.objects.filter(owner=occurrence.schedule.owner, active=True).exists():
                continue
            reply = submission.reply if submission else occurrence.reply
            if occurrence.skill_run_id or occurrence.preparation_job:
                occurrence.status = "awaiting_skill" if occurrence.skill_run_id else "awaiting_preparation"
                occurrence.save(update_fields=("status",))
                if reply:
                    reply.in_progress = False
                    reply.complete = True
                    reply.completed_at = timezone.now()
                    reply.save(update_fields=("in_progress", "complete", "completed_at"))
                if submission:
                    submission.active = False
                    submission.save(update_fields=("active",))
                advance_schedule(occurrence)
                continue
            if reply and (reply.complete or submission is None or submission.submitted_day):
                occurrence.reply = reply
                occurrence.status = "complete" if reply.complete else "interrupted"
                occurrence.save(update_fields=("reply", "status"))
                if not reply.complete:
                    reply.in_progress = False
                    reply.save(update_fields=("in_progress",))
                if submission:
                    submission.active = False
                    submission.save(update_fields=("active",))
                advance_schedule(occurrence)
                continue
            if submission:
                submission.delete()
            if reply:
                prompt = reply.prompt
                reply.delete()
                if prompt:
                    prompt.delete()
            if not occurrence.schedule.canceled:
                Schedule.objects.filter(pk=occurrence.schedule_id, next_run__isnull=True).update(next_run=occurrence.due_at)
            occurrence.delete()
    for schedule in Schedule.objects.filter(
        canceled=False, paused_at__isnull=True, owner__is_active=True,
        next_run__isnull=True, repetition__in=("daily", "weekly"),
    ):
        occurrence = schedule.occurrences.order_by("-due_at").first()
        if occurrence and occurrence.status not in ("claimed", "running"):
            advance_schedule(occurrence)


def run_due():
    with scheduler_lock() as acquired:
        if acquired:
            from .models import SkillRun
            from .skill_jobs import discard_orphan_artifacts, run_result, spool
            directory = spool()
            if directory is not None:
                for run in SkillRun.objects.filter(artifacts_imported=False).iterator():
                    run_result(directory, run)
                discard_orphan_artifacts(directory)
            recover_occurrences()
            if settings.OMNIROUTE_URL and settings.OMNIROUTE_API_KEY:
                _run_due()


def _run_due():
    now = timezone.now()
    for schedule_id in Schedule.objects.filter(
        next_run__lte=now, paused_at__isnull=True, owner__is_active=True,
    ).values_list("pk", flat=True):
        with transaction.atomic():
            if not Schedule.objects.filter(
                pk=schedule_id, next_run__lte=now, paused_at__isnull=True, owner__is_active=True,
            ).update(owner_id=F("owner_id")):
                continue
            schedule = Schedule.objects.filter(
                pk=schedule_id, next_run__lte=now, paused_at__isnull=True, owner__is_active=True,
            ).first()
            if schedule is None:
                continue
            occurrence, created = ScheduleOccurrence.objects.get_or_create(
                schedule=schedule, due_at=schedule.next_run,
            )
            if not created:
                continue
            schedule.next_run = None
            schedule.save(update_fields=("next_run",))

        if timezone.now() > occurrence.due_at + timedelta(hours=1):
            mark_missed(occurrence)
            continue

        try:
            route = schedule.preferred_model or ChatRoute.active_id()
            if not route or route not in available_routes():
                raise ValueError("The preferred model is unavailable.")
        except (GatewayFailure, ValueError) as failure:
            occurrence.reply = schedule.conversation.messages.create(
                role=Message.Role.ASSISTANT, content=f"Scheduled task failed: {failure}",
            )
            occurrence.status = "failed"
            occurrence.save(update_fields=("reply", "status"))
            advance_schedule(occurrence)
            continue

        try:
            skill = text_skill(schedule.prompt)
        except ValueError as failure:
            occurrence.reply = schedule.conversation.messages.create(
                role=Message.Role.ASSISTANT, content=f"Scheduled task failed: {failure}",
            )
            occurrence.status = "failed"
            occurrence.save(update_fields=("reply", "status"))
            advance_schedule(occurrence)
            continue

        selected = {item.pk: item for item in ChatInput.objects.filter(owner=schedule.owner, pk__in=schedule.input_ids)}
        missing = next((item for item in schedule.input_ids if item not in selected), None)
        if missing is not None:
            occurrence.reply = schedule.conversation.messages.create(
                role=Message.Role.ASSISTANT, content=f"Scheduled task failed: input #{missing} is missing or inaccessible.",
            )
            occurrence.status = "failed"
            occurrence.save(update_fields=("reply", "status"))
            advance_schedule(occurrence)
            continue

        if timezone.now() > occurrence.due_at + timedelta(hours=1):
            mark_missed(occurrence)
            continue

        def create_reply():
            if not Schedule.objects.filter(
                pk=schedule.pk,
                canceled=False,
                paused_at__isnull=True,
                owner_id=schedule.conversation.owner_id,
                owner__is_active=True,
            ).exists():
                raise AdmissionDenied("The schedule is canceled or unavailable.", 409)
            if timezone.now() > occurrence.due_at + timedelta(hours=1):
                raise AdmissionDenied("The scheduled start window expired.", 410)
            prompt = schedule.conversation.messages.create(role=Message.Role.USER, content=schedule.prompt)
            return schedule.conversation.messages.create(
                prompt=prompt, role=Message.Role.ASSISTANT, content="",
                complete=False, in_progress=True,
            )

        try:
            submission, limit = admit(schedule.owner, occurrence_key(occurrence), sha256(str(occurrence.pk).encode()).hexdigest(), create_reply)
        except AdmissionDenied as failure:
            if failure.status == 409:
                with transaction.atomic():
                    Schedule.objects.filter(pk=schedule.pk, next_run__isnull=True, canceled=False).update(next_run=occurrence.due_at)
                    occurrence.delete()
            elif failure.status == 410:
                mark_missed(occurrence)
            else:
                occurrence.status = "skipped" if failure.status in (403, 429) else "failed"
                if failure.status != 403:
                    occurrence.reply = schedule.conversation.messages.create(
                        role=Message.Role.ASSISTANT,
                        content=f"Scheduled task {occurrence.status}: {failure}",
                    )
                    occurrence.save(update_fields=("reply", "status"))
                else:
                    occurrence.failure_reason = "The owner account became inactive before this task started."
                    occurrence.save(update_fields=("failure_reason", "status"))
                advance_schedule(occurrence)
            continue
        occurrence.reply = submission.reply
        occurrence.status = "running"
        occurrence.save(update_fields=("reply", "status"))
        outcome = None
        for event in execute_message(submission, route, limit, skill, scheduled=True, inputs=[selected[item] for item in schedule.input_ids]):
            if isinstance(event, ChatOutcome):
                outcome = event
        occurrence.status = outcome.status
        occurrence.failure_reason = outcome.error if outcome.status == "failed" else ""
        occurrence.save(update_fields=("status", "failure_reason"))
        advance_schedule(occurrence)
