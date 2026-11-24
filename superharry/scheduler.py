"""Execute due scheduled chat submissions."""

from hashlib import sha256
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .chat import stream_message
from .models import ChatRoute, Message, Schedule, ScheduleOccurrence
from .quota import AdmissionDenied, admit


def run_due():
    if not settings.OMNIROUTE_URL or not settings.OMNIROUTE_API_KEY:
        return
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
            occurrence.save(update_fields=("status",))
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
            submission, limit = admit(schedule.owner, uuid4(), sha256(str(occurrence.pk).encode()).hexdigest(), create_reply)
        except AdmissionDenied as failure:
            if failure.status == 409:
                with transaction.atomic():
                    Schedule.objects.filter(pk=schedule.pk, next_run__isnull=True, canceled=False).update(next_run=occurrence.due_at)
                    occurrence.delete()
            else:
                occurrence.status = "skipped" if failure.status == 429 else "failed"
                occurrence.save(update_fields=("status",))
            continue
        occurrence.reply = submission.reply
        occurrence.status = "running"
        occurrence.save(update_fields=("reply", "status"))
        for _ in stream_message(submission, schedule.preferred_model or ChatRoute.active_id(), limit, None, scheduled=True):
            pass
        submission.reply.refresh_from_db()
        occurrence.status = "complete" if submission.reply.complete else "interrupted"
        occurrence.save(update_fields=("status",))
