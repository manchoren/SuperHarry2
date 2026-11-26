"""Verify existing results survive the artifact grouping migration."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="superharry-artifact-migration-") as directory:
    env = os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE="superharry.settings", DJANGO_SECRET_KEY="synthetic-test", DJANGO_DATABASE_PATH=str(Path(directory) / "db.sqlite3"))
    def execute(*arguments):
        subprocess.run([sys.executable, *arguments], env=env, check=True, stdout=subprocess.DEVNULL)

    execute("-m", "django", "migrate", "superharry", "0032", "--noinput", "--verbosity", "0")
    create = '''
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
apps = MigrationExecutor(connection).loader.project_state([("superharry", "0032_conversation_last_opened_at_and_more")]).apps
User = apps.get_model("auth", "User")
Conversation = apps.get_model("superharry", "Conversation")
Message = apps.get_model("superharry", "Message")
Run = apps.get_model("superharry", "SkillRun")
Artifact = apps.get_model("superharry", "SkillArtifact")
ChatInput = apps.get_model("superharry", "ChatInput")
owner = User.objects.create(username="owner")
conversation = Conversation.objects.create(owner_id=owner.pk, title="Existing")
reply = Message.objects.create(conversation_id=conversation.pk, role="assistant", content="Done")
run = Run.objects.create(owner_id=owner.pk, reply_id=reply.pk, skill_name="test", repository_skill=True, artifacts_imported=True)
Artifact.objects.create(run_id=run.pk, name="report.pdf", payload=b"first")
Artifact.objects.create(run_id=run.pk, name="report_ver2.pdf", payload=b"second")
ChatInput.objects.create(owner_id=owner.pk, name="old.txt", media_type="text/plain", payload=b"old")
'''
    execute("-c", create)
    execute("-m", "django", "migrate", "--noinput", "--verbosity", "0")
    verify = '''
import django
django.setup()
from superharry.models import SkillArtifact, ArtifactDelivery, ChatInput
items = list(SkillArtifact.objects.order_by("name"))
assert len(items) == 2 and items[0].group_id == items[1].group_id and items[0].group_id is not None
assert ArtifactDelivery.objects.filter(artifact__in=items).count() == 2
assert bytes(items[0].payload) == b"first" and bytes(items[1].payload) == b"second"
assert items[0].updated_at is None and ChatInput.objects.get(name="old.txt").created_at is None
'''
    execute("-c", verify)
    print("Artifact migration smoke passed")
