import base64
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from superharry.models import Conversation, Message, SkillArtifact, SkillRun


class SkillArtifactTests(TestCase):
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

    def test_overwrite_version_group_move_and_delete(self):
        self.write_result("complete")
        first = self.client.get(f"/skill-runs/{self.run.job_id}/artifacts/").json()['artifacts'][0]
        original = SkillArtifact.objects.get()
        original_url = first['url']

        def deliver(name, data):
            reply = Message.objects.create(conversation=self.conversation, role='assistant', content='Next')
            run = SkillRun.objects.create(owner=self.owner, reply=reply, skill_name='demo', repository_skill=True)
            root = Path(self.folder.name)
            (root / f'{run.job_id}.skill-result').write_text(json.dumps({'status': 'complete', 'output': 'done'}))
            (root / f'{run.job_id}.skill-artifacts').write_text(json.dumps({'generation': 0, 'files': [{'name': name, 'data': base64.b64encode(data).decode()}]}))
            return self.client.get(f'/skill-runs/{run.job_id}/artifacts/').json()

        replacement = deliver('report.pdf', b'updated')
        self.assertEqual(replacement['artifacts'][0]['url'], original_url)
        self.assertEqual(self.client.get(original_url).content, b'updated')
        same = deliver('report.pdf', b'updated')
        self.assertEqual(same['artifacts'], [])
        version = deliver('report_ver10.pdf', b'version ten')
        second = SkillArtifact.objects.get(name='report_ver10.pdf')
        original.refresh_from_db()
        self.assertEqual(original.group_id, second.group_id)
        self.assertEqual(self.client.get(version['artifacts'][0]['url']).content, b'version ten')
        files_url = f'/chat/{self.conversation.pk}/files/'
        self.assertContains(self.client.get(files_url), 'report_ver10.pdf')
        self.client.post(f'/chat/{self.conversation.pk}/files/{second.pk}/move/', {'group': 'new'})
        second.refresh_from_db()
        self.assertNotEqual(original.group_id, second.group_id)
        self.client.post(f'/chat/{self.conversation.pk}/files/{second.pk}/move/', {'group': original.group_id})
        second.refresh_from_db()
        self.assertEqual(original.group_id, second.group_id)
        deletion = f'/chat/{self.conversation.pk}/groups/{original.group_id}/delete/'
        confirmation = self.client.get(deletion, follow=True)
        self.assertContains(confirmation, '2 files')
        token = confirmation.context['token']
        other = Client()
        other.force_login(get_user_model().objects.create_user(username='group-other'))
        self.assertEqual(other.get(deletion).status_code, 404)
        self.assertEqual(other.post(f'/chat/{self.conversation.pk}/files/{second.pk}/move/', {'group': 'new'}).status_code, 404)
        self.client.post('/files/delete/', {'token': token})
        self.assertEqual(self.client.get(original_url).status_code, 404)
        self.assertTrue(self.client.get(f'/skill-runs/{self.run.job_id}/artifacts/').json()['artifacts'][0]['removed'])
        self.assertFalse(SkillArtifact.objects.exists())

    def test_same_name_overwrite_targets_selected_artifact(self):
        first = SkillArtifact.objects.create(run=self.run, name='same.pdf', payload=b'first')
        reply = Message.objects.create(conversation=self.conversation, role='assistant', content='Other')
        old = SkillRun.objects.create(owner=self.owner, reply=reply, skill_name='old', repository_skill=True)
        selected = SkillArtifact.objects.create(run=old, name='same.pdf', payload=b'second')
        latest = SkillRun.objects.create(owner=self.owner, reply=reply, skill_name='edit', repository_skill=True, selected_artifact_ids=[selected.pk])
        root = Path(self.folder.name)
        (root / f'{latest.job_id}.skill-result').write_text(json.dumps({'status': 'complete', 'output': 'done'}))
        (root / f'{latest.job_id}.skill-artifacts').write_text(json.dumps({'generation': 0, 'files': [{'name': 'same.pdf', 'data': base64.b64encode(b'updated').decode()}]}))
        self.client.get(f'/skill-runs/{latest.job_id}/artifacts/')
        first.refresh_from_db()
        selected.refresh_from_db()
        self.assertEqual(bytes(first.payload), b'first')
        self.assertEqual(bytes(selected.payload), b'updated')
