import json
import logging
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from django.contrib.auth import get_user_model
from django.core.handlers.wsgi import WSGIHandler
from django.test import Client, TransactionTestCase

from superharry import browser_handoff


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class BrowserHandoffBridgeTests(TransactionTestCase):
    def test_authenticated_page_continuation_requires_opt_in_and_synthesizes_only_page_context(self):
        from superharry.csv_protocol import publish
        from superharry.models import Conversation, Message, SkillRun

        owner = get_user_model().objects.create_user(username="handoff-model-owner", password="test")
        conversation = Conversation.objects.create(owner=owner, title="Authenticated page fixture")
        prompt = Message.objects.create(
            conversation=conversation, role=Message.Role.USER,
            content="Find the invoice amount in my signed-in account.",
        )
        reply = Message.objects.create(
            conversation=conversation, prompt=prompt, role=Message.Role.ASSISTANT, content="", complete=True,
        )
        identifier = uuid4()
        token = "handoff-model-capability-0123456789abcd"
        expires = time.time() + 180
        with TemporaryDirectory() as temporary, self.settings(SKILL_SPOOL=temporary):
            directory = Path(temporary)
            run = SkillRun.objects.create(
                owner=owner, reply=reply, skill_name="playwright", repository_skill=True,
                job_id=identifier, execution_payload={
                    "runtime": "playwright-browser", "target_url": "https://fixture.example/account",
                    "browser_handoff": True, "continue_with_model": True,
                    "answer_route_id": "test/selected-route", "owner_returned": False,
                    "handoff_cleaned": False,
                },
            )
            publish(directory / f"{identifier}.skill-running", json.dumps({
                "runtime": "playwright-browser", "target_url": "https://fixture.example/account",
                "browser_session": True, "browser_handoff": True, "continue_with_model": True,
                "_browser_owner_id": owner.pk, "_browser_consent_token": token,
            }).encode(), replace=False)
            publish(directory / f"{identifier}.browser-consent", json.dumps({
                "job_id": str(identifier), "owner_id": owner.pk, "token": token,
                "expires_at": expires,
            }).encode(), replace=False)
            browser_handoff.register(identifier, owner.pk, token, expires)
            client = Client()
            page = "Title: Invoices\\nOpened URL: https://fixture.example/account\\nPage text: Invoice total is $42."
            citations = ["https://fixture.example/account"]
            body = json.dumps({
                "operation": "synthesize_handoff_answer", "owner_id": owner.pk,
                "page_content": page, "citations": citations,
            })
            with patch("superharry.gateway.stream_reply", return_value=iter([
                "The invoice total is $42 [1]. See https://fixture.example/account.",
            ])) as gateway:
                premature = client.post(
                    f"/skill-runs/{identifier}/browser-handoff/bridge/", body,
                    content_type="application/json", HTTP_X_BROWSER_TASK_CAPABILITY=token,
                )
                self.assertEqual(premature.status_code, 410)
                gateway.assert_not_called()
                self.assertTrue(browser_handoff.take_control(identifier, owner.pk))
                self.assertTrue(browser_handoff.return_control(identifier, owner.pk))
                cleanup = client.post(
                    f"/skill-runs/{identifier}/browser-handoff/bridge/",
                    json.dumps({"operation": "cleanup", "owner_id": owner.pk}),
                    content_type="application/json", HTTP_X_BROWSER_TASK_CAPABILITY=token,
                )
                self.assertEqual(cleanup.status_code, 200, cleanup.content)
                response = client.post(
                    f"/skill-runs/{identifier}/browser-handoff/bridge/", body,
                    content_type="application/json", HTTP_X_BROWSER_TASK_CAPABILITY=token,
                )
                replay = client.post(
                    f"/skill-runs/{identifier}/browser-handoff/bridge/", body,
                    content_type="application/json", HTTP_X_BROWSER_TASK_CAPABILITY=token,
                )
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json(), {"answer": "The invoice total is $42 [1]. See https://fixture.example/account."})
            self.assertEqual(replay.json(), response.json())
            gateway.assert_called_once()
            self.assertEqual(gateway.call_args.kwargs["route_id"], "test/selected-route")
            sent = json.loads(gateway.call_args.args[0][1]["content"])
            self.assertEqual(sent["original_user_request"], prompt.content)
            self.assertEqual(sent["authenticated_page_excerpt_untrusted"], page)
            self.assertIn("untrusted", gateway.call_args.args[0][0]["content"])
            run.refresh_from_db()
            self.assertNotIn(page, json.dumps(run.execution_payload))

            opted_out_id = uuid4()
            SkillRun.objects.create(
                owner=owner, skill_name="playwright", repository_skill=True,
                job_id=opted_out_id, execution_payload={
                    "runtime": "playwright-browser", "browser_handoff": True,
                    "continue_with_model": False, "answer_route_id": "test/selected-route",
                },
            )
            opted_out_token = "handoff-model-opted-out-0123456789abc"
            publish(directory / f"{opted_out_id}.skill-running", json.dumps({
                "runtime": "playwright-browser", "target_url": "https://fixture.example/account",
                "browser_session": True, "browser_handoff": True, "continue_with_model": False,
                "_browser_owner_id": owner.pk, "_browser_consent_token": opted_out_token,
            }).encode(), replace=False)
            publish(directory / f"{opted_out_id}.browser-consent", json.dumps({
                "job_id": str(opted_out_id), "owner_id": owner.pk, "token": opted_out_token,
                "expires_at": expires,
            }).encode(), replace=False)
            refused = client.post(
                f"/skill-runs/{opted_out_id}/browser-handoff/bridge/",
                json.dumps({**json.loads(body), "owner_id": owner.pk}), content_type="application/json",
                HTTP_X_BROWSER_TASK_CAPABILITY=opted_out_token,
            )
            self.assertEqual(refused.status_code, 410)

    def test_owner_csrf_ram_bridge_redaction_and_cleanup_over_loopback_http(self):
        from scripts.run_skill_jobs import process_queue
        from superharry.csv_protocol import publish, read_limited
        from superharry.models import Conversation, Message, SkillArtifact, SkillRun

        owner = get_user_model().objects.create_user(username="handoff-bridge-owner", password="test")
        other = get_user_model().objects.create_user(username="handoff-bridge-other", password="test")
        conversation = Conversation.objects.create(owner=owner, title="Handoff bridge fixture")
        prompt = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Sign in")
        reply = Message.objects.create(conversation=conversation, prompt=prompt,
                                       role=Message.Role.ASSISTANT, content="", complete=True)
        identifier = uuid4()
        token = "bridge-capability-synthetic-0123456789abcd"
        sentinel = "SYNTHETIC_OWNER_PASSWORD_4d9a"
        expires = time.time() + 180

        server = make_server("127.0.0.1", 0, WSGIHandler(),
                             server_class=WSGIServer, handler_class=QuietRequestHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server_thread.join, 3)
        self.addCleanup(server.shutdown)
        request_errors = []
        request_logger = logging.getLogger("django.request")
        old_handlers, old_propagate, old_level = request_logger.handlers[:], request_logger.propagate, request_logger.level

        class SanitizedErrorCapture(logging.Handler):
            def emit(self, record):
                if record.exc_info and record.exc_info[1] is not None:
                    request_errors.append(type(record.exc_info[1]).__name__)

        request_logger.handlers = [SanitizedErrorCapture()]
        request_logger.propagate = False
        request_logger.setLevel(logging.ERROR)

        def restore_request_logger():
            request_logger.handlers = old_handlers
            request_logger.propagate = old_propagate
            request_logger.setLevel(old_level)

        self.addCleanup(restore_request_logger)

        run = SkillRun.objects.create(
            owner=owner, skill_name="playwright", repository_skill=True, reply=reply,
            job_id=identifier, execution_payload={
                "runtime": "playwright-browser", "target_url": "https://fixture.example/login",
                "browser_handoff": True,
            },
        )
        request = {
            "runtime": "playwright-browser", "target_url": "https://fixture.example/login",
            "browser_session": True, "browser_handoff": True,
            "_browser_owner_id": owner.pk, "_browser_consent_token": token,
        }
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp), patch.dict(
            os.environ, {"SUPERHARRY_PORT": str(server.server_port)},
        ):
            directory = Path(temp)
            publish(directory / f"{identifier}.skill-input", json.dumps(request).encode(), replace=False)
            publish(directory / f"{identifier}.browser-consent", json.dumps({
                "job_id": str(identifier), "owner_id": owner.pk, "token": token,
                "expires_at": expires,
            }).encode(), replace=False)
            browser_handoff.register(identifier, owner.pk, token, expires)
            owner_client = Client()
            owner_client.force_login(owner)
            other_client = Client()
            other_client.force_login(other)

            csrf_client = Client(enforce_csrf_checks=True)
            csrf_client.force_login(owner)
            self.assertEqual(csrf_client.post(
                f"/skill-runs/{identifier}/browser-decision/", {"decision": "take_control"},
            ).status_code, 403)
            self.assertEqual(other_client.post(
                f"/skill-runs/{identifier}/browser-decision/", {"decision": "take_control"},
            ).status_code, 404)

            worker_started = threading.Event()
            take_polled = threading.Event()
            frame_published = threading.Event()
            typed_sent = threading.Event()
            typed_polled = threading.Event()
            return_sent = threading.Event()
            return_requested = threading.Event()
            failures = []

            def simulated_skill(_request, **kwargs):
                handler = kwargs["browser_handler"]

                def call(action):
                    try:
                        value = handler(action)
                    except Exception as error:
                        message = str(error).replace(sentinel, "[redacted]")[:100]
                        failures.append(f"bridge-{action.get('operation')}-{type(error).__name__}: {message}")
                        raise
                    return json.loads(value["body"])

                worker_started.set()
                take = call({"operation": "poll_owner_control"})
                if take.get("type") != "take_control":
                    raise AssertionError("Host bridge did not receive the owner takeover event")
                take_polled.set()
                frame = call({"operation": "publish_owner_view", "mime": "image/jpeg",
                              "data": "/9j/2Q=="})
                if frame.get("published") is not True:
                    raise AssertionError("Host bridge did not publish the volatile owner frame")
                frame_published.set()
                if not typed_sent.wait(5):
                    raise AssertionError("Owner did not send the synthetic text event")
                typed = call({"operation": "poll_owner_control"})
                if typed.get("type") != "type":
                    raise AssertionError("Host bridge did not receive the owner text event")
                typed_polled.set()
                returning = call({"operation": "poll_owner_control"})
                if returning.get("type") != "return_control":
                    raise AssertionError("Host bridge did not receive Return control")
                return_requested.set()
                cleanup = call({"operation": "cleanup_owner_control"})
                if cleanup.get("cleared") is not True:
                    raise AssertionError("Host bridge did not clear the RAM handoff")
                published = call({
                    "operation": "publish_result",
                    "message": f"Account access works. Echo: {typed['text']}",
                    "citations": ["https://fixture.example/private"],
                })
                if published.get("published") is not True:
                    raise AssertionError("Host bridge did not publish the sanitized result")
                return {"status": "complete", "output": "Published by the browser result seam"}

            with patch("scripts.run_skill_jobs.run_skill", side_effect=simulated_skill):
                def run_worker():
                    try:
                        process_queue(directory)
                    except Exception as error:
                        message = str(error).replace(sentinel, "[redacted]")[:200]
                        failures.append(f"{type(error).__name__}: {message}")

                worker = threading.Thread(target=run_worker, daemon=True)
                worker.start()
                self.assertTrue(worker_started.wait(5))
                self.assertEqual(owner_client.post(
                    f"/skill-runs/{identifier}/browser-decision/", {"decision": "take_control"},
                ).status_code, 302)
                if not take_polled.wait(5):
                    worker.join(5)
                    self.fail(f"Host bridge did not poll take control; worker errors: {failures}; Django error classes: {request_errors}")
                self.assertTrue(frame_published.wait(5))
                live_frame = owner_client.get(f"/skill-runs/{identifier}/browser-view/")
                self.assertEqual(live_frame.status_code, 200)
                self.assertEqual(live_frame.content, b"\xff\xd8\xff\xd9")
                self.assertFalse((directory / f"{identifier}.browser-view").exists())
                self.assertEqual(csrf_client.post(
                    f"/skill-runs/{identifier}/browser-handoff/input/",
                    data=json.dumps({"type": "type", "text": sentinel}), content_type="application/json",
                ).status_code, 403)
                self.assertEqual(owner_client.post(
                    f"/skill-runs/{identifier}/browser-handoff/input/",
                    data=json.dumps({"type": "type", "text": sentinel}),
                    content_type="application/json",
                ).status_code, 202)
                typed_sent.set()
                self.assertTrue(typed_polled.wait(5))
                self.assertEqual(owner_client.post(
                    f"/skill-runs/{identifier}/browser-decision/", {"decision": "return_control"},
                ).status_code, 302)
                return_sent.set()
                self.assertTrue(return_requested.wait(5))
                worker.join(10)
                self.assertFalse(worker.is_alive())
                self.assertEqual(failures, [])

            result_path = directory / f"{identifier}.skill-result"
            raw_result = read_limited(result_path, 100 * 1024)
            self.assertNotIn(sentinel.encode(), raw_result)
            self.assertIn(b"Account access works", raw_result)
            self.assertIsNone(browser_handoff.owner_state(identifier, owner.pk))
            self.assertFalse((directory / f"{identifier}.skill-artifacts").exists())
            self.assertFalse(SkillArtifact.objects.filter(run=run).exists())
            detail = owner_client.get(f"/skill-runs/{identifier}/")
            self.assertEqual(detail.status_code, 200)
            reply.refresh_from_db()
            self.assertNotIn(sentinel, reply.content)
            self.assertIn("Account access works", reply.content)
            self.assertIn("https://fixture.example/private", reply.content)

    def test_expired_bridge_capability_clears_ram_state(self):
        from superharry.browser_handoff import MAX_ACTIVE_HANDOFFS

        self.assertGreaterEqual(MAX_ACTIVE_HANDOFFS, 1)
        identifier = uuid4()
        owner_id = 914
        token = "bridge-expired-synthetic-0123456789abcd"
        browser_handoff.register(identifier, owner_id, token, time.time() - 1)
        self.assertIsNone(browser_handoff.authenticate(identifier, owner_id, token))
        self.assertIsNone(browser_handoff.owner_state(identifier, owner_id))

    def test_cancel_clears_live_owner_input_and_consent(self):
        from superharry.csv_protocol import publish
        from superharry.models import Conversation, Message, SkillRun

        owner = get_user_model().objects.create_user(username="handoff-cancel-owner", password="test")
        conversation = Conversation.objects.create(owner=owner, title="Handoff cancellation fixture")
        prompt = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Sign in")
        reply = Message.objects.create(conversation=conversation, prompt=prompt,
                                       role=Message.Role.ASSISTANT, content="", complete=True)
        identifier = uuid4()
        token = "bridge-cancel-capability-0123456789abcd"
        secret = "SYNTHETIC_CANCEL_SENTINEL_51"
        expires = time.time() + 180
        SkillRun.objects.create(
            owner=owner, skill_name="playwright", repository_skill=True, reply=reply,
            job_id=identifier, execution_payload={"runtime": "playwright-browser",
                                                  "browser_handoff": True},
        )
        with TemporaryDirectory() as temp, self.settings(SKILL_SPOOL=temp):
            directory = Path(temp)
            publish(directory / f"{identifier}.skill-running", json.dumps({
                "runtime": "playwright-browser", "target_url": "https://fixture.example/login",
                "browser_session": True, "browser_handoff": True,
                "_browser_owner_id": owner.pk, "_browser_consent_token": token,
            }).encode(), replace=False)
            publish(directory / f"{identifier}.browser-consent", json.dumps({
                "job_id": str(identifier), "owner_id": owner.pk, "token": token,
                "expires_at": expires,
            }).encode(), replace=False)
            state = browser_handoff.register(identifier, owner.pk, token, expires)
            client = Client()
            client.force_login(owner)
            self.assertEqual(client.post(
                f"/skill-runs/{identifier}/browser-decision/", {"decision": "take_control"},
            ).status_code, 302)
            self.assertTrue(browser_handoff.enqueue_owner_event(identifier, owner.pk, {
                "type": "type", "text": secret,
            }))
            self.assertEqual(client.post(
                f"/skill-runs/{identifier}/browser-decision/", {"decision": "cancel"},
            ).status_code, 302)
            self.assertIsNone(browser_handoff.owner_state(identifier, owner.pk))
            self.assertFalse(state.owner_control)
            self.assertTrue(state.events.empty())
            self.assertEqual(state.token, "")
            self.assertFalse((directory / f"{identifier}.browser-consent").exists())
            for path in directory.iterdir():
                self.assertNotIn(secret.encode(), path.read_bytes())
