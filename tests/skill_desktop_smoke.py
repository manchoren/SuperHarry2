"""Check Docker Desktop's Django-to-host-worker spool with disposable state."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4


def phase(action):
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.test import Client, override_settings

    spool = Path(os.environ["SKILL_SPOOL"])
    with override_settings(ALLOWED_HOSTS=["testserver"]):
        users = get_user_model()
        user, _ = users.objects.get_or_create(username="desktop-smoke")
        client = Client()
        client.force_login(user)
        if action == "prepare":
            response = client.post("/skills/new/", {
                "name": "desktop-demo", "purpose": "Demo", "instructions": "Demo",
                "files": '{"main.py": "import demo_skill_dep; print(input().upper())"}',
                "dependencies": '["demo-skill-dep==1.0"]',
            })
            assert response.status_code == 302, response.status_code
            wheel = SimpleUploadedFile("demo_skill_dep-1.0-py3-none-any.whl", (spool / "fixture.whl").read_bytes())
            response = client.post("/skills/desktop-demo/prepare/", {"entrypoint": "main.py", "wheels": wheel})
            assert response.status_code == 302, response.status_code
        elif action == "run":
            assert b"Prepared" in client.get("/skills/desktop-demo/").content
            response = client.post("/skills/desktop-demo/run/", {"input": "hello"})
            assert response.status_code == 302, response.status_code
            (spool / "result-path.txt").write_text(response["Location"], encoding="utf-8")
        elif action == "verify":
            location = (spool / "result-path.txt").read_text(encoding="utf-8")
            assert b"HELLO" in client.get(location).content
            other, _ = users.objects.get_or_create(username="desktop-other")
            client.force_login(other)
            assert client.get(location).status_code == 404
        elif action == "prepare_network":
            source = """from skill_network_client import request
try:
    request('POST', 'https://example.invalid/submit', 'private sample')
except PermissionError:
    print('handled')
"""
            response = client.post("/skills/new/", {
                "name": "desktop-network", "purpose": "Network demo", "instructions": "Demo",
                "files": json.dumps({"main.py": source}), "dependencies": "[]",
            })
            assert response.status_code == 302, response.status_code
            assert client.post("/skills/desktop-network/prepare/", {"entrypoint": "main.py"}).status_code == 302
        elif action == "run_network":
            response = client.post("/skills/desktop-network/run/", {"input": ""})
            assert response.status_code == 302, response.status_code
            (spool / "network-result-path.txt").write_text(response["Location"], encoding="utf-8")
        elif action == "approve_network":
            location = (spool / "network-result-path.txt").read_text(encoding="utf-8")
            pending = list(spool.glob("*.network-request"))
            assert len(pending) == 1, pending
            assert b"private sample" in client.get(location).content
            request_id = pending[0].name.split(".")[1]
            digest = hashlib.sha256(pending[0].read_bytes()).hexdigest()
            response = client.post(f"{location}network/{request_id}/", {"decision": "approve", "digest": digest})
            assert response.status_code == 302, response.status_code
        elif action == "verify_network":
            location = (spool / "network-result-path.txt").read_text(encoding="utf-8")
            assert b"handled" in client.get(location).content
        elif action == "prepare_lifecycle":
            response = client.post("/skills/new/", {
                "name": "desktop-lifecycle", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "import time; time.sleep(60); print(input())"}),
                "dependencies": "[]",
            })
            assert response.status_code == 302
            assert client.post("/skills/desktop-lifecycle/prepare/", {"entrypoint": "main.py"}).status_code == 302
        elif action == "run_lifecycle":
            response = client.post("/skills/desktop-lifecycle/run/", {"input": "synthetic input"})
            assert response.status_code == 302
            (spool / "lifecycle-result-path.txt").write_text(response["Location"], encoding="utf-8")
        elif action == "update_lifecycle":
            response = client.post("/skills/desktop-lifecycle/edit/", {
                "name": "desktop-lifecycle", "purpose": "Demo", "instructions": "Demo",
                "files": json.dumps({"main.py": "print('updated:', input())"}), "dependencies": "[]",
            })
            assert response.status_code == 302, response.status_code
        elif action == "verify_lifecycle":
            location = (spool / "lifecycle-result-path.txt").read_text(encoding="utf-8")
            assert b"updated: synthetic input" in client.get(location).content


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "run", "verify", "prepare_network", "run_network", "approve_network", "verify_network", "prepare_lifecycle", "run_lifecycle", "update_lifecycle", "verify_lifecycle"))
    args = parser.parse_args()
    if args.phase:
        phase(args.phase)
        return

    from scripts.run_skill_jobs import supervise
    from tests.skill_sandbox_smoke import fixture_wheel

    image = f"superharry-skill-desktop-test:{uuid4().hex}"
    container = f"superharry-skill-desktop-test-{uuid4().hex}"
    with TemporaryDirectory() as directory:
        spool = Path(directory)
        (spool / "fixture.whl").write_bytes(base64.b64decode(fixture_wheel()))
        try:
            subprocess.run(["docker", "build", "--target", "django", "-t", image, "."], check=True)
            subprocess.run([
                "docker", "run", "-d", "--rm", "--name", container, "--network=none",
                "--mount", f"type=bind,source={spool},target=/shared",
                "-e", "DJANGO_SECRET_KEY=synthetic-test-only", "-e", "DJANGO_DATABASE_PATH=/shared/test.sqlite3",
                "-e", "SKILL_SPOOL=/shared", "--entrypoint", "sleep", image, "180",
            ], check=True, stdout=subprocess.DEVNULL)

            def docker_exec(*command):
                subprocess.run(["docker", "exec", container, *command], check=True)

            def process_once():
                if os.environ.get("SKILL_SMOKE_UNSUPPORTED_RUNTIME") == "1":
                    with patch("scripts.run_skill_jobs.validate_runtime"):
                        supervise(spool, once=True, image=os.environ.get("SKILL_TEST_IMAGE"))
                else:
                    supervise(spool, once=True, image=os.environ.get("SKILL_TEST_IMAGE"))

            docker_exec("python", "-m", "django", "migrate", "--noinput", "--verbosity", "0")
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "prepare")
            process_once()
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "run")
            process_once()
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "verify")
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "prepare_network")
            process_once()
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "run_network")
            errors = []
            def process_network():
                try:
                    process_once()
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=process_network)
            worker.start()
            deadline = time.monotonic() + 20
            while not list(spool.glob("*.network-request")) and time.monotonic() < deadline and worker.is_alive():
                time.sleep(0.1)
            assert list(spool.glob("*.network-request")), errors
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "approve_network")
            worker.join(30)
            assert not worker.is_alive() and not errors, errors
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "verify_network")
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "prepare_lifecycle")
            process_once()
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "run_lifecycle")
            worker = threading.Thread(target=process_network)
            worker.start()
            deadline = time.monotonic() + 20
            while not list(spool.glob("*.skill-running")) and time.monotonic() < deadline and worker.is_alive():
                time.sleep(0.1)
            assert list(spool.glob("*.skill-running")), errors
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "update_lifecycle")
            worker.join(30)
            assert not worker.is_alive() and not errors, errors
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "verify_lifecycle")
            assert not list(spool.glob("*.skill-input")) and not list(spool.glob("*.skill-running"))
            print("Docker Desktop skill spool smoke passed.", flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
