"""Exercise local and saved file previews with an isolated browser and database."""
import os
import base64
import json
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
    from django.contrib.auth.models import Group
    from django.conf import settings
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from superharry.models import ChatInput, Conversation, Message, SkillRun

    call_command("migrate", verbosity=0)
    settings.OMNIROUTE_URL = "http://fixture"
    settings.OMNIROUTE_API_KEY = "fixture"
    settings.CHAT_MODEL = "fixture/model"
    settings.SKILL_SPOOL = directory
    gateway = patch("superharry.chat.stream_reply", side_effect=lambda *args, **kwargs: (text for text in ["saved"]))
    gateway.start()
    patch("superharry.chat.available_route_catalog", return_value={"fixture/model": {"id": "fixture/model"}}).start()
    user = get_user_model().objects.create_user(username="preview-user", password="synthetic-password-32")
    admin = get_user_model().objects.create_user(username="history-admin", password="synthetic-admin-password-32")
    admin.groups.add(Group.objects.get_or_create(name="Administrators")[0])
    conversation = Conversation.objects.create(owner=user, title="Preview test")
    message = Message.objects.create(conversation=conversation, role="user", content="Files")
    document = BytesIO()
    Image.new("RGB", (200, 100), "white").save(document, "PDF")
    ChatInput.objects.create(owner=user, message=message, name="sample.pdf", media_type="application/octet-stream", payload=document.getvalue())
    ChatInput.objects.create(owner=user, message=message, name="data.csv", media_type="text/plain", payload=b"a,b\n1,2")
    picture = BytesIO()
    Image.new("RGB", (32, 24), "blue").save(picture, "PNG")
    ChatInput.objects.create(owner=user, message=message, name="picture.png", media_type="application/octet-stream", payload=picture.getvalue())
    reply = Message.objects.create(conversation=conversation, role="assistant", content="Generated file")
    job = SkillRun.objects.create(owner=user, reply=reply, skill_name="demo", repository_skill=True)
    (Path(directory) / f"{job.job_id}.skill-result").write_text(json.dumps({"status": "complete", "output": "done"}))
    (Path(directory) / f"{job.job_id}.skill-artifacts").write_text(json.dumps({"generation": 0, "files": [{"name": name, "data": base64.b64encode(document.getvalue()).decode()} for name in ("generated.pdf", "generated_ver2.pdf")]}))
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
            folders_entry = page.get_by_role("link", name="Folders")
            assert folders_entry.get_attribute("href") == "/inputs/"
            assert page.get_by_role("link", name="File history").count() == 0
            page.goto(f"http://127.0.0.1:{server.server_port}/inputs/")
            assert page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Inputs").get_attribute("aria-current") == "page"
            page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Files").click()
            assert page.url.endswith("/files/")
            assert page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Files").get_attribute("aria-current") == "page"
            page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Inputs").click()
            assert page.get_by_role("table").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.get_by_role("link", name="Input #1: sample.pdf").click()
            assert page.get_by_role("heading", name="Input #1").is_visible()
            assert page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Inputs").get_attribute("aria-current") == "page"
            assert page.get_by_role("link", name="Download sample.pdf").is_visible()
            page.goto(f"http://127.0.0.1:{server.server_port}/chat/{conversation.pk}/")
            page.get_by_text("Download generated.pdf").wait_for()
            page.locator(".skill-artifacts button").first.click()
            page.get_by_text("Page 1 of 1").wait_for()
            page.get_by_role("button", name="Close preview").click()
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
            page.locator('summary[aria-label="Attach files"]').click()
            assert page.get_by_text("Image understanding and image generation require a separate compatible capability.", exact=False).is_visible()
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
            page.get_by_role("link", name="Conversation files").click()
            assert page.get_by_role("table").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            for path in ("/inputs/", "/files/", f"/chat/{conversation.pk}/files/"):
                for value, theme in (("0", "light"), ("1", "light"), ("2", "dark")):
                    page.goto(f"http://127.0.0.1:{server.server_port}{path}")
                    page.locator("[data-theme-select]").evaluate(
                        "(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }",
                        value,
                    )
                    assert page.locator("html").get_attribute("data-theme") == theme
                    assert page.get_by_role("table").is_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.goto(f"http://127.0.0.1:{server.server_port}/chat/{conversation.pk}/files/")
            page.get_by_role("link", name="generated.pdf", exact=True).click()
            assert page.get_by_role("heading", name="generated.pdf").is_visible()
            assert page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Files").get_attribute("aria-current") == "page"
            assert page.get_by_text("demo", exact=True).is_visible()
            page.get_by_role("link", name="Back to conversation files").click()
            page.get_by_label("Group for generated_ver2.pdf").select_option(label="New separate group")
            page.get_by_label("Group for generated_ver2.pdf").locator("..").get_by_role("button", name="Move").click()
            page.get_by_label("Group for generated_ver2.pdf").select_option(index=0)
            page.get_by_label("Group for generated_ver2.pdf").locator("..").get_by_role("button", name="Move").click()
            page.get_by_role("link", name="Review deletion of all versions in generated.pdf").click()
            page.get_by_role("button", name="Confirm deletion").click()
            page.goto(f"http://127.0.0.1:{server.server_port}/chat/{conversation.pk}/")
            page.get_by_text("generated.pdf is no longer available").wait_for()
            page.get_by_role("link", name="Folders").click()
            page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Files").click()
            assert page.get_by_role("table").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            local_csv_row = page.get_by_role("row").filter(has_text="local.csv")
            local_csv_row.get_by_role("group", name="File actions").get_by_role("link", name="Download").focus()
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.tagName") == "A"
            local_csv_row.get_by_role("link", name="Delete").click()
            assert page.get_by_role("heading", name="Delete 1 file?").is_visible()
            page.get_by_role("button", name="Confirm deletion").click()
            page.goto(f"http://127.0.0.1:{server.server_port}/chat/{conversation.pk}/")
            assert page.get_by_text("local.csv is no longer available").is_visible()
            page.get_by_role("button", name="Sign out").click()
            page.get_by_label("Username:").fill("history-admin")
            page.get_by_label("Password:").fill("synthetic-admin-password-32")
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("link", name="Folders").click()
            page.get_by_role("navigation", name="Folder sections").get_by_role("link", name="Files").click()
            assert page.get_by_role("table").is_visible()
            assert page.get_by_role("button", name="Review deletion").is_disabled()
            page.get_by_label("User", exact=True).select_option(label="preview-user")
            page.get_by_role("button", name="Show files").click()
            assert page.get_by_role("button", name="Review deletion").is_enabled()
            local_pdf_row = page.get_by_role("row").filter(has_text="local.pdf")
            local_pdf_row.get_by_role("link", name="Download").click()
            page.get_by_role("button", name="Administrative access audit").click()
            assert page.get_by_text("download", exact=False).count() > 0
            browser.close()
        print("File preview browser smoke passed")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        connections.close_all()
        gateway.stop()
