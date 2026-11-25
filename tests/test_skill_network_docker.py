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

    def test_updating_queued_skill_runs_new_version_with_original_input(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print('old:', input())"}), "dependencies": "[]",
            })
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            self.assertEqual(self.client.post("/skills/report/edit/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print('new:', input())"}), "dependencies": "[]",
            }).status_code, 302)
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            self.assertContains(self.client.get(run["Location"]), "new: original")

    def test_update_preserves_input_for_a_run_created_before_lifecycle_upgrade(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime
        from superharry.models import SkillRun

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print(input())"}), "dependencies": "[]",
            })
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            SkillRun.objects.update(execution_payload={})
            self.client.post("/skills/report/edit/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print('updated:', input())"}), "dependencies": "[]",
            })
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            self.assertContains(self.client.get(run["Location"]), "updated: original")

    def test_old_running_job_without_action_history_is_not_automatically_repeated(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime
        from superharry.models import SkillRun

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {"name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "import time; time.sleep(60)"}), "dependencies": "[]"})
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            SkillRun.objects.update(execution_payload={})
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
            })
            worker.start()
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.skill-running")) and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                self.assertTrue(list(folder.glob("*.skill-running")))
                self.assertEqual(self.client.post("/skills/report/edit/", {
                    "name": "report", "purpose": "Demo", "instructions": "Demo",
                    "files": json.dumps({"main.py": "print('must not restart')"}), "dependencies": "[]",
                }).status_code, 302)
                worker.join(20)
                self.assertFalse(worker.is_alive())
                self.assertContains(self.client.get(run["Location"]), "prior external activity is unknown")
                self.assertNotContains(self.client.get(run["Location"]), "must not restart")
            finally:
                worker.join(70)

    def test_updating_dependencies_prepares_them_before_restarting(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime

        def package_request(action):
            if urlsplit(action["url"]).hostname == "pypi.org":
                return b'<a href="https://files.pythonhosted.org/packages/aa/bb/cc/demo_skill_dep-1.0-py3-none-any.whl">wheel</a>'
            return base64.b64decode(fixture_wheel())

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print(input())"}), "dependencies": "[]",
            })
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            self.client.post("/skills/report/edit/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "from demo_skill_dep import value; print(value(), input())"}),
                "dependencies": '["demo-skill-dep==1.0"]',
            })
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local", package_handler=package_request)
            self.assertContains(self.client.get(run["Location"]), "dependency ready original")

    def test_recovery_does_not_interrupt_a_restarted_run(self):
        from django.core.management import call_command
        from scripts.run_skill_jobs import process_queue, validate_runtime

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print(input())"}), "dependencies": "[]",
            })
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            source = "from skill_network_client import request\ntry: request('POST', 'https://example.invalid/', 'data')\nexcept PermissionError: print('denied')"
            self.client.post("/skills/report/edit/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": source}), "dependencies": "[]",
            })
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
            })
            worker.start()
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.network-request")) and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                pending = next(folder.glob("*.network-request"))
                call_command("recover_skill_dispatch", verbosity=0)
                time.sleep(0.5)
                self.assertTrue(pending.exists())
                self.assertTrue(worker.is_alive())
                request_id = pending.name.split(".")[1]
                digest = hashlib.sha256(pending.read_bytes()).hexdigest()
                self.assertEqual(self.client.post(f'{run["Location"]}network/{request_id}/', {"decision": "deny", "digest": digest}).status_code, 302)
                worker.join(20)
                self.assertContains(self.client.get(run["Location"]), "denied")
            finally:
                if worker.is_alive():
                    for request in folder.glob("*.network-request"):
                        request_id = request.name.split(".")[1]
                        digest = hashlib.sha256(request.read_bytes()).hexdigest()
                        self.client.post(f'{run["Location"]}network/{request_id}/', {"decision": "deny", "digest": digest})
                worker.join(20)

    def test_updating_running_skill_interrupts_and_restarts_with_original_input(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "import time; time.sleep(60); print(input())"}), "dependencies": "[]",
            })
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
            })
            worker.start()
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.skill-running")) and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                self.assertTrue(list(folder.glob("*.skill-running")))
                self.assertEqual(self.client.post("/skills/report/edit/", {
                    "name": "report", "purpose": "Demo", "instructions": "Demo",
                    "files": json.dumps({"main.py": "print('updated:', input())"}), "dependencies": "[]",
                }).status_code, 302)
                worker.join(20)
                self.assertFalse(worker.is_alive())
                self.assertContains(self.client.get(run["Location"]), "updated: original")
            finally:
                worker.join(70)

    def test_update_after_external_request_stops_without_repeating_it(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "report", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "from skill_network_client import request; request('POST', 'https://example.invalid/', 'data')"}),
                "dependencies": "[]",
            })
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
            })
            worker.start()
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.network-request")) and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                self.assertTrue(list(folder.glob("*.network-request")))
                self.assertEqual(self.client.post("/skills/report/edit/", {
                    "name": "report", "purpose": "Demo", "instructions": "Demo",
                    "files": json.dumps({"main.py": "print('should not restart')"}), "dependencies": "[]",
                }).status_code, 302)
                worker.join(20)
                self.assertFalse(worker.is_alive())
                self.assertContains(self.client.get(run["Location"]), "External action was not repeated")
                self.assertNotContains(self.client.get(run["Location"]), "should not restart")
                self.assertFalse(list(folder.glob("*.network-request")))
            finally:
                worker.join(70)

    def test_update_after_performed_external_action_does_not_repeat_it(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime
        from scripts.skill_network import NetworkMediator

        received = []
        class Destination(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"done")
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            source = "from skill_network_client import request\nimport time\nrequest('POST', input(), 'data')\ntime.sleep(60)"
            self.client.post("/skills/new/", {"name": "report", "purpose": "Demo", "instructions": "Demo",
                                              "files": json.dumps({"main.py": source}), "dependencies": "[]"})
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": f"http://127.0.0.1:{server.server_port}/"})
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
                "mediator": NetworkMediator(set(), allow_private=True),
            })
            worker.start()
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.network-request")) and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                pending = next(folder.glob("*.network-request"))
                request_id = pending.name.split(".")[1]
                digest = hashlib.sha256(pending.read_bytes()).hexdigest()
                self.assertEqual(self.client.post(f'{run["Location"]}network/{request_id}/', {"decision": "approve", "digest": digest}).status_code, 302)
                deadline = time.monotonic() + 15
                while not received and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(received, [b"data"])
                self.assertEqual(self.client.post("/skills/report/edit/", {
                    "name": "report", "purpose": "Demo", "instructions": "Demo",
                    "files": json.dumps({"main.py": source}), "dependencies": "[]",
                }).status_code, 302)
                worker.join(20)
                self.assertFalse(worker.is_alive())
                self.assertContains(self.client.get(run["Location"]), "External action was not repeated")
                self.assertEqual(received, [b"data"])
            finally:
                worker.join(70)

    def test_concurrent_update_and_delete_stop_an_executing_run(self):
        from django.db import close_old_connections
        from django.test import Client
        from scripts.run_skill_jobs import process_queue, validate_runtime

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            source = "import time; time.sleep(60); print(input())"
            fields = {"name": "report", "purpose": "Demo", "instructions": "Demo",
                      "files": json.dumps({"main.py": source}), "dependencies": "[]"}
            self.client.post("/skills/new/", fields)
            self.client.post("/skills/report/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/report/run/", {"input": "original"})
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
            })
            worker.start()
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.skill-running")) and time.monotonic() < deadline:
                time.sleep(0.05)
            barrier, responses, errors = threading.Barrier(2), {}, []
            cookies = self.client.cookies.copy()

            def transition(kind):
                client = Client()
                client.cookies = cookies.copy()
                try:
                    barrier.wait(10)
                    response = client.post(f"/skills/report/{kind}/", fields if kind == "edit" else {})
                    responses[kind] = response.status_code
                except Exception as error:
                    errors.append(error)
                finally:
                    close_old_connections()

            edit = threading.Thread(target=transition, args=("edit",))
            delete = threading.Thread(target=transition, args=("delete",))
            try:
                self.assertTrue(list(folder.glob("*.skill-running")))
                edit.start()
                delete.start()
                edit.join(20)
                delete.join(20)
                self.assertFalse(errors, errors)
                self.assertIn(responses.get("edit"), (302, 404, 503))
                self.assertIn(responses.get("delete"), (302, 503))
                if responses["delete"] == 503:
                    self.assertEqual(self.client.post("/skills/report/delete/").status_code, 302)
                worker.join(25)
                self.assertFalse(worker.is_alive())
                self.assertContains(self.client.get(run["Location"]), "Skill deleted")
                self.assertFalse(list(folder.glob("*.skill-running")))
            finally:
                worker.join(70)

    def test_deleting_skill_interrupts_running_container_and_notifies_owner(self):
        from scripts.run_skill_jobs import process_queue, validate_runtime

        self.client.force_login(get_user_model().objects.create_user(username="owner"))
        with TemporaryDirectory() as directory, override_settings(SKILL_SPOOL=directory):
            folder = Path(directory)
            self.client.post("/skills/new/", {
                "name": "long-job", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "import time; time.sleep(60); print('late')"}),
                "dependencies": "[]",
            })
            self.client.post("/skills/long-job/prepare/", {"entrypoint": "main.py"})
            process_queue(folder, runtime_check=validate_runtime, image="superharry-skill-network-test:local")
            run = self.client.post("/skills/long-job/run/", {"input": "hello"})
            worker = threading.Thread(target=process_queue, args=(folder,), kwargs={
                "image": "superharry-skill-network-test:local", "runtime_check": validate_runtime,
            })
            worker.start()
            self.addCleanup(worker.join, 70)
            deadline = time.monotonic() + 15
            while not list(folder.glob("*.skill-running")) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(list(folder.glob("*.skill-running")))
            self.assertEqual(self.client.post("/skills/long-job/delete/").status_code, 302)
            worker.join(15)
            self.assertFalse(worker.is_alive())
            self.assertContains(self.client.get(run["Location"]), "Skill deleted")

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
