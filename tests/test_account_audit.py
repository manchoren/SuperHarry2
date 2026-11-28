from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from superharry.models import AccountAudit, FileAccessAudit


class AccountAuditViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_superuser(username="audit-owner", password="CHANGE_ME")
        cls.delegate = users.create_user(
            username="audit-delegate", password="CHANGE_ME", is_staff=True
        )
        cls.regular = users.create_user(username="audit-regular", password="CHANGE_ME")
        cls.administrator = users.create_user(
            username="audit-administrator", password="CHANGE_ME", is_staff=True
        )
        cls.admin_group, _ = Group.objects.get_or_create(name="Administrators")
        cls.delegate.groups.add(cls.admin_group)
        cls.administrator.groups.add(cls.admin_group)

    def create_event(self, *, actor, target, action, privileged):
        return AccountAudit.objects.create(
            actor=actor,
            target=target,
            actor_id_snapshot=actor.pk if actor else None,
            actor_username=actor.username if actor else "removed-actor",
            target_id_snapshot=target.pk if target else 987654,
            target_username=target.username if target else "removed-target",
            actor_is_privileged=bool(actor and (actor.is_staff or actor.is_superuser)),
            target_is_privileged=privileged,
            action=action,
            occurred_at=datetime(2026, 11, 27, 12, 30, tzinfo=timezone.utc),
        )

    def test_audit_access_is_manager_only_and_delegate_rows_use_classification(self):
        self.create_event(
            actor=self.delegate, target=self.regular, action="created", privileged=False
        )
        self.create_event(
            actor=self.owner, target=self.administrator, action="password_reset", privileged=True
        )
        url = reverse("account-audit")
        self.assertRedirects(self.client.get(url), f"/login/?next={url}")

        regular_client = self.client_class()
        regular_client.force_login(self.regular)
        self.assertEqual(regular_client.get(url).status_code, 403)

        self.client.force_login(self.delegate)
        delegate_response = self.client.get(url)
        self.assertEqual(delegate_response.status_code, 200)
        self.assertContains(delegate_response, "audit-delegate")
        self.assertContains(delegate_response, "audit-regular")
        self.assertContains(delegate_response, "Created")
        self.assertContains(delegate_response, "Regular")
        self.assertNotContains(delegate_response, "audit-administrator")
        self.assertNotContains(delegate_response, "Password reset")
        self.assertEqual(len(delegate_response.context["events"]), 1)

        self.client.force_login(self.owner)
        owner_response = self.client.get(url)
        self.assertContains(owner_response, "audit-administrator")
        self.assertContains(owner_response, "Administrator")
        self.assertContains(owner_response, "Password reset")

    def test_promotion_and_demotion_change_prior_event_visibility(self):
        prior_event = self.create_event(
            actor=self.delegate, target=self.regular, action="edited", privileged=False
        )
        self.client.force_login(self.delegate)
        self.assertContains(self.client.get(reverse("account-audit")), "audit-regular")

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("account-edit", args=(self.regular.pk,)),
            {"username": self.regular.username, "administrators_group": "on"},
        )
        self.assertEqual(response.status_code, 302)
        prior_event.refresh_from_db()
        self.assertTrue(prior_event.target_is_privileged)
        self.client.force_login(self.delegate)
        self.assertNotContains(self.client.get(reverse("account-audit")), "audit-regular")

        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("account-edit", args=(self.regular.pk,)),
            {"username": self.regular.username, "administrators_group": ""},
        )
        self.assertEqual(response.status_code, 302)
        prior_event.refresh_from_db()
        self.assertFalse(prior_event.target_is_privileged)
        self.client.force_login(self.delegate)
        self.assertContains(self.client.get(reverse("account-audit")), "audit-regular")

    def test_deleted_regular_target_keeps_identity_and_actor_snapshot(self):
        event = self.create_event(
            actor=self.administrator, target=self.regular, action="edited", privileged=False
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("account-delete", args=(self.regular.pk,)),
            {"confirmation": self.regular.username},
        )
        self.assertEqual(response.status_code, 302)
        self.client.post(
            reverse("account-delete", args=(self.administrator.pk,)),
            {"confirmation": self.administrator.username},
        )
        event.refresh_from_db()
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.actor_id_snapshot, self.administrator.pk)
        self.assertEqual(event.actor_username, "audit-administrator")
        self.assertIsNone(event.target)
        self.assertEqual(event.target_id_snapshot, self.regular.pk)
        self.assertEqual(event.target_username, "audit-regular")
        self.assertFalse(event.target_is_privileged)

        self.client.force_login(self.delegate)
        response = self.client.get(reverse("account-audit"))
        self.assertContains(response, "audit-administrator")
        self.assertContains(response, "audit-regular")
        self.assertContains(response, "Edited")

    def test_deleted_promoted_target_keeps_final_privileged_classification(self):
        self.create_event(
            actor=self.delegate, target=self.regular, action="created", privileged=False
        )
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("account-edit", args=(self.regular.pk,)),
            {"username": self.regular.username, "administrators_group": "on"},
        )
        self.assertEqual(response.status_code, 302)
        response = self.client.post(
            reverse("account-delete", args=(self.regular.pk,)),
            {"confirmation": self.regular.username},
        )
        self.assertEqual(response.status_code, 302)
        events = AccountAudit.objects.filter(target_id_snapshot=self.regular.pk)
        self.assertEqual(events.count(), 4)
        self.assertTrue(all(event.target_is_privileged for event in events))

        self.client.force_login(self.delegate)
        self.assertNotContains(self.client.get(reverse("account-audit")), "audit-regular")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("account-audit"))
        self.assertContains(response, "audit-regular")
        self.assertContains(response, "Administrator")
        self.assertContains(response, "Deleted")

    def test_real_lifecycle_actions_appear_without_credentials(self):
        self.client.force_login(self.delegate)
        with patch("superharry.accounts.validate_password"):
            response = self.client.post(
                reverse("account-create"),
                {
                    "username": "audit-action-target",
                    "password": "CHANGE_ME",
                    "must_change_password": "on",
                },
            )
        target = get_user_model().objects.get(username="audit-action-target")
        self.assertEqual(response.status_code, 302)
        self.client.post(
            reverse("account-edit", args=(target.pk,)),
            {"username": target.username, "first_name": "Updated"},
        )
        reset_password = "Audit-Reset-Fresh-Password-9137!"
        self.client.post(
            reverse("account-password-reset", args=(target.pk,)),
            {
                "new_password1": reset_password,
                "new_password2": reset_password,
                "must_change_password": "on",
            },
        )
        self.client.post(reverse("account-deactivate", args=(target.pk,)))
        self.client.post(reverse("account-reactivate", args=(target.pk,)))
        self.client.post(
            reverse("account-delete", args=(target.pk,)),
            {"confirmation": target.username},
        )

        actions = set(
            AccountAudit.objects.filter(target_id_snapshot=target.pk).values_list(
                "action", flat=True
            )
        )
        self.assertEqual(
            actions,
            {"created", "edited", "password_reset", "deactivated", "reactivated", "deleted"},
        )
        response = self.client.get(reverse("account-audit"))
        for label in ("Created", "Edited", "Password reset", "Deactivated", "Reactivated", "Deleted"):
            self.assertContains(response, label)
        self.assertNotContains(response, reset_password)


class AccountAuditClassificationMigrationTests(TransactionTestCase):
    migrate_from = ("superharry", "0041_account_deletion_and_audit_snapshots")
    migrate_to = ("superharry", "0042_account_audit_final_classification")

    def setUp(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        old_apps = self.executor.loader.project_state([self.migrate_from]).apps
        user_app_label, user_model_name = get_user_model()._meta.label.split(".")
        User = old_apps.get_model(user_app_label, user_model_name)
        AccountAuditOld = old_apps.get_model("superharry", "AccountAudit")
        Group = old_apps.get_model("auth", "Group")

        self.actor = User.objects.create(username="migration-actor", is_staff=True)
        regular = User.objects.create(username="migration-regular")
        promoted = User.objects.create(username="migration-promoted", is_staff=True)
        admin_group, _ = Group.objects.get_or_create(name="Administrators")
        promoted.groups.add(admin_group)
        deleted = User.objects.create(username="migration-deleted", is_staff=True)

        def event(target, action, privileged):
            return AccountAuditOld.objects.create(
                actor=self.actor,
                target=target,
                actor_id_snapshot=self.actor.pk,
                actor_username="migration-actor",
                actor_is_privileged=True,
                target_id_snapshot=target.pk,
                target_username=target.username,
                target_is_privileged=privileged,
                action=action,
                occurred_at=datetime(2026, 11, 27, 12, 30, tzinfo=timezone.utc),
            )

        self.regular_event = event(regular, "created", False)
        self.promoted_event = event(promoted, "edited", False)
        self.deleted_history_event = event(deleted, "edited", False)
        self.deleted_event = event(deleted, "deleted", True)
        self.deleted_id = deleted.pk
        deleted.delete()
        self.snapshots = {
            event.pk: (event.actor_id, event.action, event.occurred_at, event.actor_username)
            for event in (
                self.regular_event,
                self.promoted_event,
                self.deleted_history_event,
                self.deleted_event,
            )
        }

        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        new_apps = self.executor.loader.project_state([self.migrate_to]).apps
        self.AccountAuditNew = new_apps.get_model("superharry", "AccountAudit")

    def tearDown(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.executor.loader.graph.leaf_nodes())

    def test_backfill_uses_current_live_and_final_deleted_target_roles(self):
        self.assertFalse(
            self.AccountAuditNew.objects.get(pk=self.regular_event.pk).target_is_privileged
        )
        self.assertTrue(
            self.AccountAuditNew.objects.get(pk=self.promoted_event.pk).target_is_privileged
        )
        for event in self.AccountAuditNew.objects.filter(target_id_snapshot=self.deleted_id):
            self.assertIsNone(event.target_id)
            self.assertTrue(event.target_is_privileged)
        for event_id, expected in self.snapshots.items():
            event = self.AccountAuditNew.objects.get(pk=event_id)
            self.assertEqual(
                (event.actor_id, event.action, event.occurred_at, event.actor_username),
                expected,
            )


class FileAccessAuditIdentityBackfillMigrationTests(TestCase):
    def test_backfill_reads_historical_user_fields_for_all_audit_identities(self):
        actor = get_user_model().objects.create_user(
            username="file-audit-migration-actor", password="test", is_staff=True,
        )
        owner = get_user_model().objects.create_user(
            username="file-audit-migration-owner", password="test",
        )
        event = FileAccessAudit.objects.create(
            actor=actor,
            owner=owner,
            action="download",
            file_kind="upload",
            file_id=123,
            file_name="existing-file.txt",
        )
        account_event = AccountAudit.objects.create(
            actor=actor,
            target=owner,
            target_username="placeholder",
            action="edited",
        )
        migration_state = MigrationExecutor(connection).loader.project_state([
            ("superharry", "0041_account_deletion_and_audit_snapshots"),
        ])
        migration = import_module(
            "superharry.migrations.0041_account_deletion_and_audit_snapshots"
        )
        schema_editor = SimpleNamespace(connection=connection)
        migration.backfill_audit_identity(migration_state.apps, schema_editor)
        event.refresh_from_db()
        account_event.refresh_from_db()

        self.assertEqual(event.actor_id_snapshot, actor.pk)
        self.assertEqual(event.actor_username, "file-audit-migration-actor")
        self.assertTrue(event.actor_is_privileged)
        self.assertEqual(event.owner_id_snapshot, owner.pk)
        self.assertEqual(event.owner_username, "file-audit-migration-owner")
        self.assertFalse(event.owner_is_privileged)
        self.assertEqual(account_event.actor_id_snapshot, actor.pk)
        self.assertEqual(account_event.actor_username, "file-audit-migration-actor")
        self.assertTrue(account_event.actor_is_privileged)
        self.assertEqual(account_event.target_id_snapshot, owner.pk)
        self.assertEqual(account_event.target_username, "file-audit-migration-owner")
