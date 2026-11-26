import json
import base64
import re
from collections import deque
from datetime import datetime, time, timezone
from io import StringIO
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from superharry.soul import guidance


class ChatTests(TestCase):
    def test_selected_skill_is_sent_with_the_message(self):
        page = self.client.get('/')
        self.assertContains(page, 'value="summarize"')
        self.assertEqual(len(self.requests), 0)
        response = self.client.post('/chat/send/', {'message': 'Help me', 'selected_skill': 'summarize'})
        list(response.streaming_content)
        self.assertIn('"name": "summarize"', self.requests[-1][2]['messages'][1]['content'])
        self.assertContains(self.client.get(response['Location']), 'Skill: summarize')
        self.assertEqual(self.client.post('/chat/send/', {'message': 'Help', 'selected_skill': 'missing'}).status_code, 400)

    def test_retry_preserves_selected_skill(self):
        self.gateway_responses.append((503, b''))
        response = self.client.post('/chat/send/', {'message': 'Help me', 'selected_skill': 'summarize'})
        list(response.streaming_content)
        from superharry.models import Message
        reply = Message.objects.get(role=Message.Role.ASSISTANT)
        retry = self.client.post(f'/chat/{reply.conversation_id}/retry/{reply.pk}/')
        list(retry.streaming_content)
        self.assertIn('"name": "summarize"', self.requests[-1][2]['messages'][1]['content'])

    def test_soul_guidance_is_sent_and_visible_read_only(self):
        response = self.client.post('/chat/send/', {'message': 'Hello'})
        list(response.streaming_content)
        self.assertEqual(self.requests[-1][2]['messages'][0], {'role': 'system', 'content': guidance()})
        page = self.client.get('/settings/')
        self.assertContains(page, 'Agent guidance')
        self.assertContains(page, 'Respect user privacy')
        self.assertEqual(self.client.post('/settings/', {'soul': 'changed'}).status_code, 405)
        self.assertRedirects(Client().get('/settings/'), '/login/?next=/settings/')

    def test_explicit_csv_request_queues_the_selected_owned_file(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name,amount\nA,4\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Summarize my CSV file"})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
            run = SkillRun.objects.get(owner=self.user, skill_name="csv-summary")
            self.assertIn(str(run.job_id), output)
            self.assertContains(self.client.get(response["Location"]), f'href="/skill-runs/{run.job_id}/"')

    def test_agent_cannot_summarize_without_a_user_request(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Here is my file"})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())
            self.assertIn("explicitly ask", output)

    def test_mentioning_a_summary_does_not_authorize_a_csv_run(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Here is the summary from yesterday."})
            list(response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())

    def test_negated_request_does_not_authorize_csv_run(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "I didn't ask you to summarize this CSV."})
            list(response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())

    def test_question_about_summarizing_does_not_authorize_csv_run(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "How would you summarize a CSV?"})
            list(response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())

    def test_request_to_summarize_text_does_not_authorize_csv_run(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Summarize this text: the meeting is tomorrow."})
            list(response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())

    def test_user_can_request_a_summary_in_natural_language(self):
        from superharry.models import ChatInput, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Please provide a summary of my CSV."})
            list(response.streaming_content)
            self.assertTrue(SkillRun.objects.filter(owner=self.user, skill_name="csv-summary").exists())

    def test_csv_skill_cannot_access_another_users_input(self):
        from superharry.models import ChatInput, SkillRun
        other = get_user_model().objects.create_user(username="other-csv")
        item = ChatInput.objects.create(owner=other, name="secret.csv", media_type="text/plain", payload=b"secret\nvalue\n")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Summarize my CSV"})
            list(response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())

    def test_csv_skill_cannot_substitute_another_owned_file_for_the_attachment(self):
        from superharry.models import ChatInput, SkillRun
        old = ChatInput.objects.create(owner=self.user, name="old.csv", media_type="text/plain", payload=b"old\n1\n")
        args = json.dumps({"name": "csv-summary", "input": str(old.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Summarize my CSV", "attachment":
                SimpleUploadedFile("new.csv", b"new\n2\n", content_type="text/csv")})
            list(response.streaming_content)
            self.assertFalse(SkillRun.objects.exists())

    def test_scheduled_csv_skill_cannot_use_an_unselected_owned_input(self):
        from superharry.models import ChatInput, Conversation, Schedule, SkillRun
        item = ChatInput.objects.create(owner=self.user, name="sales.csv", media_type="text/plain", payload=b"name\nA\n")
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Summarize my CSV",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        args = json.dumps({"name": "csv-summary", "input": str(item.pk)})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
            self.assertFalse(SkillRun.objects.exists())

    def test_chat_attachment_is_available_without_automatically_running_a_skill(self):
        from superharry.models import ChatInput, SkillRun
        response = self.client.post("/chat/send/", {"message": "Here is my file", "attachment":
            SimpleUploadedFile("sales.csv", b"name,amount\nA,4\n", content_type="text/csv")})
        list(response.streaming_content)
        self.assertEqual(ChatInput.objects.get(owner=self.user).name, "sales.csv")
        self.assertFalse(SkillRun.objects.exists())

    def test_chat_updates_skill_with_pending_run(self):
        from superharry.models import SkillPreparation, VmSkill
        from superharry.skill_jobs import skill_fingerprint

        skill = VmSkill.objects.create(owner=self.user, name="owned", purpose="Original", instructions="Original", files={"main.py": "print(input())"})
        payload = {"operation": "prepare", "files": skill.files, "entrypoint": "main.py", "input": "", "dependencies": [], "wheels": {}}
        preparation = SkillPreparation.objects.create(skill=skill, fingerprint=skill_fingerprint(skill), payload=payload)
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            (Path(directory) / f"{preparation.job_id}.skill-result").write_text('{"status":"complete","output":""}')
            self.assertEqual(self.client.post("/skills/owned/run/", {"input": "original"}).status_code, 302)
            args = json.dumps({"name": "owned", "purpose": "Revised", "instructions": "Revised", "files": {"main.py": "print('new', input())"}, "dependencies": []})
            event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "edit-1", "function": {"name": "update_skill", "arguments": args}}]}}]}
            self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
            response = self.client.post("/chat/send/", {"message": "Update my skill"})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
            self.assertIn("Skill updated", output)
            self.assertEqual(VmSkill.objects.get(name="owned").instructions, "Revised")

    def test_chat_updates_its_own_skill(self):
        from superharry.models import VmSkill
        VmSkill.objects.create(owner=self.user, name="owned", purpose="Original", instructions="Original")
        args = json.dumps({"name": "owned", "purpose": "Revised", "instructions": "Revised", "files": {}, "dependencies": ["packaging==26.0"]})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "edit-1", "function": {"name": "update_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Update my skill"})
        self.assertIn("Skill updated", "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content))
        self.assertEqual(VmSkill.objects.get(name="owned").dependencies, ["packaging==26.0"])

    def test_chat_rejects_conflicting_skill_names(self):
        from superharry.models import SkillRun, VmSkill
        VmSkill.objects.create(owner=self.user, name="summarize", purpose="Conflict", instructions="Conflict", files={"main.py": "print('bad')"})
        args = json.dumps({"name": "summarize", "input": "text"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "run-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Summarize"})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
        self.assertIn("conflict", output.lower())
        self.assertFalse(SkillRun.objects.exists())

    def test_scheduled_preparation_is_pending_until_the_worker_finishes(self):
        from superharry.models import Conversation, Schedule, VmSkill
        skill = VmSkill.objects.create(owner=self.user, name="prepare-demo", purpose="Demo", instructions="Demo", files={"main.py": "print('ok')"})
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Prepare demo",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "prepare-1", "function": {
            "name": "prepare_skill", "arguments": json.dumps({"name": "prepare-demo", "entrypoint": "main.py"})}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
            occurrence = schedule.occurrences.get()
            self.assertEqual(occurrence.status, "awaiting_preparation")
            (Path(directory) / f"{skill.preparation.job_id}.skill-result").write_text('{"status":"failed"}')
            call_command("run_schedules", stdout=StringIO())
            occurrence.refresh_from_db()
            self.assertEqual(occurrence.status, "failed")
            self.assertIn("preparation failed", occurrence.reply.content.lower())

    def test_scheduled_unknown_skill_is_reported_as_failed(self):
        from superharry.models import Conversation, Schedule
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Run missing skill",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "run-1", "function": {
            "name": "invoke_skill", "arguments": json.dumps({"name": "missing", "input": "data"})}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(schedule.occurrences.get().status, "failed")

    def test_scheduled_skill_run_is_pending_until_the_worker_finishes(self):
        from superharry.models import Conversation, Schedule, VmSkill
        VmSkill.objects.create(owner=self.user, name="greet", purpose="Greet", instructions="Greet", files={"main.py": "print(input())"})
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Greet",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        args = json.dumps({"name": "greet", "input": "chosen"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "run-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            self.client.post("/skills/greet/prepare/", {"entrypoint": "main.py"})
            preparation = VmSkill.objects.get(name="greet").preparation
            (Path(directory) / f"{preparation.job_id}.skill-result").write_text('{"status":"complete"}')
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
            occurrence = schedule.occurrences.get()
            self.assertEqual(occurrence.status, "awaiting_skill")
            (Path(directory) / f"{occurrence.skill_run.job_id}.skill-result").write_text('{"status":"complete","output":"Hello"}')
            call_command("run_schedules", stdout=StringIO())
            occurrence.refresh_from_db()
            self.assertEqual(occurrence.status, "complete")
            self.assertIn("Hello", occurrence.reply.content)

    def test_scheduled_skill_worker_failure_is_reported(self):
        from superharry.models import Conversation, Message, Schedule, ScheduleOccurrence, SkillRun, VmSkill
        skill = VmSkill.objects.create(owner=self.user, name="greet", purpose="Greet", instructions="Greet")
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Greet", timezone="UTC")
        reply = Message.objects.create(conversation=conversation, role="assistant", content="Queued")
        run = SkillRun.objects.create(owner=self.user, skill=skill, skill_name=skill.name)
        occurrence = ScheduleOccurrence.objects.create(schedule=schedule, due_at=datetime(2099, 1, 1, tzinfo=timezone.utc), status="awaiting_skill", skill_run=run, reply=reply)
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            (Path(directory) / f"{run.job_id}.skill-result").write_text('{"status":"timeout"}')
            call_command("run_schedules", stdout=StringIO())
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.status, "failed")
        self.assertIn("timed out", occurrence.reply.content)

    def test_scheduled_skill_output_respects_the_response_limit(self):
        from superharry.models import Conversation, Message, Schedule, ScheduleOccurrence, SkillRun, UserChatLimits, VmSkill
        UserChatLimits.objects.create(user=self.user, max_response_characters=10)
        skill = VmSkill.objects.create(owner=self.user, name="greet", purpose="Greet", instructions="Greet")
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Greet", timezone="UTC")
        reply = Message.objects.create(conversation=conversation, role="assistant", content="Queued")
        run = SkillRun.objects.create(owner=self.user, skill=skill, skill_name=skill.name)
        occurrence = ScheduleOccurrence.objects.create(schedule=schedule, due_at=datetime(2099, 1, 1, tzinfo=timezone.utc), status="awaiting_skill", skill_run=run, reply=reply)
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            (Path(directory) / f"{run.job_id}.skill-result").write_text('{"status":"complete","output":"abcdefghijklmnop"}')
            call_command("run_schedules", stdout=StringIO())
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.reply.content, "Queued\nabc")


    def test_scheduled_chat_does_not_run_an_unprepared_skill(self):
        from superharry.models import Conversation, Schedule, SkillRun, VmSkill
        VmSkill.objects.create(owner=self.user, name="draft", purpose="Draft", instructions="Draft", files={"main.py": "print(input())"})
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Run the draft",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        args = json.dumps({"name": "draft", "input": "chosen"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "run-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
                call_command("run_schedules", stdout=StringIO())
        self.assertFalse(SkillRun.objects.exists())
        self.assertIn("Prepare this skill", schedule.occurrences.get().reply.content)
        self.assertEqual(schedule.occurrences.get().status, "failed")

    def test_scheduled_chat_creates_skill_for_schedule_owner(self):
        from superharry.models import Conversation, Schedule, VmSkill
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Create a greeting skill",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        args = json.dumps({"name": "scheduled-greet", "purpose": "Greet", "instructions": "Greet", "files": {"main.py": "print(input())"}, "dependencies": []})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "create-1", "function": {"name": "create_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(VmSkill.objects.get(name="scheduled-greet").owner, self.user)
        self.assertEqual(schedule.occurrences.get().status, "complete")

    def test_chat_invokes_a_prepared_shared_vm_skill(self):
        from superharry.models import VmSkill
        owner = get_user_model().objects.create_user(username="skill-owner")
        VmSkill.objects.create(owner=owner, name="greet", purpose="Greet", instructions="Greet", files={"main.py": "print(input())"})
        other = Client()
        other.force_login(owner)
        args = json.dumps({"name": "greet", "input": "chosen input"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "run-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            other.post("/skills/greet/prepare/", {"entrypoint": "main.py"})
            preparation = VmSkill.objects.get(name="greet").preparation
            (Path(directory) / f"{preparation.job_id}.skill-result").write_text('{"status":"complete"}')
            response = self.client.post("/chat/send/", {"message": "Greet me"})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
            self.assertIn("/skill-runs/", output)
            self.assertIn("/skill-runs/", json.dumps(self.requests[-1][2]["messages"]))

    def test_chat_cannot_update_another_users_skill(self):
        from superharry.models import VmSkill
        owner = get_user_model().objects.create_user(username="other-owner")
        VmSkill.objects.create(owner=owner, name="private", purpose="Original", instructions="Original")
        args = json.dumps({"name": "private", "purpose": "Changed", "instructions": "Changed", "files": {}, "dependencies": []})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "edit-1", "function": {"name": "update_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Update private"})
        list(response.streaming_content)
        self.assertEqual(VmSkill.objects.get(name="private").purpose, "Original")
        self.assertIn("cannot update", json.dumps(self.requests[-1][2]["messages"]))

    def test_chat_can_prepare_an_owned_skill(self):
        from superharry.models import VmSkill
        VmSkill.objects.create(owner=self.user, name="greet", purpose="Greet", instructions="Greet", files={"main.py": "print(input())"})
        args = json.dumps({"name": "greet", "entrypoint": "main.py"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "prepare-1", "function": {"name": "prepare_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with TemporaryDirectory() as directory, self.settings(SKILL_SPOOL=directory):
            response = self.client.post("/chat/send/", {"message": "Prepare my greeting skill"})
            list(response.streaming_content)
            self.assertContains(self.client.get("/skills/greet/"), "Preparation")
            self.assertTrue(VmSkill.objects.get(name="greet").preparation)

    def test_chat_creates_an_owned_skill_from_a_request(self):
        args = json.dumps({"name": "greet", "purpose": "Greet", "instructions": "Greet the user", "files": {"main.py": "print(input())"}, "dependencies": []})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "create-1", "function": {"name": "create_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Create a greeting skill"})
        output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
        self.assertIn("Created skill greet", output)
        self.assertContains(self.client.get("/skills/greet/"), "Greet")
        from superharry.models import VmSkill
        self.assertEqual(VmSkill.objects.get(name="greet").owner, self.user)

    def test_chat_invokes_repository_skill_without_a_slash_command(self):
        args = json.dumps({"name": "summarize", "input": "A short note"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "skill-1", "function": {"name": "invoke_skill", "arguments": args}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Summarize my note"})
        list(response.streaming_content)
        self.assertEqual(self.requests[-1][2]["messages"][-1]["content"], "A short note")
        self.assertNotIn("tools", self.requests[-1][2])

    def test_chat_discovers_skills_for_a_natural_language_request(self):
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "skills-1", "function": {"name": "discover_skills", "arguments": "{}"}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Find a way to summarize this text"})
        self.assertEqual("".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content), "Hello there")
        self.assertIn("summarize", json.dumps(self.requests[-1][2]["messages"]))

    def test_scheduled_gateway_quota_failure_is_visible_in_the_conversation(self):
        from superharry.models import Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        self.gateway_responses.append((429, b'{}'))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "The free route has exhausted its quota or rate limit.")
        occurrence = schedule.occurrences.get()
        self.assertEqual(occurrence.status, "failed")
        self.assertEqual(occurrence.failure_reason, "The free route has exhausted its quota or rate limit.")

    def test_scheduled_interrupted_stream_preserves_partial_reply(self):
        from superharry.models import Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(
            owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC",
            repetition="once", preferred_model="test/free-model",
        )
        self.gateway_responses.append(
            (200, b'data: {"choices":[{"delta":{"content":"Partial reply"}}]}\n\n')
        )
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        occurrence = schedule.occurrences.get()
        self.assertEqual(occurrence.status, "interrupted")
        self.assertEqual(occurrence.failure_reason, "")
        self.assertEqual(occurrence.reply.content, "Partial reply")
        self.assertFalse(occurrence.reply.complete)

    def test_empty_gateway_error_remains_an_error_event(self):
        from superharry.gateway import GatewayFailure

        with patch("superharry.chat.stream_reply", side_effect=GatewayFailure("")):
            response = self.client.post("/chat/send/", {"message": "Hello"})
            self.assertEqual([json.loads(chunk) for chunk in response.streaming_content], [{"error": ""}])

    def test_initial_recurring_time_in_dst_gap_uses_first_valid_instant(self):
        arguments = json.dumps({"prompt": "Review", "due_at": "2026-03-08T02:30:00-05:00", "timezone": "America/New_York", "repetition": "daily"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with patch("superharry.chat.timezone.now", return_value=datetime(2026, 3, 1, tzinfo=timezone.utc)):
            response = self.client.post("/chat/send/", {"message": "Review every day at 2:30"})
            notice = b"".join(response.streaming_content).decode()
        self.assertIn("2026-03-08T07:00:00+00:00", notice)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2026, 3, 8, 7, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        page = self.client.get(response["Location"])
        self.assertContains(page, "2026-03-09 02:30")

    def test_scheduling_tool_provides_the_current_time_and_deployment_zone(self):
        with patch("superharry.chat.timezone.now", return_value=datetime(2026, 1, 15, 15, tzinfo=timezone.utc)):
            response = self.client.post("/chat/send/", {"message": "Choose a time for a follow-up"})
            list(response.streaming_content)
        tools = self.requests[-1][2]["tools"]
        description = next(tool["function"]["description"] for tool in tools if tool["function"]["name"] == "create_schedule")
        self.assertIn("2026-01-15T10:00:00-05:00", description)
        self.assertIn("America/Guayaquil", description)

    def test_required_decision_is_visible_even_when_response_cap_is_small(self):
        from superharry.models import Conversation, Schedule, UserChatLimits

        UserChatLimits.objects.create(user=self.user, max_response_characters=5)
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "decision-1", "function": {"name": "request_user_input", "arguments": json.dumps({"question": "Which option should I choose?"})}}]}}]}
        payload = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.gateway_responses.extend([(200, payload), (200, payload)])
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Which option should I choose?")

    def test_tool_free_scheduled_skill_cannot_create_a_child_even_if_gateway_requests_it(self):
        from superharry.models import Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="/summarize Notes",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        arguments = json.dumps({"prompt": "Unauthorized child", "due_at": "2099-01-02T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertFalse(Schedule.objects.filter(prompt="Unauthorized child").exists())

    def test_child_schedule_shares_the_owners_active_schedule_cap(self):
        from superharry.models import ChatLimits, Conversation, Schedule

        ChatLimits.objects.filter(pk=1).update(max_active_schedules=1)
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        parent = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Follow up",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="daily",
            wall_time=time(12), preferred_model="test/free-model")
        arguments = json.dumps({"prompt": "Child follow up", "due_at": "2099-01-03T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        payload = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.gateway_responses.append((200, payload))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertFalse(Schedule.objects.filter(prompt="Child follow up").exists())
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "active schedule limit")
        ChatLimits.objects.filter(pk=1).update(max_active_schedules=2)
        self.gateway_responses.append((200, payload))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 2, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertTrue(Schedule.objects.filter(prompt="Child follow up", created_by__schedule=parent).exists())
        self.gateway_responses.append((200, payload))
        response = self.client.post(f"/chat/{conversation.pk}/send/", {"message": "Schedule another"})
        self.assertIn("schedule limit", b"".join(response.streaming_content).decode().lower())

    def test_scheduled_run_creates_an_independent_child_visible_to_its_owner(self):
        from superharry.models import Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        parent = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Follow up",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        arguments = json.dumps({"prompt": "Child follow up", "due_at": "2099-01-02T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "child-1", "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertIn("create_schedule", [tool["function"]["name"] for tool in self.requests[-1][2]["tools"]])
        menu = self.client.get("/schedules/")
        self.assertContains(menu, "Child follow up")
        self.assertContains(menu, f"Created by scheduled task #{parent.pk}")
        child = Schedule.objects.get(prompt="Child follow up")
        self.assertEqual(child.owner, self.user)
        self.assertEqual(child.conversation, conversation)
        self.client.post(f"/schedules/{parent.pk}/cancel/")
        child.refresh_from_db()
        self.assertFalse(child.canceled)
        self.assertIsNotNone(child.next_run)
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other"))
        self.assertEqual(other.post(f"/schedules/{child.pk}/cancel/").status_code, 404)
        self.assertEqual(self.client.post(f"/schedules/{child.pk}/cancel/").status_code, 302)

    def test_required_decision_shows_partial_work_and_keeps_daily_timetable(self):
        from superharry.models import Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="daily",
            wall_time=time(12), preferred_model="test/free-model")
        arguments = json.dumps({"question": "Which contract should I sign?"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "decision-1", "function": {"name": "request_user_input", "arguments": arguments}}]}}]}
        payload = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.gateway_responses.append((200, b'data: {"choices":[{"delta":{"content":"Prepared the report."}}]}\n\n' + payload))
        self.gateway_responses.append((200, payload))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(schedule.occurrences.get().status, "needs_user_input")
        schedule.refresh_from_db()
        self.assertEqual(schedule.next_run, datetime(2099, 1, 2, 12, tzinfo=timezone.utc))
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, "Prepared the report.")
        self.assertContains(page, "Needs user input: Which contract should I sign?")
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 2, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(list(schedule.occurrences.order_by("due_at").values_list("status", flat=True)), ["needs_user_input", "complete"])

    def test_scheduled_model_receives_unavailable_user_guidance_and_can_continue(self):
        from superharry.models import ChatSubmission, Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Check the report",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        arguments = json.dumps({"question": "Which format should I use?"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "decision-1", "function": {"name": "request_user_input", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        self.gateway_responses.append((200, b'data: {"choices":[{"delta":{"content":"Used the recommended format."}}]}\n\ndata: [DONE]\n\n'))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(len(self.requests), 2)
        messages = self.requests[-1][2]["messages"]
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("The user is unavailable right now", messages[-1]["content"])
        self.assertEqual(schedule.occurrences.get().status, "complete")
        self.assertEqual(ChatSubmission.objects.filter(owner=self.user, submitted_day__isnull=False).count(), 1)
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Used the recommended format.")

    def test_model_cannot_schedule_another_users_input(self):
        from superharry.models import ChatInput

        other = get_user_model().objects.create_user(username="other")
        item = ChatInput.objects.create(owner=other, name="private.txt", media_type="text/plain", payload=b"hidden")
        arguments = json.dumps({"prompt": "Review", "due_at": "2099-01-01T12:00:00Z", "input_ids": [item.pk]})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Schedule a review"})
        notice = b"".join(response.streaming_content).decode()
        self.assertIn("could not be created", notice)
        self.assertNotIn("private.txt", notice)
        self.assertNotIn("hidden", notice)
        self.assertEqual(self.client.post(f"/inputs/{item.pk}/delete/").status_code, 404)

    def test_scheduled_image_is_sent_to_the_gateway_as_image_input(self):
        from superharry.models import ChatInput

        png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO7nFQAAAABJRU5ErkJggg==")
        self.client.post("/inputs/", {"file": SimpleUploadedFile("pixel.png", png, content_type="image/png")})
        selected = ChatInput.objects.get(owner=self.user)
        arguments = json.dumps({"prompt": "Inspect this image", "due_at": "2099-01-01T12:00:00Z", "input_ids": [selected.pk]})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Inspect the uploaded image later"})
        list(response.streaming_content)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        outbound = json.dumps(self.requests[-1][2])
        self.assertIn("data:image/png;base64," + base64.b64encode(png).decode(), outbound)

    def test_schedule_reports_input_that_changed_ownership_without_exposing_contents(self):
        from superharry.models import ChatInput, Conversation, Schedule

        other = get_user_model().objects.create_user(username="other")
        item = ChatInput.objects.create(owner=self.user, name="private.txt", media_type="text/plain", payload=b"Private secret content")
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once",
            preferred_model="test/free-model", input_ids=[item.pk])
        item.owner = other
        item.save(update_fields=("owner",))
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(self.requests, [])
        page = self.client.get(f"/chat/{conversation.pk}/")
        self.assertContains(page, f"input #{item.pk} is missing or inaccessible")
        self.assertNotContains(page, "Private secret content")
        self.assertNotContains(page, "private.txt")

    def test_schedule_reports_a_deleted_input(self):
        from superharry.models import ChatInput, Conversation, Schedule

        item = ChatInput.objects.create(owner=self.user, name="report.txt", media_type="text/plain", payload=b"report")
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once",
            preferred_model="test/free-model", input_ids=[item.pk])
        item_id = item.pk
        item.delete()
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(self.requests, [])
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), f"input #{item_id} is missing or inaccessible")

    def test_scheduled_run_receives_only_the_selected_owned_input(self):
        from superharry.models import ChatInput

        self.client.post("/inputs/", {"file": SimpleUploadedFile("report.txt", b"Quarterly report", content_type="text/plain")})
        selected = ChatInput.objects.get(owner=self.user)
        arguments = json.dumps({"prompt": "Review the selected report", "due_at": "2099-01-01T12:00:00Z", "input_ids": [selected.pk]})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Review the uploaded report later"})
        list(response.streaming_content)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        outbound = json.dumps(self.requests[-1][2], ensure_ascii=False)
        self.assertIn("Quarterly report", outbound)
        self.assertIn("Review the selected report", outbound)
        self.assertContains(self.client.get(response["Location"]), "Hello there")

    def test_user_can_upload_a_private_text_input_for_scheduled_work(self):
        upload = SimpleUploadedFile("report.txt", b"Quarterly report", content_type="text/plain")
        response = self.client.post("/inputs/", {"file": upload})
        self.assertEqual(response.status_code, 302)
        page = self.client.get("/inputs/")
        self.assertContains(page, "report.txt")
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="other"))
        self.assertNotContains(other.get("/inputs/"), "report.txt")

    def test_completed_one_time_schedule_releases_its_limit_slot(self):
        from superharry.models import ChatLimits, Conversation, Schedule

        ChatLimits.objects.filter(pk=1).update(max_active_schedules=1)
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="First",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        arguments = json.dumps({"prompt": "Second", "due_at": "2099-01-02T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post(f"/chat/{conversation.pk}/send/", {"message": "Schedule second"})
        self.assertIn("Scheduled task #", b"".join(response.streaming_content).decode())

    def test_schedule_cap_can_be_configured_and_canceling_releases_a_slot(self):
        from superharry.models import ChatLimits, Conversation, Schedule

        admin = Client()
        admin.force_login(get_user_model().objects.create_superuser(username="limit-admin"))
        result = admin.post("/admin/superharry/chatlimits/1/change/", {
            "daily_messages": 50, "max_response_characters": 12000, "max_active_schedules": 1,
        })
        self.assertEqual(result.status_code, 302)
        conversation = Conversation.objects.create(owner=self.user, title="Work")
        existing = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="First",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        arguments = json.dumps({"prompt": "Second", "due_at": "2099-01-02T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        payload = b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"
        self.gateway_responses.append((200, payload))
        first = self.client.post(f"/chat/{conversation.pk}/send/", {"message": "Schedule second"})
        self.assertIn("schedule limit", b"".join(first.streaming_content).decode().lower())
        self.assertEqual(self.client.post(f"/schedules/{existing.pk}/cancel/").status_code, 302)
        self.gateway_responses.append((200, payload))
        second = self.client.post(f"/chat/{conversation.pk}/send/", {"message": "Schedule second again"})
        self.assertIn("Scheduled task #", b"".join(second.streaming_content).decode())

    def test_scheduled_run_reports_a_model_that_is_no_longer_available(self):
        from superharry.models import Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/removed")
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(self.requests, [])
        self.assertContains(self.client.get(f"/chat/{conversation.pk}/"), "Scheduled task failed: The preferred model is unavailable.")

    def test_owner_can_change_schedule_model_or_use_active_model(self):
        from superharry.models import ChatRoute, Conversation, Schedule

        conversation = Conversation.objects.create(owner=self.user, title="Work")
        schedule = Schedule.objects.create(owner=self.user, conversation=conversation, prompt="Review",
            next_run=datetime(2099, 1, 1, 12, tzinfo=timezone.utc), timezone="UTC", repetition="once", preferred_model="test/free-model")
        stranger = Client()
        stranger.force_login(get_user_model().objects.create_user(username="other"))
        self.assertEqual(stranger.post(f"/schedules/{schedule.pk}/model/", {"model": "test/alternate"}).status_code, 404)
        self.assertEqual(self.client.post(f"/schedules/{schedule.pk}/model/", {"model": "test/alternate"}).status_code, 302)
        self.assertContains(self.client.get("/schedules/"), "test/alternate")
        self.assertEqual(self.client.post(f"/schedules/{schedule.pk}/model/", {"model": ""}).status_code, 302)
        ChatRoute.select("test/alternate")
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(self.requests[-1][2]["model"], "test/alternate")

    def test_weekly_schedule_uses_first_occurrence_of_ambiguous_local_time(self):
        arguments = json.dumps({"prompt": "Review", "due_at": "2026-11-01T06:30:00Z", "timezone": "America/New_York", "repetition": "weekly"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        with patch("superharry.chat.timezone.now", return_value=datetime(2026, 10, 1, tzinfo=timezone.utc)):
            response = self.client.post("/chat/send/", {"message": "Review weekly"})
            notice = b"".join(response.streaming_content).decode()
        page = self.client.get(response["Location"])
        self.assertContains(page, "Weekly")
        self.assertIn("2026-11-01T05:30:00+00:00", notice)

    def test_model_creates_a_daily_schedule_in_the_requested_time_zone(self):
        arguments = json.dumps({"prompt": "Review the report", "due_at": "2099-01-01T14:00:00Z", "timezone": "America/New_York", "repetition": "daily"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Review it daily"})
        notice = b"".join(response.streaming_content).decode()
        self.assertIn("daily", notice)
        page = self.client.get(response["Location"])
        self.assertContains(page, "Review the report")
        self.assertContains(page, "America/New_York")
        self.assertContains(page, "Daily")
        self.assertContains(page, "Cancel schedule")

    def test_schedule_uses_the_explicit_configured_model(self):
        arguments = json.dumps({"prompt": "Review the report", "due_at": "2099-01-01T12:00:00Z", "preferred_model": "test/alternate"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Review it later"})
        list(response.streaming_content)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertEqual(self.requests[-1][2]["model"], "test/alternate")
        self.assertContains(self.client.get(response["Location"]), "Hello there")

    def test_schedule_with_unavailable_text_skill_reports_failure(self):
        arguments = json.dumps({"prompt": "/missing-skill notes", "due_at": "2099-01-01T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Do this later"})
        list(response.streaming_content)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        self.assertContains(self.client.get(response["Location"]), "Scheduled task failed: Unknown text skill.")

    def test_scheduled_text_skill_uses_the_same_instructions_as_chat(self):
        arguments = json.dumps({"prompt": "/summarize First draft notes", "due_at": "2099-01-01T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Summarize these notes later"})
        list(response.streaming_content)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        outbound = self.requests[-1][2]
        self.assertEqual(outbound["messages"][-1], {"role": "user", "content": "First draft notes"})
        self.assertIn("Summarize the user", outbound["messages"][1]["content"])
        self.assertContains(self.client.get(response["Location"]), "Hello there")

    def test_model_creates_one_time_schedule_from_chat(self):
        arguments = json.dumps({"prompt": "Check the report", "due_at": "2099-01-01T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Check the report later"})
        list(response.streaming_content)
        page = self.client.get(response["Location"])
        self.assertContains(page, "Check the report")
        self.assertContains(page, "America/Guayaquil")
        self.assertContains(page, "Cancel schedule")

    def test_created_schedule_runs_and_reply_is_identified_in_chat(self):
        arguments = json.dumps({"prompt": "Review the report", "due_at": "2099-01-01T12:00:00Z"})
        event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1", "type": "function", "function": {"name": "create_schedule", "arguments": arguments}}]}}]}
        self.gateway_responses.append((200, b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n"))
        response = self.client.post("/chat/send/", {"message": "Review it later"})
        list(response.streaming_content)
        with patch("superharry.scheduler.timezone.now", return_value=datetime(2099, 1, 1, 12, 1, tzinfo=timezone.utc)):
            call_command("run_schedules", stdout=StringIO())
        page = self.client.get(response["Location"])
        self.assertContains(page, "Scheduled task #")
        self.assertContains(page, "Hello there")
        self.assertEqual(self.requests[-1][2]["model"], "test/free-model")
        self.assertEqual(self.requests[-1][2]["messages"][-1]["content"], "Review the report")

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="chat-user")
        self.client.force_login(self.user)
        self.requests = []
        self.gateway_responses = deque()
        requests = self.requests
        gateway_responses = self.gateway_responses

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test/alternate"},{"id":"test/free-model"}]}')

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
            "max_active_schedules": 10,
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
        sent_tools = self.requests[0][2].pop("tools")
        self.assertEqual(sent_tools[0]["function"]["name"], "create_schedule")
        self.assertEqual(self.requests, [(
            "/v1/chat/completions",
            "Bearer test-gateway-key",
            {
                "model": "test/free-model",
                "stream": True,
                "messages": [{"role": "system", "content": guidance()}, {"role": "user", "content": "Hi"}],
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
        self.assertEqual(self.requests[-1][2]["messages"][1:], [
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

    def test_saved_memory_is_opt_in_and_scoped_to_one_conversation_and_owner(self):
        self.client.post("/memories/new/", {"content": "Coffee should be served black."})
        self.client.post("/memories/new/", {"content": "Prefer window seats."})
        other = Client()
        other.force_login(get_user_model().objects.create_user(username="another-memory-owner"))
        other.post("/memories/new/", {"content": "Coffee with another account's secret."})

        first = self.client.post("/chat/send/", {"message": "What coffee do I prefer?"})
        list(first.streaming_content)
        history = first["Location"]
        self.assertNotIn("Coffee should be served black", json.dumps(self.requests[-1][2]))

        self.assertEqual(other.post(f"{history}memory/", {"memory_enabled": "on"}).status_code, 404)
        self.assertRedirects(
            self.client.post(f"{history}memory/", {"memory_enabled": "on"}), history
        )
        self.assertContains(self.client.get(history), "Memory is on")
        followup = self.client.post(f"{history}send/", {"message": "Coffee again?"})
        list(followup.streaming_content)
        outbound = json.dumps(self.requests[-1][2])
        self.assertIn("Coffee should be served black.", outbound)
        self.assertNotIn("window seats", outbound)
        self.assertNotIn("another account", outbound)

        separate = self.client.post("/chat/send/", {"message": "Coffee in a separate chat"})
        list(separate.streaming_content)
        self.assertNotIn("Coffee should be served black", json.dumps(self.requests[-1][2]))

    def test_text_skill_is_invoked_without_tools(self):
        self.assertContains(self.client.get("/"), "/summarize")
        response = self.client.post("/chat/send/", {"message": "/summarize First draft notes"})
        self.assertEqual(
            [json.loads(chunk) for chunk in response.streaming_content][-1], {"done": True}
        )
        outbound = self.requests[-1][2]
        self.assertNotIn("tools", outbound)
        self.assertEqual(outbound["messages"][-1], {"role": "user", "content": "First draft notes"})
        self.assertIn("Summarize the user", outbound["messages"][1]["content"])
        history = self.client.get(response["Location"])
        self.assertContains(history, "/summarize First draft notes")
        self.assertContains(history, "Hello there")

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

    def test_closing_stream_after_partial_reply_releases_submission(self):
        from superharry.models import ChatSubmission, Message

        self.gateway_responses.append(
            (200, b'data: {"choices":[{"delta":{"content":"Partial reply"}}]}\n\n'
             b'data: {"choices":[{"delta":{"content":"Later"}}]}\n\n'
             b'data: [DONE]\n\n')
        )
        response = self.client.post("/chat/send/", {"message": "Question"})
        self.assertEqual(json.loads(next(response.streaming_content)), {"text": "Partial reply"})
        response.close()
        reply = Message.objects.get(role="assistant")
        self.assertEqual(reply.content, "Partial reply")
        self.assertFalse(reply.complete)
        self.assertFalse(reply.in_progress)
        self.assertFalse(ChatSubmission.objects.get(reply=reply).active)

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
        first_request = json.loads(json.dumps(self.requests[0]))
        retried_request = json.loads(json.dumps(self.requests[1]))
        for request in (first_request, retried_request):
            request[2]["tools"][0]["function"].pop("description")
        self.assertEqual(first_request, retried_request)
        page = self.client.get(history)
        self.assertContains(page, "<p>Original question</p>", count=1, html=True)
        self.assertContains(page, "Partial reply")
        self.assertContains(page, "Hello there")
        self.assertNotContains(page, "Retry reply")
        self.assertEqual(self.client.post(retry_url).status_code, 409)
        self.assertEqual(len(self.requests), 2)

    def test_free_route_failures_are_reported_without_fallback(self):
        http_error = b'{"error":"private upstream details"}'
        for status, body, expected in (
            (429, http_error, "The free route has exhausted its quota or rate limit."),
            (503, http_error, "The free route is currently unavailable."),
            (
                200,
                b'data: {"error":{"code":"model_cooldown","type":"rate_limit_error"}}\n\n',
                "The free route has exhausted its quota or rate limit.",
            ),
        ):
            with self.subTest(status=status):
                self.gateway_responses.append((status, body))
                previous = len(self.requests)
                response = self.client.post("/chat/send/", {"message": "Hello"})
                self.assertEqual(
                    [json.loads(chunk) for chunk in response.streaming_content],
                    [{"error": expected}],
                )
                self.assertContains(self.client.get(response["Location"]), "Incomplete reply")
                self.assertEqual(len(self.requests), previous + 1)
                self.assertEqual(self.requests[-1][2]["model"], "test/free-model")
