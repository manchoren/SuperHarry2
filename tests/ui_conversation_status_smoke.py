"""Exercise live conversation indicators with a real browser and isolated data."""
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from wsgiref.simple_server import make_server

from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(prefix="superharry-status-") as directory:
    os.environ.update(DJANGO_SETTINGS_MODULE="superharry.settings", DJANGO_SECRET_KEY="synthetic-test", DJANGO_DATABASE_PATH=str(Path(directory) / "db.sqlite3"))
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from django.utils import timezone
    from superharry.models import Conversation, Message

    call_command("migrate", verbosity=0)
    user = get_user_model().objects.create_user(username="status-user", password="synthetic-password-32")
    target = Conversation.objects.create(owner=user, title="Target chat")
    reply = Message.objects.create(conversation=target, role="assistant", complete=False, in_progress=True)
    server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))

    def serve():
        try:
            server.serve_forever()
        finally:
            connections.close_all()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    def database(operation):
        def execute():
            try:
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(execute).result()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["UI_BROWSER_PATH"], headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{server.server_port}/login/")
            page.get_by_label("Username:").fill("status-user")
            page.get_by_label("Password:").fill("synthetic-password-32")
            page.get_by_role("button", name="Sign in").click()
            label = page.locator(f'[data-conversation-id="{target.pk}"]')
            page.get_by_text("Thinking…").wait_for()
            database(lambda: Message.objects.filter(pk=reply.pk).update(complete=True, in_progress=False, completed_at=timezone.now()))
            page.get_by_text("New response").wait_for(timeout=10000)
            page.get_by_role("link", name="Target chat ● New response").click()
            page.get_by_text("Seen just now").wait_for()
            completed = timezone.now()
            database(lambda: Message.objects.create(conversation_id=target.pk, role="assistant", complete=True, completed_at=completed))
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                seen = database(lambda: Conversation.objects.values_list("seen_through", flat=True).get(pk=target.pk))
                if seen >= completed:
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("Visible conversation was not marked seen")
            assert "Seen" in label.inner_text()
            browser.close()
        print("Conversation status browser smoke passed")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        connections.close_all()
