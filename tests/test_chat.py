import json
import re
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase


class ChatTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="chat-user")
        self.client.force_login(self.user)
        self.requests = []
        self.gateway_responses = deque()
        requests = self.requests
        gateway_responses = self.gateway_responses

        class Gateway(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), payload))
                status, body = gateway_responses.popleft() if gateway_responses else (
                    200,
                    b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":" there"}}]}\n\n'
                    b"data: [DONE]\n\n",
                )
                self.send_response(status)
                self.send_header(
                    "Content-Type", "text/event-stream" if status == 200 else "application/json"
                )
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Gateway)
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.enterContext(self.settings(
            OMNIROUTE_URL=f"http://127.0.0.1:{server.server_port}",
            OMNIROUTE_API_KEY="test-gateway-key",
            CHAT_MODEL="test/free-model",
        ))

    def configure_limits(self, daily_messages, max_response_characters):
        admin = Client()
        admin.force_login(get_user_model().objects.create_superuser(username="limit-owner"))
        result = admin.post("/admin/superharry/chatlimits/1/change/", {
            "daily_messages": daily_messages,
            "max_response_characters": max_response_characters,
        })
        self.assertRedirects(result, "/admin/superharry/chatlimits/")

    def test_new_chat_streams_reply_from_active_model(self):
        saved = self.client.post("/memories/new/", {"content": "Private saved preference"})
        self.assertRedirects(saved, "/memories/")
        response = self.client.post("/chat/send/", {"message": "Hi"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertEqual(
            [json.loads(chunk) for chunk in response.streaming_content],
            [{"text": "Hello"}, {"text": " there"}, {"done": True}],
        )
        self.assertEqual(self.requests, [(
            "/v1/chat/completions",
            "Bearer test-gateway-key",
            {
                "model": "test/free-model",
                "stream": True,
                "messages": [{"role": "user", "content": "Hi"}],
            },
        )])

    def test_reopened_chat_shows_history_and_sends_it_with_the_next_message(self):
        first = self.client.post("/chat/send/", {"message": "First question"})
        list(first.streaming_content)
        conversation_id = first["X-Conversation-Id"]
        page = self.client.get(f"/chat/{conversation_id}/")
        self.assertContains(page, "test/free-model")
        self.assertContains(page, "First question")
        self.assertContains(page, "Hello there")
        self.assertContains(page, 'name="message"')
        self.assertContains(self.client.get("/"), f'href="/chat/{conversation_id}/"')

        second = self.client.post(
            f"/chat/{conversation_id}/send/", {"message": "Follow-up"}
        )
        list(second.streaming_content)
        self.assertEqual(self.requests[-1][2]["messages"], [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "Hello there"},
            {"role": "user", "content": "Follow-up"},
        ])

    def test_other_accounts_cannot_read_or_extend_a_private_conversation(self):
        response = self.client.post("/chat/send/", {"message": "Private question"})
        list(response.streaming_content)
        conversation_id = response["X-Conversation-Id"]
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other-user"))

        self.assertNotContains(other.get("/"), "Private question")
        self.assertEqual(other.get(f"/chat/{conversation_id}/").status_code, 404)
        self.assertEqual(
            other.post(f"/chat/{conversation_id}/send/", {"message": "Intrusion"}).status_code,
            404,
        )
        self.assertEqual(len(self.requests), 1)

    def test_chat_requires_sign_in(self):
        anonymous = Client()
        self.assertRedirects(anonymous.get("/"), "/login/?next=/")
        self.assertRedirects(
            anonymous.post("/chat/send/", {"message": "Hi"}),
            "/login/?next=/chat/send/",
        )
        self.assertEqual(self.requests, [])

    def test_daily_limit_counts_gateway_submissions_and_skips_local_rejections(self):
        self.configure_limits(daily_messages=1, max_response_characters=100)
        self.assertEqual(self.client.post("/chat/send/", {"message": " "}).status_code, 400)
        first = self.client.post("/chat/send/", {"message": "Allowed"})
        self.assertEqual([json.loads(chunk) for chunk in first.streaming_content][-1], {"done": True})
        denied = self.client.post("/chat/send/", {"message": "Over limit"})
        self.assertEqual(denied.status_code, 429)
        self.assertContains(denied, "daily message limit", status_code=429)
        self.assertEqual(len(self.requests), 1)

    def test_response_limit_caps_streamed_and_saved_characters(self):
        self.configure_limits(daily_messages=3, max_response_characters=7)
        reply = self.client.post("/chat/send/", {"message": "Keep it short"})
        self.assertEqual(
            [json.loads(chunk) for chunk in reply.streaming_content],
            [{"text": "Hello"}, {"text": " t"}, {"done": True}],
        )
        history = self.client.get(reply["Location"])
        self.assertContains(history, "<p>Hello t</p>", html=True)
        self.assertNotContains(history, "Hello there")
        self.assertNotContains(history, "Incomplete reply")

    def test_administrator_can_override_limits_for_one_user(self):
        self.configure_limits(daily_messages=0, max_response_characters=100)
        account = get_user_model().objects.create_user(username="limits-admin", is_staff=True)
        account.groups.add(Group.objects.get(name="Administrators"))
        administrator = Client()
        administrator.force_login(account)
        response = administrator.post("/admin/superharry/userchatlimits/add/", {
            "user": self.user.pk,
            "daily_messages": 1,
            "max_response_characters": 4,
        })
        self.assertRedirects(response, "/admin/superharry/userchatlimits/")
        reply = self.client.post("/chat/send/", {"message": "My override"})
        self.assertEqual(
            [json.loads(chunk) for chunk in reply.streaming_content],
            [{"text": "Hell"}, {"done": True}],
        )
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="ordinary-limit"))
        self.assertEqual(other.post("/chat/send/", {"message": "Default"}).status_code, 429)
        self.assertEqual(self.client.post("/chat/send/", {"message": "Over"}).status_code, 429)
        self.assertEqual(len(self.requests), 1)

    def test_repeated_delivery_reopens_one_submission_without_charging_twice(self):
        self.configure_limits(daily_messages=2, max_response_characters=100)
        page = self.client.get("/")
        key = re.search(r'name="submission_id" value="([^"]+)"', page.content.decode())
        self.assertIsNotNone(key)
        form = {"message": "One submission", "submission_id": key.group(1)}
        first = self.client.post("/chat/send/", form)
        list(first.streaming_content)
        repeated = self.client.post("/chat/send/", form)
        self.assertEqual(repeated.status_code, 204)
        self.assertEqual(repeated["Location"], first["Location"])
        changed = self.client.post("/chat/send/", {**form, "message": "Different text"})
        self.assertEqual(changed.status_code, 409)
        second = self.client.post("/chat/send/", {"message": "New submission"})
        list(second.streaming_content)
        self.assertEqual(self.client.post("/chat/send/", {"message": "Third"}).status_code, 429)
        self.assertEqual(len(self.requests), 2)

    def test_daily_quota_resets_at_midnight_in_the_deployment_timezone(self):
        self.configure_limits(daily_messages=1, max_response_characters=100)
        with self.settings(TIME_ZONE="America/Guayaquil"):
            with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 24, 4, 59, tzinfo=timezone.utc)):
                before = self.client.post("/chat/send/", {"message": "Before midnight"})
                list(before.streaming_content)
                self.assertEqual(self.client.post("/chat/send/", {"message": "Too early"}).status_code, 429)
            with patch("django.utils.timezone.now", return_value=datetime(2026, 11, 24, 5, tzinfo=timezone.utc)):
                after = self.client.post("/chat/send/", {"message": "New quota day"})
                self.assertEqual(after.status_code, 200)
                list(after.streaming_content)
        self.assertEqual(len(self.requests), 2)

    def test_active_response_blocks_another_send_until_it_closes(self):
        self.configure_limits(daily_messages=1, max_response_characters=100)
        pending = self.client.post("/chat/send/", {"message": "Reserved but not streamed"})
        self.addCleanup(pending.close)
        self.assertTrue(pending.streaming)
        denied = self.client.post("/chat/send/", {"message": "Overlapping submission"})
        self.assertEqual(denied.status_code, 409)
        self.assertContains(denied, "already in progress", status_code=409)
        self.assertEqual(self.requests, [])
        pending.close()

        allowed = self.client.post("/chat/send/", {"message": "After cancellation"})
        self.assertEqual(allowed.status_code, 200)
        list(allowed.streaming_content)
        self.assertEqual(len(self.requests), 1)

    def test_interrupted_reply_can_be_retried_explicitly(self):
        self.gateway_responses.append(
            (200, b'data: {"choices":[{"delta":{"content":"Partial reply"}}]}\n\n')
        )
        interrupted = self.client.post("/chat/send/", {"message": "Original question"})
        self.assertEqual(
            [json.loads(chunk) for chunk in interrupted.streaming_content],
            [{"text": "Partial reply"}, {"error": "The model reply was interrupted."}],
        )
        history = interrupted["Location"]
        page = self.client.get(history)
        self.assertContains(page, "Partial reply")
        self.assertContains(page, "Incomplete reply")
        form = re.search(r'<form class="chat-retry" method="post" action="([^"]+)"', page.content.decode())
        self.assertIsNotNone(form)
        retry_url = form.group(1)
        self.assertEqual(len(self.requests), 1)

        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other-user"))
        self.assertEqual(other.post(retry_url).status_code, 404)
        self.assertEqual(len(self.requests), 1)

        retry = self.client.post(retry_url)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual([json.loads(chunk) for chunk in retry.streaming_content][-1], {"done": True})
        self.assertEqual(self.requests[0], self.requests[1])
        page = self.client.get(history)
        self.assertContains(page, "<p>Original question</p>", count=1, html=True)
        self.assertContains(page, "Partial reply")
        self.assertContains(page, "Hello there")
        self.assertNotContains(page, "Retry reply")
        self.assertEqual(self.client.post(retry_url).status_code, 409)
        self.assertEqual(len(self.requests), 2)

    def test_free_route_failures_are_reported_without_fallback(self):
        for status, expected in (
            (429, "The free route has exhausted its quota or rate limit."),
            (503, "The free route is currently unavailable."),
        ):
            with self.subTest(status=status):
                self.gateway_responses.append((status, b'{"error":"private upstream details"}'))
                previous = len(self.requests)
                response = self.client.post("/chat/send/", {"message": "Hello"})
                self.assertEqual(
                    [json.loads(chunk) for chunk in response.streaming_content],
                    [{"error": expected}],
                )
                self.assertContains(self.client.get(response["Location"]), "Incomplete reply")
                self.assertEqual(len(self.requests), previous + 1)
                self.assertEqual(self.requests[-1][2]["model"], "test/free-model")
