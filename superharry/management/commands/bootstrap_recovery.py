import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create the recovery superuser from a deployment secret."

    def handle(self, *args, **options):
        users = get_user_model().objects
        existing = users.filter(username="recovery").first()
        if existing is not None:
            if not (existing.is_active and existing.is_staff and existing.is_superuser):
                raise CommandError("The recovery username belongs to an unusable account.")
            return

        secret_file = os.environ.get("RECOVERY_PASSWORD_FILE")
        if not secret_file:
            raise CommandError("Set RECOVERY_PASSWORD_FILE to the recovery password file.")
        try:
            password = Path(secret_file).read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeError):
            raise CommandError("The recovery password file could not be read.") from None
        if not password.strip():
            raise CommandError("The recovery password file is empty.")

        users.create_superuser(username="recovery", password=password)
