from django.core.management.base import BaseCommand
from django.db import transaction

from superharry.models import ChatSubmission, Message


class Command(BaseCommand):
    help = "Mark replies left running by the previous Django process as incomplete."

    def handle(self, *args, **options):
        with transaction.atomic():
            Message.objects.filter(role=Message.Role.ASSISTANT, in_progress=True).update(
                in_progress=False, complete=False
            )
            ChatSubmission.objects.filter(active=True).update(active=False)
