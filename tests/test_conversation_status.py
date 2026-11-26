from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from superharry.models import Conversation, Message


class ConversationStatusTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="status-owner")
        self.client.force_login(self.owner)
        self.conversation = Conversation.objects.create(owner=self.owner, title="Status")

    def status(self):
        return self.client.get('/chat/status/').json()['conversations'][0]

    def test_thinking_unread_and_seen_states(self):
        self.client.post(f'/chat/{self.conversation.pk}/seen/', {'opened': '1'})
        reply = Message.objects.create(conversation=self.conversation, role='assistant', in_progress=True, complete=False)
        self.assertEqual(self.status()['state'], 'thinking')
        reply.in_progress = False
        reply.complete = True
        reply.completed_at = timezone.now() + timedelta(seconds=1)
        reply.save()
        self.assertEqual(self.status()['state'], 'unread')
        self.client.post(f'/chat/{self.conversation.pk}/seen/', {'opened': '1'})
        self.conversation.refresh_from_db()
        self.assertIsNotNone(self.conversation.last_opened_at)
        # Put completion before the current open to avoid dependence on clock resolution.
        reply.completed_at = self.conversation.seen_through - timedelta(seconds=1)
        reply.save(update_fields=('completed_at',))
        self.assertEqual(self.status()['state'], 'seen')
        self.assertIsNotNone(self.status()['last_opened'])

    def test_mark_seen_and_status_are_owner_scoped(self):
        Message.objects.create(conversation=self.conversation, role='assistant', complete=True, completed_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.status()['state'], 'unread')
        self.client.get(f'/chat/{self.conversation.pk}/')
        self.assertEqual(self.status()['state'], 'unread')
        self.assertEqual(self.client.post(f'/chat/{self.conversation.pk}/seen/').status_code, 204)
        self.assertEqual(self.status()['state'], 'seen')
        other = Client()
        other.force_login(get_user_model().objects.create_user(username='status-other'))
        self.assertEqual(other.get('/chat/status/').json()['conversations'], [])
        self.assertEqual(other.post(f'/chat/{self.conversation.pk}/seen/').status_code, 404)
