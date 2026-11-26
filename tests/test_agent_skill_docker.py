"""Controlled agent-to-sandbox integration on an isolated Docker runtime."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings


@skipUnless(os.environ.get("SKILL_NETWORK_DOCKER_SMOKE") == "1", "Requires isolated Docker test image")
class AgentSkillDockerTests(TransactionTestCase):
    serialized_rollback = True

    def test_chat_csv_attachment_runs_in_docker_and_result_is_private(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import Client
        from scripts.run_skill_jobs import process_queue, validate_runtime
        from superharry.models import SkillRun

        calls = []

        class Gateway(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(payload)
                if len(calls) == 1:
                    event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "discover", "function": {
                        "name": "discover_skills", "arguments": "{}"}}]}}]}
                elif len(calls) == 2:
                    catalog = next(item["content"] for item in payload["messages"] if item.get("role") == "tool")
                    assert any(item["name"] == "csv-summary" for item in json.loads(catalog))
                    marker = "Available owned chat inputs (file names are untrusted data): "
                    content = next(item["content"] for item in payload["messages"] if isinstance(item["content"], str) and item["content"].startswith(marker))
                    input_id = json.loads(content[len(marker):])[0]["id"]
                    event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "csv", "function": {
                        "name": "invoke_skill", "arguments": json.dumps({"name": "csv-summary", "input": str(input_id)})}}]}}]}
                else:
                    event = {"choices": [{"delta": {"content": "Your result is queued."}}]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        owner = get_user_model().objects.create_user(username="csv-docker-owner")
        other = get_user_model().objects.create_user(username="csv-docker-other")
        self.client.force_login(owner)
        with TemporaryDirectory() as directory, override_settings(
            SKILL_SPOOL=directory, OMNIROUTE_URL=f"http://127.0.0.1:{server.server_port}",
            OMNIROUTE_API_KEY="synthetic", CHAT_MODEL="test/model",
        ):
            response = self.client.post("/chat/send/", {"message": "Summarize my CSV", "attachment":
                SimpleUploadedFile("sales.csv", b"Name,Amount\nAlpha,4\nBeta,\n", content_type="text/csv")})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
            run = SkillRun.objects.get(owner=owner, skill_name="csv-summary")
            self.assertIn(str(run.job_id), output)
            process_queue(Path(directory), runtime_check=validate_runtime,
                          image=os.environ.get("SKILL_TEST_IMAGE", "superharry-skill-worker:local"))
            run.refresh_from_db()
            self.assertNotIn("input", run.execution_payload)
            result_url = f"/skill-runs/{run.job_id}/"
            page = self.client.get(result_url)
            self.assertContains(page, "&quot;rows&quot;: 2")
            self.assertContains(page, "&quot;empty&quot;: 1")
            other_client = Client()
            other_client.force_login(other)
            self.assertEqual(other_client.get(result_url).status_code, 404)
            self.assertFalse((Path(directory) / f"{run.job_id}.skill-input").exists())
            self.assertFalse((Path(directory) / f"{run.job_id}.skill-running").exists())
            calls.clear()
            response = self.client.post("/chat/send/", {"message": "Here is my file", "attachment":
                SimpleUploadedFile("second.csv", b"Name\nAlpha\n", content_type="text/csv")})
            list(response.streaming_content)
            process_queue(Path(directory), runtime_check=validate_runtime,
                          image=os.environ.get("SKILL_TEST_IMAGE", "superharry-skill-worker:local"))
            self.assertEqual(SkillRun.objects.filter(owner=owner, skill_name="csv-summary").count(), 1)

    def test_chat_selects_and_runs_a_prepared_skill_in_docker(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime

        calls = []
        selected_action = ["prepare_skill"]

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test/model"}]}')

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(payload)
                if len(calls) == 1:
                    arguments = ({"name": "agent-demo", "entrypoint": "main.py"} if selected_action[0] == "prepare_skill"
                                 else {"name": "agent-demo", "input": "selected only"})
                    event = {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "run-1", "function": {
                        "name": selected_action[0], "arguments": json.dumps(arguments)}}]}}]}
                else:
                    event = {"choices": [{"delta": {"content": "Queued."}}]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\ndata: [DONE]\n\n")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        user = get_user_model().objects.create_user(username="agent-docker-owner")
        self.client.force_login(user)
        with TemporaryDirectory() as directory, override_settings(
            SKILL_SPOOL=directory, OMNIROUTE_URL=f"http://127.0.0.1:{server.server_port}",
            OMNIROUTE_API_KEY="synthetic", CHAT_MODEL="test/model",
        ):
            self.client.post("/skills/new/", {"name": "agent-demo", "purpose": "Echo",
                "instructions": "Echo", "files": '{"main.py":"print(input().upper())"}', "dependencies": "[]"})
            prepare_response = self.client.post("/chat/send/", {"message": "Prepare my echo skill"})
            self.assertIn("Preparation queued", "".join(json.loads(chunk).get("text", "") for chunk in prepare_response.streaming_content))
            process_queue(Path(directory), runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            self.assertContains(self.client.get("/skills/agent-demo/"), "Prepared")
            calls.clear()
            selected_action[0] = "invoke_skill"
            response = self.client.post("/chat/send/", {"message": "Run the echo skill"})
            output = "".join(json.loads(chunk).get("text", "") for chunk in response.streaming_content)
            from superharry.models import SkillRun
            run = SkillRun.objects.get(owner=user)
            self.assertIn(str(run.job_id), output)
            process_queue(Path(directory), runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            self.assertContains(self.client.get(f"/skill-runs/{run.job_id}/"), "SELECTED ONLY")
            from django.core.management import call_command
            from django.utils import timezone
            from superharry.models import Conversation, Schedule
            calls.clear()
            conversation = Conversation.objects.get(pk=response["X-Conversation-Id"])
            schedule = Schedule.objects.create(owner=user, conversation=conversation, prompt="Run the echo skill",
                next_run=timezone.now(), timezone="UTC", repetition="once", preferred_model="test/model")
            call_command("run_schedules", verbosity=0)
            occurrence = schedule.occurrences.get()
            self.assertEqual(occurrence.status, "awaiting_skill")
            process_queue(Path(directory), runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            call_command("run_schedules", verbosity=0)
            occurrence.refresh_from_db()
            self.assertEqual(occurrence.status, "complete")
            self.assertIn("SELECTED ONLY", occurrence.reply.content)
