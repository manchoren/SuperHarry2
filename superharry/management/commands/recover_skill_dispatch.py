from uuid import UUID

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from superharry.skill_jobs import dispatch_pending, resolve_uncertain


class Command(BaseCommand):
    help = "Recover committed skill jobs that have not reached the worker spool."

    def add_arguments(self, parser):
        parser.add_argument("--mark-uncertain-failed", metavar="JOB_ID")
        parser.add_argument("--confirm-no-running-container", action="store_true")

    def handle(self, *args, **options):
        if options["mark_uncertain_failed"]:
            if not options["confirm_no_running_container"]:
                raise CommandError("Inspect the job container and confirm it is absent before resolving uncertainty.")
            try:
                resolve_uncertain(UUID(options["mark_uncertain_failed"]))
            except (ValueError, OSError, DatabaseError) as error:
                raise CommandError(str(error)) from error
            return
        dispatch_pending()
