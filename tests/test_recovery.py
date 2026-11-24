import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase


class RecoverySignInTests(TestCase):
    def test_provisioned_owner_can_sign_in_to_admin(self):
        password = "test-only-recovery-password"
        with TemporaryDirectory() as directory:
            secret = Path(directory) / "password"
            secret.write_text(password, encoding="utf-8")
            with patch.dict(os.environ, {"RECOVERY_PASSWORD_FILE": str(secret)}):
                call_command("bootstrap_recovery")

        browser = Client(enforce_csrf_checks=True)
        login = browser.get("/admin/login/")
        response = browser.post(
            "/admin/login/",
            {
                "username": "recovery",
                "password": password,
                "csrfmiddlewaretoken": login.cookies["csrftoken"].value,
                "next": "/admin/",
            },
        )
        self.assertRedirects(response, "/admin/")
        self.assertContains(browser.get("/admin/"), "Site administration")
