from django.core.management.base import BaseCommand

from superharry.scheduler import run_due


class Command(BaseCommand):
    help = "Run due scheduled chat work."

    def handle(self, *args, **options):
        run_due()
