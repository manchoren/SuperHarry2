"""Check Docker Desktop's Django-to-host-worker spool with disposable state."""
import argparse
import base64
import os
from pathlib import Path
import subprocess
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("prepare", "run", "verify"))
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
                        supervise(spool, once=True)
                else:
                    supervise(spool, once=True)

            docker_exec("python", "-m", "django", "migrate", "--noinput", "--verbosity", "0")
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "prepare")
            process_once()
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "run")
            process_once()
            docker_exec("python", "-m", "tests.skill_desktop_smoke", "--phase", "verify")
            assert not list(spool.glob("*.skill-input")) and not list(spool.glob("*.skill-running"))
            print("Docker Desktop skill spool smoke passed.", flush=True)
        finally:
            subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "image", "rm", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
