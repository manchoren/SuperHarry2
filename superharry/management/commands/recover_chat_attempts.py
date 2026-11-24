from django.core.management.base import BaseCommand

from superharry.models import Message


class Command(BaseCommand):
    help = "Mark replies left running by the previous Django process as incomplete."

    def handle(self, *args, **options):
        Message.objects.filter(role=Message.Role.ASSISTANT, in_progress=True).update(
            in_progress=False, complete=False
        )
