"""Exercise Docker worker outputs across isolated Django container restarts."""
import os
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from scripts.run_skill_jobs import process_queue, validate_runtime


def run(command):
    return subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip()


django_image = os.environ.get("DJANGO_TEST_IMAGE", "superharry-output-django:local")
worker_image = os.environ.get("SKILL_TEST_IMAGE", "superharry-output-test:local")
volume = "superharry-output-smoke-" + uuid4().hex
with tempfile.TemporaryDirectory(prefix="superharry-output-spool-") as directory:
    spool = Path(directory).resolve()
    run(["docker", "volume", "create", volume])
    try:
        base = ["docker", "run", "--rm", "--network=none", "--mount", f"type=volume,source={volume},target=/data",
                "--mount", f"type=bind,source={spool},target=/skill-jobs", "-e", "DJANGO_SECRET_KEY=synthetic-test",
                "-e", "DJANGO_DATABASE_PATH=/data/db.sqlite3", "-e", "SKILL_SPOOL=/skill-jobs", django_image]
        run(base + ["python", "-m", "django", "migrate", "--noinput", "--verbosity", "0"])
        create = '''
from uuid import uuid4
from django.contrib.auth import get_user_model
from superharry.models import Conversation, Message, SkillOutbox, SkillRun
from superharry.skill_jobs import dispatch_one
user = get_user_model().objects.create_user(username="owner", password="synthetic")
conversation = Conversation.objects.create(owner=user, title="Smoke")
reply = Message.objects.create(conversation=conversation, role="assistant", content="Queued")
job = SkillRun.objects.create(owner=user, reply=reply, skill_name="probe", repository_skill=True, job_id=uuid4())
payload = {"operation": "run", "files": {"main.py": "from pathlib import Path; Path('/work/output/report.txt').write_text('persisted output')"}, "entrypoint": "main.py", "input": "", "dependencies": [], "wheels": {}}
SkillOutbox.objects.create(job_id=job.job_id, execution=job, requested_by=user, payload=payload)
assert dispatch_one(job.job_id)
print(job.job_id)
'''
        identifier = run(base + ["python", "-m", "django", "shell", "-c", create]).splitlines()[-1]
        process_queue(spool, runtime_check=validate_runtime, image=worker_image)
        first = f'''
from django.contrib.auth import get_user_model
from django.test import Client
client = Client(HTTP_HOST="localhost")
client.force_login(get_user_model().objects.get(username="owner"))
response = client.get("/skill-runs/{identifier}/artifacts/")
assert response.status_code == 200
artifact = response.json()["artifacts"][0]
assert client.get(artifact["url"]).content == b"persisted output"
'''
        run(base + ["python", "-m", "django", "shell", "-c", first])
        assert not list(spool.glob("*.skill-artifacts"))
        second = f'''
from django.contrib.auth import get_user_model
from django.test import Client
from superharry.models import SkillArtifact
client = Client(HTTP_HOST="localhost")
client.force_login(get_user_model().objects.get(username="owner"))
artifact = SkillArtifact.objects.get(run__job_id="{identifier}")
assert client.get("/skill-artifacts/%s/download/" % artifact.pk).content == b"persisted output"
'''
        run(base + ["python", "-m", "django", "shell", "-c", second])
        print("Docker artifact restart smoke passed")
    finally:
        run(["docker", "volume", "rm", volume])
