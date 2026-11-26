"""Exercise local and saved file previews with an isolated browser and database."""
import os
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from wsgiref.simple_server import make_server
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(prefix="superharry-preview-") as directory:
    os.environ.update(DJANGO_SETTINGS_MODULE="superharry.settings", DJANGO_SECRET_KEY="synthetic-test", DJANGO_DATABASE_PATH=str(Path(directory) / "db.sqlite3"))
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.conf import settings
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from superharry.models import ChatInput, Conversation, Message

    call_command("migrate", verbosity=0)
    settings.OMNIROUTE_URL = "http://fixture"
    settings.OMNIROUTE_API_KEY = "fixture"
    settings.CHAT_MODEL = "fixture/model"
    gateway = patch("superharry.chat.stream_reply", side_effect=lambda *args, **kwargs: (text for text in ["saved"]))
    gateway.start()
    user = get_user_model().objects.create_user(username="preview-user", password="synthetic-password-32")
    conversation = Conversation.objects.create(owner=user, title="Preview test")
    message = Message.objects.create(conversation=conversation, role="user", content="Files")
    document = BytesIO()
    Image.new("RGB", (200, 100), "white").save(document, "PDF")
    ChatInput.objects.create(owner=user, message=message, name="sample.pdf", media_type="application/octet-stream", payload=document.getvalue())
    ChatInput.objects.create(owner=user, message=message, name="data.csv", media_type="text/plain", payload=b"a,b\n1,2")
    picture = BytesIO()
    Image.new("RGB", (32, 24), "blue").save(picture, "PNG")
    ChatInput.objects.create(owner=user, message=message, name="picture.png", media_type="application/octet-stream", payload=picture.getvalue())
    server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))

    def serve():
        try:
            server.serve_forever()
        finally:
            connections.close_all()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ["UI_BROWSER_PATH"], headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda error: print("Browser error:", error))
            page.goto(f"http://127.0.0.1:{server.server_port}/login/")
            page.get_by_label("Username:").fill("preview-user")
            page.get_by_label("Password:").fill("synthetic-password-32")
            page.get_by_role("button", name="Sign in").click()
            page.goto(f"http://127.0.0.1:{server.server_port}/chat/{conversation.pk}/")
            page.locator(".preview-file").first.click()
            page.get_by_text("Page 1 of 1").wait_for()
            assert page.locator("#preview-pdf").is_visible()
            page.get_by_role("button", name="Close preview").click()
            page.locator(".preview-file").nth(1).click()
            page.locator("#preview-status").wait_for(state="visible", timeout=3000)
            page.get_by_text("Preview is not available", exact=False).wait_for(timeout=3000)
            page.get_by_role("button", name="Close preview").click()
            page.locator(".preview-file").nth(2).click()
            page.wait_for_function("document.querySelector('#preview-image').naturalWidth === 32")
            page.get_by_role("button", name="Close preview").click()
            page.locator('input[type="file"]').first.set_input_files([
                {"name": "local.pdf", "mimeType": "application/pdf", "buffer": document.getvalue()},
                {"name": "local.csv", "mimeType": "text/csv", "buffer": b"a,b\n1,2"},
            ])
            page.wait_for_function("window.chatFilePond && window.chatFilePond.getFiles().length === 2")
            page.locator("#file-preview").wait_for(state="visible")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.get_by_role("button", name="Close preview").is_visible()
            page.get_by_role("button", name="Close preview").click()
            assert page.locator("#file-preview").is_hidden()
            page.get_by_label("Message", exact=True).fill("Save both files")
            page.get_by_role("button", name="Send", exact=True).click()
            page.get_by_text("Download local.pdf").wait_for()
            assert page.get_by_text("Download local.csv").is_visible()
            browser.close()
        print("File preview browser smoke passed")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        connections.close_all()
        gateway.stop()
