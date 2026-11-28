import json
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase


@skipUnless(os.environ.get("SKILL_BROWSER_TEST_IMAGE"), "Set SKILL_BROWSER_TEST_IMAGE for the Docker browser integration")
class BrowserSessionDockerTests(TransactionTestCase):
    def test_screenshot_becomes_owner_artifact_while_session_is_active_and_cancel_cleans_up(self):
        from scripts.run_skill_jobs import process_queue
        from superharry.csv_protocol import publish, read_limited
        from superharry.models import ArtifactDelivery, Conversation, Message, SkillArtifact, SkillRun

        owner = get_user_model().objects.create_user(username="browser-session-docker-owner", password="test")
        other = get_user_model().objects.create_user(username="browser-session-docker-other", password="test")
        conversation = Conversation.objects.create(owner=owner, title="Docker session fixture")
        prompt = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Read the fixture page")
        reply = Message.objects.create(conversation=conversation, prompt=prompt, role=Message.Role.ASSISTANT,
                                       content="", complete=True)
        job_id = uuid4()
        run = SkillRun.objects.create(
            owner=owner, skill_name="playwright", repository_skill=True, reply=reply,
            job_id=job_id, execution_payload={"runtime": "playwright-browser"},
        )
        token = "docker-session-consent-token-01234567890123456789"

        class FixtureMediator:
            def retrieve_browser(self, action, *, authorized=None, cancelled=None):
                if cancelled is not None and cancelled.is_set():
                    raise PermissionError("Cancelled")
                if authorized is not None and not authorized():
                    raise PermissionError("Consent ended")
                if action.get("method") != "GET" or action.get("url") != "https://fixture.example/article":
                    raise ValueError("Unexpected fixture request")
                body = ("<!doctype html><html><head><title>Docker session fixture</title></head>"
                        "<body><h1>Docker session page text</h1></body></html>").encode()
                return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}

        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            directory = Path(temp)
            request = {
                "runtime": "playwright-browser", "target_url": "https://fixture.example/article",
                "_browser_owner_id": owner.pk, "_browser_consent_token": token,
                "browser_session": True,
                "browser_handoff": False,
            }
            consent = {
                "job_id": str(job_id), "owner_id": owner.pk, "token": token,
                "expires_at": time.time() + 300,
            }
            publish(directory / f"{job_id}.browser-consent", json.dumps(consent).encode(), replace=False)
            publish(directory / f"{job_id}.skill-input", json.dumps(request).encode(), replace=False)

            failures = []

            def execute():
                from django.db import close_old_connections

                close_old_connections()
                try:
                    process_queue(directory, browser_image=os.environ["SKILL_BROWSER_TEST_IMAGE"],
                                  mediator=FixtureMediator())
                except Exception as error:
                    failures.append(error)
                finally:
                    close_old_connections()

            worker = threading.Thread(target=execute, daemon=True)
            worker.start()
            owner_client = Client()
            owner_client.force_login(owner)
            try:
                deadline = time.monotonic() + 45
                view_path = directory / f"{job_id}.browser-view"
                result_path = directory / f"{job_id}.skill-result"
                while time.monotonic() < deadline and not (view_path.exists() and result_path.exists()) and not failures:
                    time.sleep(0.1)
                self.assertTrue(view_path.exists(), f"Browser view was not published; failures={failures}")
                self.assertTrue(result_path.exists(), "Page result was not published before screenshot")
                self.assertEqual(json.loads(read_limited(result_path, 100 * 1024))["status"], "complete")

                request_response = owner_client.post(f"/skill-runs/{job_id}/browser-screenshot/")
                self.assertEqual(request_response.status_code, 202, request_response.content)
                command = json.loads(read_limited(directory / f"{job_id}.browser-command", 2048))
                self.assertEqual(command["owner_id"], owner.pk)

                artifact_status = None
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    artifact_status = owner_client.get(f"/skill-runs/{job_id}/artifacts/")
                    if any(item["name"] == "browser-screenshot.jpg" for item in artifact_status.json()["artifacts"]):
                        break
                    if failures:
                        break
                    time.sleep(0.15)
                self.assertFalse(failures, f"Browser worker failed: {failures}")
                self.assertIsNotNone(artifact_status)
                delivered = next((item for item in artifact_status.json()["artifacts"]
                                  if item["name"] == "browser-screenshot.jpg"), None)
                self.assertIsNotNone(delivered, f"Screenshot was not imported while the session remained active: {artifact_status.json()}")
                self.assertFalse(artifact_status.json()["pending"])
                artifact = SkillArtifact.objects.get(run=run, name="browser-screenshot.jpg")
                self.assertTrue(bytes(artifact.payload).startswith(b"\xff\xd8\xff"))
                self.assertEqual(ArtifactDelivery.objects.get(run=run, name="browser-screenshot.jpg").artifact_id, artifact.pk)
                self.assertEqual(owner_client.get(delivered["url"]).status_code, 200)
                other_client = Client()
                other_client.force_login(other)
                self.assertEqual(other_client.get(delivered["url"]).status_code, 404)

                self.assertEqual(owner_client.post(f"/skill-runs/{job_id}/browser-decision/", {"decision": "cancel"}).status_code, 302)
                worker.join(timeout=25)
                self.assertFalse(worker.is_alive(), "Owner cancellation did not end the browser session")
                self.assertFalse(failures, f"Browser worker failed during cleanup: {failures}")
                self.assertFalse((directory / f"{job_id}.skill-running").exists())
                self.assertFalse(view_path.exists())
                self.assertEqual(json.loads(read_limited(result_path, 100 * 1024))["status"], "complete")
            finally:
                if worker.is_alive():
                    try:
                        owner_client.post(f"/skill-runs/{job_id}/browser-decision/", {"decision": "cancel"})
                    except Exception:
                        pass
                    worker.join(timeout=20)
                if worker.is_alive():
                    from scripts.skill_sandbox import remove_container

                    remove_container(job_id)
                    worker.join(timeout=10)
                self.assertFalse(worker.is_alive(), "Browser worker was not cleaned up after the test")
