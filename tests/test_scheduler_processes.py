import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread

from django.test import SimpleTestCase


class SchedulerProcessTests(SimpleTestCase):
    def test_killed_scheduler_does_not_resubmit_a_started_gateway_request(self):
        started = Event()
        release = Event()
        calls = []

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test/model"}]}')

            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers["Content-Length"])))
                started.set()
                release.wait(15)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="scheduler-crash-test-") as directory:
                db = Path(directory) / "db.sqlite3"
                key = Path(directory) / "key"
                key.write_text("test-key", encoding="utf-8")
                env = {**os.environ, "DJANGO_SETTINGS_MODULE": "superharry.settings", "DJANGO_SECRET_KEY": "test",
                    "DJANGO_DATABASE_PATH": str(db), "OMNIROUTE_API_KEY_FILE": str(key),
                    "OMNIROUTE_URL": f"http://127.0.0.1:{server.server_port}", "CHAT_MODEL": "test/model"}
                subprocess.run([sys.executable, "-m", "django", "migrate", "--noinput", "--verbosity", "0"], env=env, check=True, timeout=30)
                seed = """import django; django.setup()
from django.contrib.auth import get_user_model
from django.utils import timezone
from superharry.models import Conversation, Schedule
u=get_user_model().objects.create_user(username='owner')
c=Conversation.objects.create(owner=u, title='Crash')
Schedule.objects.create(owner=u, conversation=c, prompt='Check', next_run=timezone.now(), timezone='UTC', repetition='once', preferred_model='test/model')
"""
                subprocess.run([sys.executable, "-c", seed], env=env, check=True, timeout=10)
                first = subprocess.Popen([sys.executable, "-m", "django", "run_schedules"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    self.assertTrue(started.wait(30), "The first scheduler did not reach the gateway")
                finally:
                    first.kill()
                    first.wait(timeout=10)
                    first.stdout.close()
                    first.stderr.close()
                    release.set()
                resumed = subprocess.run([sys.executable, "-m", "django", "run_schedules"], env=env, capture_output=True, timeout=30)
                self.assertEqual(resumed.returncode, 0, resumed.stderr.decode())
                self.assertEqual(len(calls), 1)
                with closing(sqlite3.connect(db)) as connection:
                    self.assertEqual(connection.execute("SELECT status FROM superharry_scheduleoccurrence").fetchone()[0], "interrupted")
                    self.assertEqual(connection.execute("SELECT active FROM superharry_chatsubmission").fetchone()[0], 0)
        finally:
            release.set()
            server.shutdown()
            thread.join()
            server.server_close()

    def test_overlapping_processes_submit_an_occurrence_once(self):
        started = Event()
        release = Event()
        calls = []

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"data":[{"id":"test/model"}]}')

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                started.set()
                if not release.wait(15):
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"done"}}]}\n\ndata: [DONE]\n\n')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="scheduler-process-test-") as directory:
                env = {**os.environ, "DJANGO_SETTINGS_MODULE": "superharry.settings", "DJANGO_SECRET_KEY": "test",
                    "DJANGO_DATABASE_PATH": str(Path(directory) / "db.sqlite3"),
                    "OMNIROUTE_URL": f"http://127.0.0.1:{server.server_port}", "CHAT_MODEL": "test/model"}
                key = Path(directory) / "key"
                key.write_text("test-key", encoding="utf-8")
                env["OMNIROUTE_API_KEY_FILE"] = str(key)
                subprocess.run([sys.executable, "-m", "django", "migrate", "--noinput", "--verbosity", "0"], env=env, check=True, timeout=30)
                seed = """import django; django.setup()
from django.contrib.auth import get_user_model
from django.utils import timezone
from superharry.models import Conversation, Schedule
u=get_user_model().objects.create_user(username='owner')
c=Conversation.objects.create(owner=u, title='Concurrent')
Schedule.objects.create(owner=u, conversation=c, prompt='Check', next_run=timezone.now(), timezone='UTC', repetition='once', preferred_model='test/model')
"""
                subprocess.run([sys.executable, "-c", seed], env=env, check=True, timeout=10)
                first = subprocess.Popen([sys.executable, "-m", "django", "run_schedules"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    self.assertTrue(started.wait(10), "The first scheduler did not reach the gateway")
                    second = subprocess.run([sys.executable, "-m", "django", "run_schedules"], env=env, capture_output=True, timeout=10)
                    self.assertEqual(second.returncode, 0, second.stderr.decode())
                finally:
                    release.set()
                    out, err = first.communicate(timeout=15)
                self.assertEqual(first.returncode, 0, err.decode())
                self.assertEqual(len(calls), 1)
        finally:
            release.set()
            server.shutdown()
            thread.join()
            server.server_close()
