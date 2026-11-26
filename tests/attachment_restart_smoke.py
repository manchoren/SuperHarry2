"""Verify attachment downloads across separate application processes."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix="superharry-attachments-") as directory:
    env = os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE="superharry.settings", DJANGO_SECRET_KEY="synthetic-test", DJANGO_DATABASE_PATH=str(Path(directory) / "db.sqlite3"))
    subprocess.run([sys.executable, "-m", "django", "migrate", "--noinput", "--verbosity", "0"], env=env, check=True)
    first = '''
import django
django.setup()
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
settings.OMNIROUTE_URL = "http://fixture"
settings.OMNIROUTE_API_KEY = "fixture"
settings.CHAT_MODEL = "fixture/model"
user = get_user_model().objects.create_user(username="owner", password="synthetic")
client = Client(HTTP_HOST="localhost")
client.force_login(user)
with patch("superharry.chat.stream_reply", return_value=(part for part in ["saved"])):
    response = client.post("/chat/send/", {"message": "Save", "attachment": [SimpleUploadedFile("a.csv", b"a,b\\n1,2"), SimpleUploadedFile("b.pdf", b"%PDF synthetic")]})
    assert response.status_code == 200
    list(response.streaming_content)
'''
    second = '''
import django
django.setup()
from django.contrib.auth import get_user_model
from django.test import Client
from superharry.models import ChatInput, Conversation
client = Client(HTTP_HOST="localhost")
client.force_login(get_user_model().objects.get(username="owner"))
conversation = Conversation.objects.get()
page = client.get("/chat/%s/" % conversation.pk)
assert b"Download b.pdf" in page.content
items = {item.name: item for item in ChatInput.objects.all()}
assert client.get("/inputs/%s/download/" % items["a.csv"].pk).content == b"a,b\\n1,2"
assert client.get("/inputs/%s/download/" % items["b.pdf"].pk).content == b"%PDF synthetic"
'''
    subprocess.run([sys.executable, "-c", first], env=env, check=True)
    subprocess.run([sys.executable, "-c", second], env=env, check=True)
    print("Attachment restart smoke passed")
