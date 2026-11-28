import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.sessions.models import Session
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from superharry.models import (
    AccountAudit,
    ArtifactGroup,
    ChatInput,
    ChatSubmission,
    Conversation,
    FileAccessAudit,
    Message,
    SavedMemory,
    Schedule,
    ScheduleOccurrence,
    SkillArtifact,
    SkillOutbox,
    SkillPreparation,
    SkillRun,
    UserProfile,
    VmSkill,
)


class AccountDeletionTests(TransactionTestCase):
    def setUp(self):
        users = get_user_model().objects
        self.manager = users.create_user(
            username="deletion-manager", password="CHANGE_ME", is_staff=True
        )
        administrators, _ = Group.objects.get_or_create(name="Administrators")
        self.manager.groups.add(administrators)
        self.account = users.create_user(username="deletion-target", password="CHANGE_ME")
        self.other = users.create_user(username="unrelated-owner", password="CHANGE_ME")
        self.conversation = Conversation.objects.create(owner=self.account, title="Private title")
        self.client.force_login(self.manager)

    def delete_url(self):
        return reverse("account-delete", args=(self.account.pk,))

    def test_delete_requires_authorized_manager_and_regular_target(self):
        guest = Client()
        response = guest.get(self.delete_url())
        self.assertEqual(response.status_code, 302)
        regular = Client()
        regular.force_login(self.account)
        self.assertEqual(regular.get(self.delete_url()).status_code, 403)

        privileged = get_user_model().objects.create_user(
            username="privileged-delete-target", password="CHANGE_ME", is_staff=True
        )
        owner = get_user_model().objects.create_superuser(
            username="deletion-superuser", password="CHANGE_ME"
        )
        for target in (privileged, owner):
            url = reverse("account-delete", args=(target.pk,))
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(
                self.client.post(url, {"confirmation": target.username}).status_code,
                404,
            )

    def test_superuser_can_delete_administrator_but_not_a_superuser_account(self):
        group, _ = Group.objects.get_or_create(name="Administrators")
        administrator = get_user_model().objects.create_user(
            username="deletable-admin", password="CHANGE_ME", is_staff=True
        )
        administrator.groups.add(group)
        owner = get_user_model().objects.create_superuser(
            username="deletion-owner", password="CHANGE_ME"
        )
        self.client.force_login(owner)
        response = self.client.post(
            reverse("account-delete", args=(administrator.pk,)),
            {"confirmation": administrator.username},
        )
        self.assertRedirects(response, reverse("account-list"))
        self.assertFalse(get_user_model().objects.filter(pk=administrator.pk).exists())
        deletion_audit = AccountAudit.objects.get(
            target_id_snapshot=administrator.pk, action="deleted"
        )
        self.assertTrue(deletion_audit.target_is_privileged)

        self.assertEqual(
            self.client.post(
                reverse("account-delete", args=(owner.pk,)),
                {"confirmation": owner.username},
            ).status_code,
            404,
        )
        owner.refresh_from_db()
        self.assertTrue(owner.is_superuser)
        self.assertTrue(owner.is_active)

    def test_confirmation_page_explains_shared_skill_consequence_and_requires_username(self):
        skill = VmSkill.objects.create(
            owner=self.account,
            name="owned-shared-skill",
            purpose="Shared workflow",
            instructions="Private skill instructions",
        )
        SkillRun.objects.create(owner=self.other, skill=skill, skill_name=skill.name)

        response = self.client.get(self.delete_url())

        self.assertContains(response, "owned-shared-skill")
        self.assertContains(response, "other users")
        self.assertContains(response, 'name="confirmation"')
        self.assertNotContains(response, "Private skill instructions")
        mismatch = self.client.post(self.delete_url(), {"confirmation": "wrong-name"})
        self.assertEqual(mismatch.status_code, 400)
        self.assertTrue(get_user_model().objects.filter(pk=self.account.pk).exists())

    def test_delete_removes_owned_content_and_spool_but_retains_classified_audits(self):
        memory = SavedMemory.objects.create(owner=self.account, content="Private memory")
        prompt = self.conversation.messages.create(role=Message.Role.USER, content="Private prompt")
        reply = self.conversation.messages.create(
            prompt=prompt, role=Message.Role.ASSISTANT, content="Private response"
        )
        upload = ChatInput.objects.create(
            owner=self.account, message=prompt, name="private.csv", media_type="text/csv", payload=b"private"
        )
        FileAccessAudit.objects.create(
            actor=self.manager,
            owner=self.account,
            actor_id_snapshot=self.manager.pk,
            actor_username=self.manager.username,
            actor_is_privileged=True,
            owner_id_snapshot=self.account.pk,
            owner_username=self.account.username,
            owner_is_privileged=False,
            action="download",
            file_kind="input",
            file_id=upload.pk,
            file_name=upload.name,
        )
        actor_file_audit = FileAccessAudit.objects.create(
            actor=self.account,
            owner=self.other,
            action="download",
            file_kind="artifact",
            file_id=9981,
            file_name="other-owner-private-name.txt",
        )
        prior_actor_audit = AccountAudit.objects.create(
            actor=self.account,
            target=self.other,
            actor_id_snapshot=self.account.pk,
            actor_username=self.account.username,
            actor_is_privileged=True,
            target_id_snapshot=self.other.pk,
            target_username=self.other.username,
            target_is_privileged=False,
            action="edited",
        )
        self.client.post(
            reverse("account-edit", args=(self.account.pk,)),
            {"username": self.account.username, "first_name": "Before deletion"},
        )
        prior_account_audit = AccountAudit.objects.get(
            target=self.account, action="edited"
        )
        schedule = Schedule.objects.create(
            owner=self.account,
            conversation=self.conversation,
            prompt="Private scheduled prompt",
            timezone="UTC",
            repetition="once",
        )
        ScheduleOccurrence.objects.create(
            schedule=schedule,
            due_at=datetime(2026, 11, 28, tzinfo=timezone.utc),
            status="complete",
            reply=reply,
        )
        skill = VmSkill.objects.create(
            owner=self.account,
            name="owned-cleanup-skill",
            purpose="Owned local skill",
            instructions="Private skill source",
        )
        preparation = SkillPreparation.objects.create(
            skill=skill, fingerprint="f" * 64, payload={"source": "private"}
        )
        run = SkillRun.objects.create(
            owner=self.account, skill=skill, skill_name=skill.name, reply=reply
        )
        SkillOutbox.objects.create(
            job_id=preparation.job_id,
            requested_by=self.account,
            preparation=preparation,
            payload={"source": "private"},
        )
        SkillOutbox.objects.create(
            job_id=run.job_id,
            requested_by=self.account,
            execution=run,
            payload={"prompt": "private"},
        )
        artifact_group = ArtifactGroup.objects.create(
            owner=self.account, conversation=self.conversation, name="private-results"
        )
        SkillArtifact.objects.create(run=run, group=artifact_group, name="result.txt", payload=b"private result")
        user_client = Client()
        user_client.force_login(self.account)
        session_key = user_client.session.session_key
        self.assertTrue(Session.objects.filter(session_key=session_key).exists())
        with tempfile.TemporaryDirectory() as spool_dir:
            spool = Path(spool_dir)
            for job_id in (preparation.job_id, run.job_id):
                (spool / f"{job_id}.skill-result").write_text(
                    json.dumps({"status": "complete", "generation": 0}), encoding="utf-8"
                )
                (spool / f"{job_id}.skill-wheels").write_text("private wheel metadata", encoding="utf-8")
            with override_settings(SKILL_SPOOL=spool_dir):
                response = self.client.post(self.delete_url(), {"confirmation": self.account.username})

            self.assertRedirects(response, reverse("account-list"))
            self.assertFalse(get_user_model().objects.filter(pk=self.account.pk).exists())
            self.assertFalse(Conversation.objects.filter(pk=self.conversation.pk).exists())
            self.assertFalse(SavedMemory.objects.filter(pk=memory.pk).exists())
            self.assertFalse(Schedule.objects.filter(pk=schedule.pk).exists())
            self.assertFalse(ChatInput.objects.filter(pk=upload.pk).exists())
            self.assertFalse(VmSkill.objects.filter(pk=skill.pk).exists())
            self.assertFalse(SkillRun.objects.filter(pk=run.pk).exists())
            self.assertFalse(SkillPreparation.objects.filter(pk=preparation.pk).exists())
            self.assertFalse(SkillArtifact.objects.filter(group_id=artifact_group.pk).exists())
            self.assertFalse(list(spool.iterdir()))

        retained = FileAccessAudit.objects.get(file_id=upload.pk, action="download")
        self.assertIsNone(retained.owner_id)
        self.assertEqual(retained.owner_username, "deletion-target")
        self.assertEqual(retained.owner_id_snapshot, self.account.pk)
        self.assertFalse(retained.owner_is_privileged)
        self.assertEqual(retained.actor_username, "deletion-manager")
        self.assertEqual(retained.actor_id_snapshot, self.manager.pk)
        actor_entry = FileAccessAudit.objects.get(pk=actor_file_audit.pk)
        self.assertIsNone(actor_entry.actor_id)
        self.assertEqual(actor_entry.actor_id_snapshot, self.account.pk)
        self.assertEqual(actor_entry.actor_username, "deletion-target")
        self.assertEqual(actor_entry.owner, self.other)
        self.assertEqual(actor_entry.file_name, "")
        prior_actor_audit.refresh_from_db()
        self.assertIsNone(prior_actor_audit.actor_id)
        self.assertEqual(prior_actor_audit.actor_id_snapshot, self.account.pk)
        self.assertTrue(prior_actor_audit.actor_is_privileged)
        audit_page = self.client.get(reverse("file-audit"))
        self.assertEqual(audit_page.status_code, 200)
        self.assertContains(audit_page, "deletion-target")
        self.assertContains(audit_page, "Removed file")
        self.assertNotContains(audit_page, "private.csv")
        self.assertNotContains(audit_page, "other-owner-private-name.txt")
        deletion = AccountAudit.objects.get(action="deleted", target_username="deletion-target")
        self.assertIsNone(deletion.target_id)
        self.assertEqual(deletion.target_id_snapshot, self.account.pk)
        self.assertEqual(deletion.actor, self.manager)
        self.assertEqual(deletion.actor_username, "deletion-manager")
        self.assertEqual(deletion.actor_id_snapshot, self.manager.pk)
        self.assertFalse(deletion.target_is_privileged)
        prior_account_audit.refresh_from_db()
        self.assertIsNone(prior_account_audit.target_id)
        self.assertEqual(prior_account_audit.target_id_snapshot, self.account.pk)
        self.assertEqual(prior_account_audit.target_username, "deletion-target")
        self.assertFalse(prior_account_audit.target_is_privileged)
        self.assertFalse(AccountAudit.objects.filter(target_username="deletion-target").exclude(action="deleted").exclude(target__isnull=True).exists())
        self.assertFalse(Session.objects.filter(session_key=session_key).exists())

    def test_pending_chat_and_skill_cancellation_keeps_account_and_data(self):
        prompt = self.conversation.messages.create(role=Message.Role.USER, content="Keep until stop")
        reply = self.conversation.messages.create(
            prompt=prompt,
            role=Message.Role.ASSISTANT,
            content="partial",
            in_progress=True,
            complete=False,
        )
        submission = ChatSubmission.objects.create(
            owner=self.account, reply=reply, fingerprint="c" * 64, active=True
        )
        skill = VmSkill.objects.create(
            owner=self.account,
            name="running-owned-skill",
            purpose="In-flight skill",
            instructions="Synthetic",
        )
        preparation = SkillPreparation.objects.create(
            skill=skill, fingerprint="a" * 64, payload={"operation": "prepare"}
        )
        run = SkillRun.objects.create(owner=self.account, skill=skill, skill_name=skill.name)
        shared_run = SkillRun.objects.create(
            owner=self.other,
            skill=skill,
            skill_name=skill.name,
            execution_payload={
                "operation": "run",
                "files": {"main.py": "private skill source"},
                "wheels": {"package.whl": "private dependency"},
                "input": "the other user's prompt",
            },
        )
        SkillOutbox.objects.create(
            job_id=run.job_id, requested_by=self.account, execution=run, payload={"operation": "run"}
        )
        SkillOutbox.objects.create(
            job_id=preparation.job_id,
            requested_by=self.account,
            preparation=preparation,
            payload={"operation": "prepare"},
        )
        SkillOutbox.objects.create(
            job_id=shared_run.job_id,
            requested_by=self.other,
            execution=shared_run,
            payload={"operation": "run", "input": "the other user's prompt"},
        )
        with tempfile.TemporaryDirectory() as spool_dir, override_settings(SKILL_SPOOL=spool_dir):
            spool = Path(spool_dir)
            for job_id in (run.job_id, shared_run.job_id, preparation.job_id):
                (spool / f"{job_id}.skill-running").write_text(
                    json.dumps({"_lifecycle_generation": 0}), encoding="utf-8"
                )
            response = self.client.post(self.delete_url(), {"confirmation": self.account.username})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(get_user_model().objects.filter(pk=self.account.pk).exists())
            self.assertTrue(Conversation.objects.filter(pk=self.conversation.pk).exists())
            self.assertFalse(AccountAudit.objects.filter(target_username="deletion-target", action="deleted").exists())
            self.assertContains(response, "awaiting confirmation", status_code=202)
            other_client = Client()
            other_client.force_login(self.other)
            self.assertEqual(
                other_client.post(
                    reverse("skill-run", args=(skill.name,)), {"input": "later run"}
                ).status_code,
                404,
            )

            self.assertIsNotNone(ChatSubmission.objects.get(pk=submission.pk).cancel_requested_at)
            self.assertEqual(SkillRun.objects.get(pk=run.pk).lifecycle, "cancelled")
            shared_run.refresh_from_db()
            self.assertEqual(shared_run.lifecycle, "cancelled")
            preparation.refresh_from_db()
            self.assertEqual(preparation.lifecycle_generation, 1)
            self.assertFalse(AccountAudit.objects.filter(target_username="deletion-target", action="deleted").exists())

            self.assertEqual(
                self.client.post(
                    self.delete_url(), {"confirmation": self.account.username}
                ).status_code,
                202,
            )
            preparation.refresh_from_db()
            self.assertEqual(preparation.lifecycle_generation, 1)

            submission.cancellation_confirmed_at = datetime.now(timezone.utc)
            submission.save(update_fields=("cancellation_confirmed_at",))
            for job_id in (run.job_id, shared_run.job_id, preparation.job_id):
                (spool / f"{job_id}.skill-running").unlink()
            from scripts.run_skill_jobs import process_queue

            process_queue(spool)
            (spool / f"{shared_run.job_id}.skill-artifacts").write_text(
                json.dumps({"generation": 0, "files": [{"name": "other-result.txt", "data": "b3RoZXIgZmlsZQ=="}]}),
                encoding="utf-8",
            )
            response = self.client.post(self.delete_url(), {"confirmation": self.account.username})
            self.assertRedirects(response, reverse("account-list"))
            self.assertTrue((spool / f"{shared_run.job_id}.skill-result").exists())
            self.assertTrue((spool / f"{shared_run.job_id}.skill-artifacts").exists())

        self.assertFalse(get_user_model().objects.filter(pk=self.account.pk).exists())
        shared_run.refresh_from_db()
        self.assertIsNone(shared_run.skill_id)
        self.assertEqual(shared_run.execution_payload["files"], {})
        self.assertEqual(shared_run.execution_payload["wheels"], {})
        self.assertEqual(shared_run.execution_payload["input"], "the other user's prompt")
        self.assertTrue(SkillRun.objects.filter(pk=shared_run.pk, owner=self.other).exists())

    def test_storage_failure_keeps_account_pending_and_blocks_reactivation_until_retry(self):
        from unittest.mock import patch

        run = SkillRun.objects.create(
            owner=self.account, skill_name="cleanup-retry-result", repository_skill=True
        )
        with tempfile.TemporaryDirectory() as spool_dir, override_settings(SKILL_SPOOL=spool_dir):
            result_path = Path(spool_dir) / f"{run.job_id}.skill-result"
            artifact_path = Path(spool_dir) / f"{run.job_id}.skill-artifacts"
            result_path.write_text(
                json.dumps({"status": "complete", "generation": 0}), encoding="utf-8"
            )
            artifact_path.write_text("private result envelope", encoding="utf-8")

            def unlink_one_then_fail(account):
                artifact_path.unlink()
                raise OSError("synthetic failure after partial cleanup")

            with patch(
                "superharry.account_deletion._remove_job_spool_files",
                side_effect=unlink_one_then_fail,
            ):
                response = self.client.post(
                    self.delete_url(), {"confirmation": self.account.username}
                )
            self.assertEqual(response.status_code, 503)
            self.account.refresh_from_db()
            self.assertFalse(self.account.is_active)
            self.assertTrue(self.account.profile.deletion_pending)
            self.assertTrue(self.account.profile.deletion_stop_confirmed)
            self.assertFalse(artifact_path.exists())
            self.assertTrue(result_path.exists())
            self.assertFalse(
                AccountAudit.objects.filter(target_username="deletion-target", action="deleted").exists()
            )
            self.assertEqual(
                self.client.post(reverse("account-reactivate", args=(self.account.pk,))).status_code,
                409,
            )
            self.assertEqual(
                self.client.get(reverse("account-edit", args=(self.account.pk,))).status_code,
                404,
            )
            response = self.client.post(
                self.delete_url(), {"confirmation": self.account.username}
            )
            self.assertRedirects(response, reverse("account-list"))
            self.assertFalse(result_path.exists())
            self.assertFalse(get_user_model().objects.filter(pk=self.account.pk).exists())

    def test_shared_skill_run_rechecks_owner_deletion_after_taking_owner_lock(self):
        from unittest.mock import patch

        from superharry.skill_jobs import skill_fingerprint

        skill = VmSkill.objects.create(
            owner=self.account,
            name="race-owned-skill",
            purpose="Race test",
            instructions="Synthetic skill",
            files={"main.py": "print(input())"},
        )
        preparation = SkillPreparation.objects.create(
            skill=skill,
            fingerprint=skill_fingerprint(skill),
            payload={"files": skill.files},
        )
        other_client = Client()
        other_client.force_login(self.other)

        with tempfile.TemporaryDirectory() as spool_dir, override_settings(SKILL_SPOOL=spool_dir):
            (Path(spool_dir) / f"{preparation.job_id}.skill-result").write_text(
                json.dumps({"status": "complete", "generation": 0}), encoding="utf-8"
            )

            def begin_deletion_while_run_waits(owner_id):
                profile, _ = UserProfile.objects.get_or_create(user_id=owner_id)
                profile.deletion_pending = True
                profile.save(update_fields=("deletion_pending",))
                return True

            with patch(
                "superharry.skill_views.lock_account",
                side_effect=begin_deletion_while_run_waits,
            ):
                response = other_client.post(
                    reverse("skill-run", args=(skill.name,)), {"input": "shared prompt"}
                )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(SkillRun.objects.filter(owner=self.other, skill=skill).exists())
        self.assertFalse(SkillOutbox.objects.filter(requested_by=self.other).exists())
