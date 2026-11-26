import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase, override_settings

from superharry.models import Conversation, Message, SkillArtifact, SkillRun


class SkillArtifactTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="artifact-owner")
        self.client.force_login(self.owner)
        self.conversation = Conversation.objects.create(owner=self.owner, title="Results")
        self.reply = Message.objects.create(conversation=self.conversation, role="assistant", content="Queued")
        self.run = SkillRun.objects.create(owner=self.owner, reply=self.reply, skill_name="demo", repository_skill=True)
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.override = override_settings(SKILL_SPOOL=self.folder.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def write_result(self, status):
        root = Path(self.folder.name)
        (root / f"{self.run.job_id}.skill-result").write_text(json.dumps({"status": status, "output": "done"}))
        data = base64.b64encode(b"private result").decode()
        (root / f"{self.run.job_id}.skill-artifacts").write_text(json.dumps({"generation": 0, "files": [{"name": "report.pdf", "data": data}]}))

    def test_imports_results_once_and_enforces_owner(self):
        self.write_result("complete")
        status = self.client.get(f"/skill-runs/{self.run.job_id}/artifacts/").json()
        self.assertFalse(status["pending"])
        self.assertEqual(len(status["artifacts"]), 1)
        url = status["artifacts"][0]["url"]
        self.assertEqual(self.client.get(url).content, b"private result")
        self.assertEqual(self.client.get(f"/skill-runs/{self.run.job_id}/artifacts/").json()["artifacts"], status["artifacts"])
        self.assertEqual(SkillArtifact.objects.count(), 1)
        self.assertFalse((Path(self.folder.name) / f"{self.run.job_id}.skill-artifacts").exists())
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="artifact-other"))
        self.assertEqual(other.get(url).status_code, 404)
        self.assertEqual(other.get(f"/skill-runs/{self.run.job_id}/artifacts/").status_code, 404)
        self.conversation.delete()
        self.assertFalse(SkillArtifact.objects.exists())

    def test_failed_and_timed_out_output_is_marked_partial(self):
        for status in ("failed", "timeout"):
            with self.subTest(status=status):
                self.run.artifacts_imported = False
                self.run.save(update_fields=("artifacts_imported",))
                self.run.artifacts.all().delete()
                self.write_result(status)
                payload = self.client.get(f"/skill-runs/{self.run.job_id}/artifacts/").json()
                self.assertEqual(payload["status"], status)
                self.assertTrue(payload["artifacts"][0]["partial"])

    def test_invalid_spool_is_not_imported(self):
        self.write_result("complete")
        path = Path(self.folder.name) / f"{self.run.job_id}.skill-artifacts"
        path.write_text(json.dumps({"generation": 0, "files": [{"name": "../bad", "data": "YQ=="}]}))
        payload = self.client.get(f"/skill-runs/{self.run.job_id}/artifacts/").json()
        self.assertTrue(payload["pending"])
        self.assertFalse(SkillArtifact.objects.exists())

    def test_deleted_conversation_cleans_finished_orphan_spool(self):
        from superharry.skill_jobs import discard_orphan_artifacts
        self.write_result("complete")
        path = Path(self.folder.name) / f"{self.run.job_id}.skill-artifacts"
        self.conversation.delete()
        discard_orphan_artifacts(Path(self.folder.name))
        self.assertFalse(path.exists())
