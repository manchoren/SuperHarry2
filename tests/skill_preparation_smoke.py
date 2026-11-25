"""Exercise Django and the real isolated preparation worker using a temporary database."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        os.environ["DJANGO_DATABASE_PATH"] = str(root / "test.sqlite3")
        os.environ["DJANGO_SETTINGS_MODULE"] = "superharry.settings"
        os.environ["DJANGO_SECRET_KEY"] = "skill-preparation-smoke-only"
        import django
        django.setup()
        from django.contrib.auth import get_user_model
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.core.management import call_command
        from django.db import connections
        from django.test import Client, override_settings

        from scripts.run_skill_jobs import supervise
        from tests.skill_sandbox_smoke import fixture_wheel
        import base64
        from unittest.mock import patch

        def process_once():
            if os.environ.get("SKILL_SMOKE_UNSUPPORTED_RUNTIME") == "1":
                with patch("scripts.run_skill_jobs.validate_runtime"):
                    supervise(root, once=True, image=os.environ.get("SKILL_TEST_IMAGE"))
            else:
                supervise(root, once=True, image=os.environ.get("SKILL_TEST_IMAGE"))

        call_command("migrate", verbosity=0, interactive=False)
        real_wheel = os.environ.get("SKILL_TEST_WHEEL")
        wheel_path = Path(real_wheel) if real_wheel else None
        wheel_name = wheel_path.name if wheel_path else "demo_skill_dep-1.0-py3-none-any.whl"
        wheel_data = wheel_path.read_bytes() if wheel_path else base64.b64decode(fixture_wheel())
        dependency = "packaging==26.0" if wheel_path else "demo-skill-dep==1.0"
        module = "packaging" if wheel_path else "demo_skill_dep"
        with override_settings(SKILL_SPOOL=str(root), ALLOWED_HOSTS=["testserver"]):
            user = get_user_model().objects.create_user(username="skill-smoke")
            client = Client()
            client.force_login(user)
            response = client.post("/skills/new/", {
                "name": "demo", "purpose": "Demo", "instructions": "Demo",
                "files": '{"main.py": "import ' + module + '; print(input().upper())"}',
                "dependencies": '["' + dependency + '"]',
            })
            assert response.status_code == 302, response.status_code
            response = client.post("/skills/demo/prepare/", {"entrypoint": "main.py"})
            assert response.status_code == 302, response.status_code
            process_once()
            assert b"Dependency preparation failed" in client.get("/skills/demo/").content
            wheel = SimpleUploadedFile(wheel_name, wheel_data)
            response = client.post("/skills/demo/prepare/", {"entrypoint": "main.py", "wheels": wheel})
            assert response.status_code == 302, response.status_code
            process_once()
            assert b"Prepared" in client.get("/skills/demo/").content
            response = client.post("/skills/demo/run/", {"input": "hello"})
            assert response.status_code == 302, response.status_code
            location = response["Location"]
            process_once()
            assert b"HELLO" in client.get(location).content
            other = get_user_model().objects.create_user(username="other-smoke")
            client.force_login(other)
            assert client.get(location).status_code == 404
            assert not list(root.glob("*.skill-input")) and not list(root.glob("*.skill-running"))
        connections.close_all()
    print("Skill preparation integration smoke passed.")


if __name__ == "__main__":
    main()
