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
from django.utils import timezone

from .chat import stream_message
from .models import ChatRoute, ChatSubmission, Message, Schedule, ScheduleOccurrence
from .quota import AdmissionDenied, admit
from .text_skills import text_skill


def occurrence_key(occurrence):
    return uuid5(NAMESPACE_URL, f"superharry:schedule-occurrence:{occurrence.pk}")


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
    for occurrence in ScheduleOccurrence.objects.filter(status__in=("claimed", "running")).select_related("schedule", "reply"):
        with transaction.atomic():
            submission = ChatSubmission.objects.filter(owner=occurrence.schedule.owner, key=occurrence_key(occurrence)).first()
            if submission is None and occurrence.reply_id:
                submission = ChatSubmission.objects.filter(reply_id=occurrence.reply_id).first()
            if submission is None and ChatSubmission.objects.filter(owner=occurrence.schedule.owner, active=True).exists():
                continue
            reply = submission.reply if submission else occurrence.reply
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


def run_due():
    if not settings.OMNIROUTE_URL or not settings.OMNIROUTE_API_KEY:
        return
    with scheduler_lock() as acquired:
        if acquired:
            recover_occurrences()
            _run_due()


def _run_due():
    now = timezone.now()
    for schedule_id in Schedule.objects.filter(next_run__lte=now).values_list("pk", flat=True):
        with transaction.atomic():
            schedule = Schedule.objects.filter(pk=schedule_id, next_run__lte=now).first()
            if schedule is None:
                continue
            occurrence, created = ScheduleOccurrence.objects.get_or_create(
                schedule=schedule, due_at=schedule.next_run,
            )
            if not created:
                continue
            schedule.next_run = None
            schedule.save(update_fields=("next_run",))

        if now > occurrence.due_at + timedelta(hours=1):
            occurrence.status = "missed"
            occurrence.reply = schedule.conversation.messages.create(
                role=Message.Role.ASSISTANT, content="Scheduled task missed: it could not start within one hour."
            )
            occurrence.save(update_fields=("reply", "status"))
            continue

        try:
            skill = text_skill(schedule.prompt)
        except ValueError as failure:
            occurrence.reply = schedule.conversation.messages.create(
                role=Message.Role.ASSISTANT, content=f"Scheduled task failed: {failure}",
            )
            occurrence.status = "failed"
            occurrence.save(update_fields=("reply", "status"))
            continue

        def create_reply():
            if not Schedule.objects.filter(pk=schedule.pk, canceled=False, owner_id=schedule.conversation.owner_id).exists():
                raise AdmissionDenied("The schedule is canceled or unavailable.", 409)
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
            else:
                occurrence.status = "skipped" if failure.status == 429 else "failed"
                occurrence.reply = schedule.conversation.messages.create(
                    role=Message.Role.ASSISTANT,
                    content=f"Scheduled task {occurrence.status}: {failure}",
                )
                occurrence.save(update_fields=("reply", "status"))
            continue
        occurrence.reply = submission.reply
        occurrence.status = "running"
        occurrence.save(update_fields=("reply", "status"))
        for _ in stream_message(submission, schedule.preferred_model or ChatRoute.active_id(), limit, skill, scheduled=True):
            pass
        submission.reply.refresh_from_db()
        occurrence.status = "complete" if submission.reply.complete else "interrupted"
        occurrence.save(update_fields=("status",))
