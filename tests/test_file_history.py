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
        self.assertContains(page, '<table', html=False)
        self.assertContains(page, f'/files/input/{self.recent.pk}/')
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
        audit_page = admin.get('/files/audit/')
        self.assertContains(audit_page, '<table', html=False)
        self.assertContains(audit_page, 'Administrative file access audit')
        self.assertNotContains(audit_page, 'other-secret')
        self.assertEqual(admin.get('/files/delete/?period=week').status_code, 400)

    def test_file_details_preserve_owner_and_administrator_access(self):
        owner_detail = self.client.get(f'/files/input/{self.recent.pk}/')
        self.assertContains(owner_detail, 'today.txt')
        self.assertContains(owner_detail, 'text/plain')
        self.assertContains(owner_detail, 'Owner chat')
        self.assertNotContains(owner_detail, 'private-input')
        self.assertEqual(self.client.get(f'/files/input/{self.other_file.pk}/').status_code, 404)

        admin = Client()
        admin.force_login(self.admin)
        admin_detail = admin.get(f'/files/input/{self.other_file.pk}/')
        self.assertContains(admin_detail, 'other.txt')
        self.assertContains(admin_detail, 'other-owner')
        self.assertNotContains(admin_detail, 'other-secret')

    def test_input_listing_and_details_remain_owner_scoped(self):
        listing = self.client.get('/inputs/')
        self.assertContains(listing, '<table', html=False)
        self.assertContains(listing, f'/inputs/{self.recent.pk}/')
        self.assertContains(listing, f'/inputs/{self.recent.pk}/delete/')
        detail = self.client.get(f'/inputs/{self.recent.pk}/')
        self.assertContains(detail, 'today.txt')
        self.assertContains(detail, 'Download today.txt')

        other = Client()
        other.force_login(self.other)
        self.assertEqual(other.get(f'/inputs/{self.recent.pk}/').status_code, 404)
        self.assertNotContains(other.get('/inputs/'), 'today.txt')

    def test_conversation_files_use_grouped_tables_and_keep_move_actions(self):
        page = self.client.get(f'/chat/{self.conversation.pk}/files/')
        self.assertContains(page, '<table', html=False)
        self.assertContains(page, f'/files/artifact/{self.artifact.pk}/')
        self.assertContains(page, f'/chat/{self.conversation.pk}/files/{self.artifact.pk}/move/')
        self.assertContains(page, 'report_ver2.pdf')
        self.assertContains(page, 'New separate group')

        detail = self.client.get(f'/files/artifact/{self.artifact.pk}/')
        self.assertContains(detail, 'demo')
        self.assertContains(detail, 'report.pdf')
        self.assertContains(detail, 'Complete')
        other = Client()
        other.force_login(self.other)
        self.assertEqual(other.get(f'/files/artifact/{self.artifact.pk}/').status_code, 404)

    def test_admin_history_pagination_preserves_owner_filter(self):
        ChatInput.objects.bulk_create([
            ChatInput(owner=self.owner, name=f'page-{index}.txt', media_type='text/plain', payload=b'x')
            for index in range(50)
        ])
        admin = Client()
        admin.force_login(self.admin)

        page = admin.get(f'/files/?owner={self.owner.pk}')

        self.assertContains(page, '<input type="hidden" name="page" value="2">')
        self.assertContains(
            page,
            f'<input type="hidden" name="owner" value="{self.owner.pk}">',
        )
        self.assertEqual(page.context['page'].paginator.count, 54)

    def test_empty_file_listings_have_clear_states(self):
        empty_user = get_user_model().objects.create_user(username='empty-file-owner')
        self.client.force_login(empty_user)
        self.assertContains(self.client.get('/inputs/'), 'No chat inputs yet.')
        self.assertContains(self.client.get('/files/'), 'No files found.')
        conversation = Conversation.objects.create(owner=empty_user, title='Empty files')
        self.assertContains(self.client.get(f'/chat/{conversation.pk}/files/'), 'No generated files yet.')

        self.client.force_login(self.admin)
        self.assertContains(self.client.get('/files/audit/'), 'No recorded access.')

    def test_admin_audit_pagination_is_preserved(self):
        FileAccessAudit.objects.bulk_create([
            FileAccessAudit(
                actor=self.admin, owner=self.owner, action='download', file_kind='input',
                file_id=index, file_name=f'audit-{index}.txt',
            )
            for index in range(101)
        ])
        admin = Client()
        admin.force_login(self.admin)

        page = admin.get('/files/audit/')

        self.assertEqual(page.context['page'].paginator.count, 101)
        self.assertContains(page, '?page=2')

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
