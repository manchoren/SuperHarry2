"""Verify Folders navigation and active tabs using synthetic files in Chromium."""
# ruff: noqa: E402
import os
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-folders-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-folders-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "folders.sqlite3"),
)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from playwright.sync_api import sync_playwright

from superharry.models import ArtifactGroup, ChatInput, Conversation, Message, SkillArtifact, SkillRun

call_command("migrate", verbosity=0)
owner = get_user_model().objects.create_user(username="folders-user", password="synthetic-password-32")
empty_user = get_user_model().objects.create_user(username="empty-folders-user", password="synthetic-password-32")
admin = get_user_model().objects.create_user(username="folders-admin", password="synthetic-admin-password-32")
admin.groups.add(Group.objects.get_or_create(name="Administrators")[0])
conversation = Conversation.objects.create(owner=owner, title="Folders synthetic conversation")
prompt = Message.objects.create(conversation=conversation, role="user", content="synthetic attachment")
chat_input = ChatInput.objects.create(
    owner=owner, message=prompt, name="input.txt", media_type="text/plain", payload=b"synthetic input"
)
reply = Message.objects.create(conversation=conversation, role="assistant", content="synthetic generated file")
run = SkillRun.objects.create(owner=owner, reply=reply, skill_name="demo", repository_skill=True)
group = ArtifactGroup.objects.create(owner=owner, conversation=conversation, name="output.txt")
artifact = SkillArtifact.objects.create(run=run, group=group, name="output.txt", payload=b"synthetic output")

server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))


def serve():
    try:
        server.serve_forever()
    finally:
        connections.close_all()


thread = threading.Thread(target=serve, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}"
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ["UI_BROWSER_PATH"], headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(base + "/login/")
        page.get_by_label("Username:").fill("folders-user")
        page.get_by_label("Password:").fill("synthetic-password-32")
        page.get_by_role("button", name="Sign in").click()

        folders_entry = page.get_by_role("link", name="Folders", exact=True)
        assert folders_entry.count() == 1
        assert folders_entry.get_attribute("href") == "/inputs/"
        folders_entry.click()
        assert page.url.endswith("/inputs/")
        tabs = page.get_by_role("navigation", name="Folder sections")
        inputs_tab = tabs.get_by_role("link", name="Inputs")
        files_tab = tabs.get_by_role("link", name="Files")
        assert inputs_tab.get_attribute("aria-current") == "page"
        inputs_tab.focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.textContent.trim()") == "Files"
        files_tab.click()
        assert page.url.endswith("/files/")
        assert files_tab.get_attribute("aria-current") == "page"
        page.get_by_role("row").filter(has_text="input.txt").get_by_role("link", name="Details").click()
        assert page.get_by_role("heading", name="input.txt").is_visible()
        assert page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Files"
        ).get_attribute("aria-current") == "page"

        page.goto(base + f"/inputs/{chat_input.pk}/")
        assert page.get_by_role("heading", name=f"Input #{chat_input.pk}").is_visible()
        assert page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Inputs"
        ).get_attribute("aria-current") == "page"
        page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Files"
        ).click()
        assert page.url.endswith("/files/")
        page.goto(base + f"/files/artifact/{artifact.pk}/")
        assert page.get_by_role("heading", name="output.txt").is_visible()
        assert page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Files"
        ).get_attribute("aria-current") == "page"
        page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Inputs"
        ).click()
        assert page.url.endswith("/inputs/")
        page.goto(base + f"/chat/{conversation.pk}/files/")
        assert page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Files"
        ).get_attribute("aria-current") == "page"
        assert page.get_by_role("link", name="output.txt", exact=True).is_visible()
        assert page.get_by_label("Group for output.txt").is_visible()
        page.get_by_role("link", name="Review deletion of all versions in output.txt").click()
        assert page.get_by_role("heading", name="Delete 1 file?").is_visible()
        assert page.get_by_text("output.txt (generated)").is_visible()
        page.get_by_role("link", name="Cancel").click()
        assert page.url.endswith("/files/")
        page.goto(base + f"/files/artifact/{artifact.pk}/")
        page.get_by_role("navigation", name="Folder sections").get_by_role(
            "link", name="Files"
        ).click()
        assert page.url.endswith("/files/")

        page.set_viewport_size({"width": 375, "height": 812})
        for path in ("/inputs/", "/files/", f"/inputs/{chat_input.pk}/", f"/files/artifact/{artifact.pk}/"):
            page.goto(base + path)
            assert page.get_by_role("navigation", name="Folder sections").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        empty_page = browser.new_page(viewport={"width": 375, "height": 812})
        empty_page.goto(base + "/login/")
        empty_page.get_by_label("Username:").fill("empty-folders-user")
        empty_page.get_by_label("Password:").fill("synthetic-password-32")
        empty_page.get_by_role("button", name="Sign in").click()
        empty_page.goto(base + "/inputs/")
        assert empty_page.get_by_text("No chat inputs yet").is_visible()
        empty_page.goto(base + "/files/")
        assert empty_page.get_by_text("No files found.").is_visible()
        assert empty_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert empty_page.request.get(base + "/files/audit/").status in (403, 404)
        empty_page.close()
        admin_page = browser.new_page()
        admin_page.goto(base + "/login/")
        admin_page.get_by_label("Username:").fill("folders-admin")
        admin_page.get_by_label("Password:").fill("synthetic-admin-password-32")
        admin_page.get_by_role("button", name="Sign in").click()
        admin_page.goto(base + "/files/audit/")
        assert admin_page.get_by_role("heading", name="Administrative file access audit").is_visible()
        assert admin_page.get_by_role("table").is_visible()
        assert admin_page.get_by_role("link", name="Folders").get_attribute("aria-current") == "page"
        admin_page.close()
        browser.close()
    print("Folders navigation browser smoke passed")
finally:
    server.shutdown()
    thread.join()
    server.server_close()
    connections.close_all()
    temporary_directory.cleanup()
