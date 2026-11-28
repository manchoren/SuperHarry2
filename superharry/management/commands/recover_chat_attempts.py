from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from superharry.models import ChatSubmission, Message, ScheduleOccurrence
from superharry.scheduler import occurrence_key


class Command(BaseCommand):
    help = "Mark replies left running by the previous Django process as incomplete."

    def handle(self, *args, **options):
        with transaction.atomic():
            scheduled = list(ScheduleOccurrence.objects.filter(status__in=("claimed", "running")))
            keys = [occurrence_key(occurrence) for occurrence in scheduled]
            reply_ids = [occurrence.reply_id for occurrence in scheduled if occurrence.reply_id]
            reply_ids += list(ChatSubmission.objects.filter(key__in=keys).exclude(reply_id=None).values_list("reply_id", flat=True))
            Message.objects.filter(role=Message.Role.ASSISTANT, in_progress=True).exclude(pk__in=reply_ids).update(
                in_progress=False, complete=False
            )
            ChatSubmission.objects.filter(active=True).exclude(key__in=keys).exclude(reply_id__in=reply_ids).update(active=False)
            ChatSubmission.objects.filter(
                cancel_requested_at__isnull=False,
                cancellation_confirmed_at__isnull=True,
            ).update(active=False, cancellation_confirmed_at=timezone.now())
