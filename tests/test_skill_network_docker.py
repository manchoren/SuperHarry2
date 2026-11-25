"""Opt-in Docker integration of skill preparation and network mediation."""
import base64
import json
import os
import threading
import time
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from urllib.parse import urlsplit
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from tests.skill_sandbox_smoke import fixture_wheel


@skipUnless(os.environ.get("SKILL_NETWORK_DOCKER_SMOKE") == "1", "Requires isolated Docker test image")
class SkillNetworkDockerTests(TransactionTestCase):
    serialized_rollback = True

    def test_worker_recovery_removes_interrupted_network_approvals(self):
        from scripts.run_skill_jobs import recover_interrupted

        with TemporaryDirectory() as directory:
            folder = Path(directory)
            job_id, request_id = uuid4(), uuid4()
            (folder / f"{job_id}.skill-running").write_text("{}")
            (folder / f"{job_id}.{request_id}.network-request").write_text("private data")
            (folder / f"{job_id}.{request_id}.network-decision").write_text("approval")
            recover_interrupted(folder)
            self.assertFalse(list(folder.glob(f"{job_id}.*.network-*")))
            self.assertIn('"interrupted"', (folder / f"{job_id}.skill-result").read_text())

    def test_skill_waits_for_its_owner_before_sending_data(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime
        from scripts.skill_network import NetworkMediator

        received = []

        class Destination(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append((self.headers.get("Content-Type"), self.rfile.read(int(self.headers["Content-Length"]))))
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
        self.client.force_login(owner)
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.assertEqual(self.client.post("/skills/new/", {
                "name": "network-demo", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "from skill_network_client import request; print(request('POST', input(), 'private report', {'Content-Type': 'text/plain'}))"}),
                "dependencies": "[]",
            }).status_code, 302)
            self.client.post("/skills/network-demo/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            response = self.client.post("/skills/network-demo/run/", {"input": f"http://127.0.0.1:{server.server_port}/submit"})
            self.assertEqual(response.status_code, 302)
            job_url = response["Location"]
            thread = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "mediator": NetworkMediator(set(), allow_private=True),
                "runtime_check": validate_runtime,
            })
            thread.start()
            self.addCleanup(thread.join, 15)
            deadline = time.monotonic() + 10
            while not list(folder.glob("*.network-request")) and time.monotonic() < deadline:
                time.sleep(0.05)
            requests = list(folder.glob("*.network-request"))
            self.assertEqual(len(requests), 1)
            self.assertEqual(received, [])
            page = self.client.get(job_url)
            self.assertContains(page, "private report")
            self.assertContains(page, "text/plain")
            request_id = requests[0].name.split(".")[1]
            digest = hashlib.sha256(requests[0].read_bytes()).hexdigest()
            self.assertEqual(self.client.post(f"{job_url}network/{request_id}/", {
                "decision": "approve", "digest": digest,
            }).status_code, 302)
            thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(received, [("text/plain", b"private report")])
            self.assertContains(self.client.get(job_url), "accepted")

    def test_downloaded_dependency_remains_available_to_the_prepared_skill(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime
        from scripts.skill_network import NetworkMediator

        wheel_path = "/packages/aa/bb/cca/demo_skill_dep-1.0-py3-none-any.whl"

        class Registry(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                if self.path.startswith("/simple/"):
                    self.wfile.write(f'<a href="https://files.pythonhosted.org{wheel_path}">wheel</a>'.encode())
                else:
                    self.wfile.write(base64.b64decode(fixture_wheel()))

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def package_request(action):
            parsed = urlsplit(action["url"])
            self.assertIn(parsed.hostname, {"pypi.org", "files.pythonhosted.org"})
            local = f"http://127.0.0.1:{server.server_port}{parsed.path}"
            return NetworkMediator(public_resources={local}, allow_private=True).dispatch({
                "method": "GET", "url": local, "body": "",
            })

        owner = get_user_model().objects.create_user(username="owner")
        self.client.force_login(owner)
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            self.assertEqual(self.client.post("/skills/new/", {
                "name": "dependency-demo", "purpose": "Demo", "instructions": "Demo",
                "files": '{"main.py":"from demo_skill_dep import value; print(value())"}',
                "dependencies": '["demo-skill-dep==1.0"]',
            }).status_code, 302)
            self.assertEqual(self.client.post("/skills/dependency-demo/prepare/", {"entrypoint": "main.py"}).status_code, 302)
            process_queue(Path(directory), runtime_check=validate_runtime, image="superharry-skill-network-test:local", package_handler=package_request)
            self.assertContains(self.client.get("/skills/dependency-demo/"), "Prepared")
            response = self.client.post("/skills/dependency-demo/run/", {"input": ""})
            self.assertEqual(response.status_code, 302)
            process_queue(Path(directory), runtime_check=validate_runtime, image="superharry-skill-network-test:local", package_handler=package_request)
            self.assertContains(self.client.get(response["Location"]), "dependency ready")
            cached = list(Path(directory).glob("*.skill-wheels"))
            self.assertEqual(len(cached), 1)
            cached[0].unlink()
            self.assertContains(self.client.get("/skills/dependency-demo/"), "Prepared dependencies unavailable")
            self.assertEqual(self.client.post("/skills/dependency-demo/run/", {"input": ""}).status_code, 409)
