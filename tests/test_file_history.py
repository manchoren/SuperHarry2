from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase
from django.utils import timezone

from superharry.models import ArtifactDelivery, ArtifactGroup, ChatInput, Conversation, FileAccessAudit, Message, RemovedChatInput, SkillArtifact, SkillRun


class FileHistoryTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username='file-owner')
        self.other = get_user_model().objects.create_user(username='other-owner')
        self.admin = get_user_model().objects.create_user(username='admin-user')
        self.admin.groups.add(Group.objects.get_or_create(name='Administrators')[0])
        self.client.force_login(self.owner)
        self.conversation = Conversation.objects.create(owner=self.owner, title='Owner chat')
        self.message = Message.objects.create(conversation=self.conversation, role='user', content='Files')
        self.recent = ChatInput.objects.create(owner=self.owner, message=self.message, name='today.txt', media_type='text/plain', payload=b'private-input')
        self.unknown = ChatInput.objects.create(owner=self.owner, message=self.message, name='unknown.txt', media_type='text/plain', payload=b'unknown', created_at=None)
        self.other_file = ChatInput.objects.create(owner=self.other, name='other.txt', media_type='text/plain', payload=b'other-secret')
        reply = Message.objects.create(conversation=self.conversation, role='assistant', content='Output')
        self.run = SkillRun.objects.create(owner=self.owner, reply=reply, skill_name='demo', repository_skill=True, artifacts_imported=True)
        self.group = ArtifactGroup.objects.create(owner=self.owner, conversation=self.conversation, name='report.pdf')
        self.artifact = SkillArtifact.objects.create(run=self.run, group=self.group, name='report.pdf', payload=b'output')
        self.version = SkillArtifact.objects.create(run=self.run, group=self.group, name='report_ver2.pdf', payload=b'old', updated_at=timezone.now() - timedelta(days=40))
        ArtifactDelivery.objects.create(run=self.run, artifact=self.artifact, name=self.artifact.name)
        ArtifactDelivery.objects.create(run=self.run, artifact=self.version, name=self.version.name)

    def confirmation(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response, response.context['token']

    def test_owner_isolation_and_admin_audit(self):
        page = self.client.get('/files/')
        self.assertContains(page, 'today.txt')
        self.assertContains(page, 'report.pdf')
        self.assertNotContains(page, 'other.txt')
        self.assertEqual(self.client.get(f'/files/input/{self.other_file.pk}/download/').status_code, 404)
        self.assertEqual(self.client.get('/files/audit/').status_code, 404)
        admin = Client()
        admin.force_login(self.admin)
        page = admin.get('/files/')
        self.assertContains(page, 'today.txt')
        self.assertContains(page, 'other.txt')
        self.assertTrue(FileAccessAudit.objects.filter(actor=self.admin, owner=self.owner, action='view', file_id=self.recent.pk, file_kind='input').exists())
        result = admin.get(f'/files/input/{self.other_file.pk}/download/')
        self.assertEqual(result.content, b'other-secret')
        self.assertTrue(FileAccessAudit.objects.filter(actor=self.admin, owner=self.other, action='download', file_id=self.other_file.pk).exists())
        self.assertNotContains(admin.get('/files/audit/'), 'other-secret')
        self.assertEqual(admin.get('/files/delete/?period=week').status_code, 400)

    def test_period_deletion_expands_groups_and_preserves_unknown_dates(self):
        response, token = self.confirmation('/files/delete/?period=hour')
        self.assertEqual(response.context['count'], 3)
        self.assertContains(response, 'report_ver2.pdf')
        self.assertEqual(self.client.post('/files/delete/', {'token': token + 'tampered'}).status_code, 400)
        self.assertEqual(self.client.post('/files/delete/', {'token': token}).status_code, 302)
        self.assertTrue(ChatInput.objects.filter(pk=self.unknown.pk).exists())
        self.assertTrue(ChatInput.objects.filter(pk=self.other_file.pk).exists())
        self.assertFalse(SkillArtifact.objects.filter(pk=self.artifact.pk).exists())
        self.assertTrue(RemovedChatInput.objects.filter(message=self.message, name='today.txt').exists())
        self.assertContains(self.client.get(f'/chat/{self.conversation.pk}/'), 'today.txt is no longer available')
        self.assertTrue(ArtifactDelivery.objects.filter(run=self.run, artifact__isnull=True, name='report.pdf').exists())

    def test_confirmation_rejects_changed_group(self):
        _, token = self.confirmation(f'/files/delete/?kind=artifact&id={self.artifact.pk}')
        SkillArtifact.objects.create(run=self.run, group=self.group, name='report_ver3.pdf', payload=b'new')
        response = self.client.post('/files/delete/', {'token': token})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(SkillArtifact.objects.count(), 3)

    def test_confirmation_rejects_an_overwritten_result(self):
        _, token = self.confirmation(f'/files/delete/?kind=artifact&id={self.artifact.pk}')
        self.artifact.payload = b'new content'
        self.artifact.updated_at = timezone.now() + timedelta(seconds=1)
        self.artifact.save(update_fields=('payload', 'updated_at'))
        self.assertEqual(self.client.post('/files/delete/', {'token': token}).status_code, 409)
        self.assertTrue(SkillArtifact.objects.filter(pk=self.artifact.pk).exists())

    def test_admin_delete_requires_selected_owner_and_records_each_file(self):
        self.client.force_login(self.admin)
        response, token = self.confirmation(f'/files/delete/?period=all&owner={self.owner.pk}')
        self.assertEqual(response.context['count'], 4)
        self.assertEqual(self.client.post('/files/delete/', {'token': token}).status_code, 302)
        self.assertTrue(ChatInput.objects.filter(pk=self.other_file.pk).exists())
        self.assertEqual(FileAccessAudit.objects.filter(actor=self.admin, owner=self.owner, action='delete').count(), 4)

    def test_existing_input_delete_creates_unavailable_notice(self):
        self.assertEqual(self.client.post(f'/inputs/{self.recent.pk}/delete/').status_code, 302)
        self.assertContains(self.client.get(f'/chat/{self.conversation.pk}/'), 'today.txt is no longer available')
