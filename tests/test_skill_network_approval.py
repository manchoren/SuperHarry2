"""User-visible approval of individual skill network requests."""
import json
import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from superharry.models import SkillRun


class SkillNetworkApprovalTests(TestCase):
    def test_identical_later_request_needs_a_new_approval(self):
        from scripts.skill_network import NetworkMediator, mediate_action

        received = []

        class Destination(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        owner = get_user_model().objects.create_user(username="owner")
        job = SkillRun.objects.create(owner=owner, skill_name="example")
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            (folder / f"{job.job_id}.skill-running").write_text("{}")
            action = {"method": "POST", "url": f"http://127.0.0.1:{server.server_port}/send", "body": "data"}
            mediator = NetworkMediator(set(), allow_private=True)
            responses = []
            thread = threading.Thread(target=lambda: [responses.append(mediate_action(
                action, folder, job.job_id, mediator, timeout=5,
            )) for _ in range(2)])
            thread.start()
            self.addCleanup(thread.join, 7)
            self.client.force_login(owner)
            seen = set()
            for count in range(2):
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    pending = [path for path in folder.glob("*.network-request") if path.name not in seen]
                    if pending:
                        break
                    time.sleep(0.01)
                self.assertEqual(len(pending), 1)
                self.assertEqual(received, [b"data"] * count)
                path = pending[0]
                seen.add(path.name)
                request_id = path.name.split(".")[1]
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(self.client.post(f"/skill-runs/{job.job_id}/network/{request_id}/", {
                    "decision": "approve", "digest": digest,
                }).status_code, 302)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(responses, [b"ok", b"ok"])
            self.assertEqual(received, [b"data", b"data"])

    def test_approval_cannot_be_reused_if_the_request_changed(self):
        owner = get_user_model().objects.create_user(username="owner")
        job = SkillRun.objects.create(owner=owner, skill_name="example")
        request_id = uuid4()
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            (folder / f"{job.job_id}.skill-running").write_text("{}")
            path = folder / f"{job.job_id}.{request_id}.network-request"
            action = {"method": "POST", "url": "https://example.org/submit", "body": "first"}
            path.write_text(json.dumps(action), encoding="utf-8")
            original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
            action["body"] = "different"
            path.write_text(json.dumps(action), encoding="utf-8")
            self.client.force_login(owner)
            response = self.client.post(f"/skill-runs/{job.job_id}/network/{request_id}/", {
                "decision": "approve", "digest": original_digest,
            })
            self.assertEqual(response.status_code, 409)
            self.assertFalse((folder / f"{job.job_id}.{request_id}.network-decision").exists())

    def test_denial_does_not_dispatch_to_the_destination(self):
        from scripts.skill_network import NetworkMediator, mediate_action

        owner = get_user_model().objects.create_user(username="owner")
        job = SkillRun.objects.create(owner=owner, skill_name="example")
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            (folder / f"{job.job_id}.skill-running").write_text("{}")
            failures = []

            def run():
                try:
                    mediate_action({"method": "POST", "url": "https://example.invalid/submit", "body": "private"},
                                   folder, job.job_id, NetworkMediator(public_resources=set()), timeout=5)
                except PermissionError:
                    failures.append("denied")

            thread = threading.Thread(target=run)
            thread.start()
            self.addCleanup(thread.join, 6)
            deadline = time.monotonic() + 3
            while not list(folder.glob("*.network-request")) and time.monotonic() < deadline:
                time.sleep(0.01)
            paths = list(folder.glob("*.network-request"))
            self.assertEqual(len(paths), 1)
            request_id = paths[0].name.split(".")[1]
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest()
            self.client.force_login(owner)
            self.assertEqual(self.client.post(f"/skill-runs/{job.job_id}/network/{request_id}/", {
                "decision": "deny", "digest": digest,
            }).status_code, 302)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, ["denied"])
            self.assertFalse(list(folder.glob("*.network-request")))

    def test_owner_approval_dispatches_the_exact_pending_action(self):
        from scripts.skill_network import NetworkMediator, mediate_action

        received = []

        class Destination(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"accepted")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        owner = get_user_model().objects.create_user(username="owner")
        job = SkillRun.objects.create(owner=owner, skill_name="example")
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            (folder / f"{job.job_id}.skill-running").write_text("{}")
            result = []
            action = {"method": "POST", "url": f"http://127.0.0.1:{server.server_port}/send", "body": "report"}
            thread = threading.Thread(target=lambda: result.append(mediate_action(
                action, folder, job.job_id, NetworkMediator(public_resources=set(), allow_private=True), timeout=5,
            )))
            thread.start()
            self.addCleanup(thread.join, 6)
            deadline = time.monotonic() + 3
            while not list(folder.glob("*.network-request")) and time.monotonic() < deadline:
                time.sleep(0.01)
            paths = list(folder.glob("*.network-request"))
            self.assertEqual(len(paths), 1)
            request_id = paths[0].name.split(".")[1]
            digest = hashlib.sha256(paths[0].read_bytes()).hexdigest()
            self.client.force_login(owner)
            self.assertContains(self.client.get(f"/skill-runs/{job.job_id}/"), "report")
            self.assertEqual(self.client.post(f"/skill-runs/{job.job_id}/network/{request_id}/", {
                "decision": "approve", "digest": digest,
            }).status_code, 302)
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, [b"accepted"])
            self.assertEqual(received, [b"report"])

    def test_only_owner_can_decide_a_specific_pending_request_once(self):
        owner = get_user_model().objects.create_user(username="owner")
        other = get_user_model().objects.create_user(username="other")
        job = SkillRun.objects.create(owner=owner, skill_name="example")
        request_id = uuid4()
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            (folder / f"{job.job_id}.skill-running").write_text("{}")
            request_path = folder / f"{job.job_id}.{request_id}.network-request"
            request_path.write_text(json.dumps({
                "method": "POST", "url": "https://example.org/submit", "body": "private report",
            }), encoding="utf-8")
            digest = hashlib.sha256(request_path.read_bytes()).hexdigest()
            url = f"/skill-runs/{job.job_id}/network/{request_id}/"
            self.client.force_login(other)
            self.assertEqual(self.client.post(url, {"decision": "approve", "digest": digest}).status_code, 404)
            self.client.force_login(owner)
            self.assertEqual(self.client.post(url, {"decision": "approve", "digest": digest}).status_code, 302)
            self.assertEqual(self.client.post(url, {"decision": "deny", "digest": digest}).status_code, 409)
            decision = json.loads((folder / f"{job.job_id}.{request_id}.network-decision").read_text())
            self.assertEqual(decision["decision"], "approve")

    def test_owner_sees_action_destination_and_data_before_approval(self):
        user = get_user_model().objects.create_user(username="owner")
        job = SkillRun.objects.create(owner=user, skill_name="example")
        request_id = uuid4()
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            path = Path(directory) / f"{job.job_id}.{request_id}.network-request"
            path.write_text(json.dumps({
                "method": "POST", "url": "https://example.org/submit", "body": "private report",
                "headers": {"Authorization": "token for this action"},
            }), encoding="utf-8")
            self.client.force_login(user)
            page = self.client.get(f"/skill-runs/{job.job_id}/")
            self.assertContains(page, "POST")
            self.assertContains(page, "https://example.org/submit")
            self.assertContains(page, "private report")
            self.assertContains(page, "token for this action")
