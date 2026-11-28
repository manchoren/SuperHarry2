import base64
import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase


class BrowserSessionPanelTests(TransactionTestCase):
    def setUp(self):
        from superharry.models import ChatLimits

        ChatLimits.objects.get_or_create(pk=1)
        self.owner = get_user_model().objects.create_user(username="browser-owner", password="test")
        self.other = get_user_model().objects.create_user(username="browser-other", password="test")
        self.job_id = uuid4()
        self.token = "c" * 48

    def publish_active_session(self, directory, *, handoff=False):
        from superharry.csv_protocol import publish

        request = {
            "runtime": "playwright-browser", "browser_session": True, "browser_handoff": handoff,
            "target_url": "https://fixture.example/login" if handoff else "https://fixture.example/article",
            "_browser_owner_id": self.owner.pk, "_browser_consent_token": self.token,
        }
        consent = {
            "job_id": str(self.job_id), "owner_id": self.owner.pk, "token": self.token,
            "expires_at": time.time() + 180,
        }
        publish(directory / f"{self.job_id}.skill-running", json.dumps(request).encode(), replace=False)
        publish(directory / f"{self.job_id}.browser-consent", json.dumps(consent).encode(), replace=False)
        publish(directory / f"{self.job_id}.browser-view", b"\xff\xd8\xff\xd9", replace=False)

    def test_owner_action_status_and_decision_are_task_scoped_csrf_protected_and_one_use(self):
        from superharry import browser_handoff

        run = self.make_run()
        run.execution_payload = {"runtime": "playwright-browser", "browser_handoff": True}
        run.save(update_fields=("execution_payload",))
        digest = "f" * 64
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            self.publish_active_session(Path(temp), handoff=True)
            expires = time.time() + 180
            browser_handoff.register(self.job_id, self.owner.pk, self.token, expires)
            self.assertTrue(browser_handoff.take_control(self.job_id, self.owner.pk))
            proposal = browser_handoff.propose_action(
                self.job_id, self.owner.pk, self.token, digest=digest, method="POST",
                url="https://fixture.example/profile/update", summary="Save profile",
                form_text="Update your profile", fields=[{"name": "display_name", "value": "Ada"}],
                expires_at=expires - 1,
            )
            self.assertIsNotNone(proposal)
            owner_client = Client()
            owner_client.force_login(self.owner)
            other_client = Client()
            other_client.force_login(self.other)
            status_path = f"/skill-runs/{self.job_id}/browser-action/status/"
            decision_path = f"/skill-runs/{self.job_id}/browser-action/decision/"
            self.assertEqual(other_client.get(status_path).status_code, 404)
            self.assertEqual(other_client.post(decision_path, {
                "proposal_id": proposal["id"], "digest": digest, "decision": "approve",
            }).status_code, 404)
            self.assertEqual(owner_client.get(status_path).json()["proposal"]["summary"], "Save profile")
            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.owner)
            self.assertEqual(csrf_client.post(decision_path, {
                "proposal_id": proposal["id"], "digest": digest, "decision": "approve",
            }).status_code, 403)
            self.assertEqual(owner_client.post(decision_path, {
                "proposal_id": proposal["id"], "digest": "0" * 64, "decision": "approve",
            }).status_code, 409)
            self.assertEqual(owner_client.post(decision_path, {
                "proposal_id": proposal["id"], "digest": digest, "decision": "approve",
            }).status_code, 200)
            self.assertEqual(browser_handoff.wait_action_decision(
                self.job_id, self.owner.pk, self.token, proposal["id"], digest, timeout=0.1,
            ), "approve")
            self.assertEqual(owner_client.post(decision_path, {
                "proposal_id": proposal["id"], "digest": digest, "decision": "approve",
            }).status_code, 409)

    def test_agent_action_planner_bridge_is_owner_scoped_and_one_use(self):
        from superharry import browser_handoff

        run = self.make_run()
        run.execution_payload = {
            "runtime": "playwright-browser", "browser_handoff": True,
            "action_request": "Set my display name to Ada", "action_route_id": "test/free-model",
        }
        run.save(update_fields=("execution_payload",))
        forms = [{
            "index": 0, "url": "https://fixture.example/profile/update", "method": "POST",
            "summary": "Save profile", "form_text": "Profile settings | Display name",
            "fields": [{"name": "display_name", "type": "text", "label": "Display name",
                        "required": True, "options": []}],
        }]
        model_plan = json.dumps({"form_index": 0, "summary": "Update display name to Ada", "fields": [
            {"name": "display_name", "value": "Ada"},
        ]})
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp), patch(
            "superharry.browser_action_plans.stream_reply", return_value=iter([model_plan]),
        ) as stream:
            self.publish_active_session(Path(temp), handoff=True)
            expires = time.time() + 180
            browser_handoff.register(self.job_id, self.owner.pk, self.token, expires)
            self.assertTrue(browser_handoff.take_control(self.job_id, self.owner.pk))
            path = f"/skill-runs/{self.job_id}/browser-handoff/bridge/"
            payload = {"operation": "prepare_action", "owner_id": self.owner.pk, "forms": forms}
            response = Client().post(path, data=json.dumps(payload), content_type="application/json",
                                     HTTP_X_BROWSER_TASK_CAPABILITY=self.token)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["plan"]["fields"], [{"name": "display_name", "value": "Ada"}])
            self.assertEqual(stream.call_args.kwargs["route_id"], "test/free-model")
            replay = Client().post(path, data=json.dumps(payload), content_type="application/json",
                                   HTTP_X_BROWSER_TASK_CAPABILITY=self.token)
            self.assertEqual(replay.status_code, 409)

    def make_run(self):
        from superharry.models import SkillRun
        from superharry.models import Conversation, Message

        conversation = Conversation.objects.create(owner=self.owner, title="Browser conversation")
        prompt = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Read the public page")
        reply = Message.objects.create(conversation=conversation, prompt=prompt, role=Message.Role.ASSISTANT,
                                       content="Page result", complete=True)

        return SkillRun.objects.create(
            owner=self.owner, skill_name="playwright", repository_skill=True,
            job_id=self.job_id, reply=reply, execution_payload={"runtime": "playwright-browser"},
        )

    def test_embedded_conversation_is_same_origin_and_scoped_to_task_owner(self):
        self.make_run()
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            self.publish_active_session(Path(temp))
            owner_client = Client()
            owner_client.force_login(self.owner)
            response = owner_client.get(f"/skill-runs/{self.job_id}/conversation/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-Frame-Options"], "SAMEORIGIN")
            self.assertContains(response, "Read the public page")
            other_client = Client()
            other_client.force_login(self.other)
            self.assertEqual(other_client.get(f"/skill-runs/{self.job_id}/conversation/").status_code, 404)

    def test_owner_can_reopen_same_image_and_request_idempotent_screenshot_after_early_result(self):
        from superharry.csv_protocol import publish, read_limited
        from superharry.models import SkillArtifact, ArtifactDelivery

        run = self.make_run()
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            directory = Path(temp)
            self.publish_active_session(directory)
            publish(directory / f"{self.job_id}.skill-result", json.dumps({
                "status": "complete", "output": "Public page summary", "citations": [
                    "https://fixture.example/article",
                ], "generation": 0,
            }).encode(), replace=False)

            owner_client = Client()
            owner_client.force_login(self.owner)
            image = owner_client.get(f"/skill-runs/{self.job_id}/browser-view/")
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image["Content-Type"], "image/jpeg")
            self.assertEqual(image.content, b"\xff\xd8\xff\xd9")
            self.assertIn("no-store", image["Cache-Control"])

            first = owner_client.post(f"/skill-runs/{self.job_id}/browser-screenshot/")
            second = owner_client.post(f"/skill-runs/{self.job_id}/browser-screenshot/")
            self.assertEqual(first.status_code, 202)
            self.assertEqual(second.status_code, 202)
            self.assertEqual(first.json()["command_id"], second.json()["command_id"])
            self.assertEqual(json.loads(read_limited(directory / f"{self.job_id}.browser-command", 2048)), {
                "job_id": str(self.job_id), "owner_id": self.owner.pk,
                "command_id": first.json()["command_id"], "command": "capture_screenshot",
                "expires_at": json.loads(read_limited(directory / f"{self.job_id}.browser-command", 2048))["expires_at"],
            })

            other_client = Client()
            other_client.force_login(self.other)
            self.assertEqual(other_client.get(f"/skill-runs/{self.job_id}/browser-view/").status_code, 404)
            self.assertEqual(other_client.post(f"/skill-runs/{self.job_id}/browser-screenshot/").status_code, 404)

            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(self.owner)
            self.assertEqual(csrf_client.post(f"/skill-runs/{self.job_id}/browser-screenshot/").status_code, 403)

            screenshot = b"\xff\xd8\xff\xd9"
            artifact_envelope = {"generation": 0, "files": [{
                "name": "browser-screenshot.jpg", "data": base64.b64encode(screenshot).decode("ascii"),
            }]}
            publish(directory / f"{self.job_id}.skill-artifacts", json.dumps(artifact_envelope).encode(), replace=True)
            publish(directory / f"{self.job_id}.browser-screenshot-complete", b"complete", replace=True)
            status = owner_client.get(f"/skill-runs/{self.job_id}/artifacts/")
            self.assertEqual(status.status_code, 200)
            self.assertFalse(status.json()["pending"])
            artifact = SkillArtifact.objects.get(run=run, name="browser-screenshot.jpg")
            self.assertEqual(bytes(artifact.payload), screenshot)
            delivery = ArtifactDelivery.objects.get(run=run, name="browser-screenshot.jpg")
            self.assertEqual(delivery.artifact_id, artifact.pk)
            self.assertEqual(owner_client.post(f"/skill-runs/{self.job_id}/browser-screenshot/").json(), {"status": "complete"})

    def test_session_end_hides_view_and_cancel_remains_available_after_answer(self):
        from superharry.csv_protocol import publish

        self.make_run()
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            directory = Path(temp)
            self.publish_active_session(directory)
            publish(directory / f"{self.job_id}.skill-result", json.dumps({
                "status": "complete", "output": "The page was read", "citations": [
                    "https://fixture.example/article",
                ], "generation": 0,
            }).encode(), replace=False)
            client = Client()
            client.force_login(self.owner)
            self.assertEqual(client.get(f"/skill-runs/{self.job_id}/browser-view/").status_code, 200)
            self.assertEqual(client.post(f"/skill-runs/{self.job_id}/browser-decision/", {"decision": "cancel"}).status_code, 302)
            self.assertFalse((directory / f"{self.job_id}.browser-consent").exists())

    def test_owner_handoff_blocks_screenshot_and_artifact_import(self):
        from superharry.csv_protocol import publish
        from superharry.models import SkillArtifact
        from superharry.skill_jobs import run_result

        run = self.make_run()
        run.execution_payload["browser_handoff"] = True
        run.save(update_fields=("execution_payload",))
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            directory = Path(temp)
            self.publish_active_session(directory)
            client = Client()
            client.force_login(self.owner)
            response = client.post(f"/skill-runs/{self.job_id}/browser-screenshot/")
            self.assertEqual(response.status_code, 409)
            self.assertFalse((directory / f"{self.job_id}.browser-command").exists())

            image = b"\xff\xd8\xff\xd9"
            publish(directory / f"{self.job_id}.skill-artifacts", json.dumps({"generation": 0, "files": [{
                "name": "browser-screenshot.jpg", "data": base64.b64encode(image).decode("ascii"),
            }]}).encode(), replace=False)
            publish(directory / f"{self.job_id}.skill-result", json.dumps({
                "status": "complete", "output": "Sanitized result", "citations": [], "generation": 0,
            }).encode(), replace=False)
            outcome = run_result(directory, run)
            self.assertEqual(outcome["output"], "Sanitized result")
            self.assertFalse((directory / f"{self.job_id}.skill-artifacts").exists())
            self.assertFalse(SkillArtifact.objects.filter(run=run).exists())

    def test_approval_rejects_http_when_owner_handoff_is_selected(self):
        from superharry.browser_tasks import consent_path
        from superharry.models import SkillOutbox

        run = self.make_run()
        outbox = SkillOutbox.objects.create(
            job_id=self.job_id, execution=run, requested_by=self.owner,
            state="awaiting_consent",
            payload={"runtime": "playwright-browser", "target_url": "http://fixture.example/login"},
        )
        client = Client()
        client.force_login(self.owner)
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            response = client.post(f"/skill-runs/{self.job_id}/browser-decision/", {
                "decision": "approve", "owner_handoff": "on",
            })
            self.assertEqual(response.status_code, 400)
            outbox.refresh_from_db()
            self.assertEqual(outbox.state, "awaiting_consent")
            self.assertEqual(outbox.payload["target_url"], "http://fixture.example/login")
            self.assertFalse(consent_path(Path(temp), self.job_id).exists())
