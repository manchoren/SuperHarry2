"""Check desktop chat-plus-panel and mobile full-screen browser return flow."""
# ruff: noqa: E402
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-browser-session-ui-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
spool_directory = Path(temporary_directory.name) / "spool"
spool_directory.mkdir()
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-browser-session-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "ui.sqlite3"),
    SKILL_SPOOL=str(spool_directory),
)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from playwright.sync_api import sync_playwright

from superharry.csv_protocol import publish
from superharry.models import Conversation, Message, SkillRun

call_command("migrate", verbosity=0)
owner = get_user_model().objects.create_user(username="browser-panel-ui", password="synthetic-password-32")
conversation = Conversation.objects.create(owner=owner, title="Browser panel fixture")
prompt = Message.objects.create(conversation=conversation, role=Message.Role.USER, content="Read this page")
reply = Message.objects.create(
    conversation=conversation, prompt=prompt, role=Message.Role.ASSISTANT,
    content="Read-only browser result for UI fixture", complete=True,
)
run = SkillRun.objects.create(
    owner=owner, skill_name="playwright", repository_skill=True, reply=reply,
    execution_payload={"runtime": "playwright-browser", "target_url": "https://fixture.example/article"},
)
token = "ui-test-consent-token-01234567890123456789"
publish(spool_directory / f"{run.job_id}.skill-running", json.dumps({
    "runtime": "playwright-browser", "target_url": "https://fixture.example/article",
    "browser_session": True, "browser_handoff": False, "_browser_owner_id": owner.pk,
    "_browser_consent_token": token,
}).encode(), replace=False)
publish(spool_directory / f"{run.job_id}.browser-consent", json.dumps({
    "job_id": str(run.job_id), "owner_id": owner.pk, "token": token,
    "expires_at": time.time() + 180,
}).encode(), replace=False)

server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

try:
    with sync_playwright() as playwright:
        installed_browser = os.environ.get("UI_BROWSER_PATH")
        if not installed_browser:
            candidates = sorted((Path.home() / "AppData/Local/ms-playwright").glob("chromium-*/chrome-win64/chrome.exe"))
            if not candidates:
                raise RuntimeError("Set UI_BROWSER_PATH to an installed Chromium executable")
            installed_browser = str(candidates[-1])
        browser = playwright.chromium.launch(executable_path=installed_browser, headless=True)
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_port}/login/")
            page.locator("#id_username").fill("browser-panel-ui")
            page.locator("#id_password").fill("synthetic-password-32")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url(f"http://127.0.0.1:{server.server_port}/")

            task_url = f"http://127.0.0.1:{server.server_port}/skill-runs/{run.job_id}/"
            publish(spool_directory / f"{run.job_id}.browser-view", page.screenshot(type="jpeg"), replace=False)
            with page.expect_response(
                lambda response: response.url.endswith(f"/skill-runs/{run.job_id}/browser-view/")
            ) as image_response_info:
                page.goto(task_url)
            panel = page.locator("[data-browser-session]")
            image = panel.get_by_role("img", name="Owner-only screenshot of the active public page")
            image_response = image_response_info.value
            assert image_response.status == 200
            assert "image/jpeg" in image_response.headers["content-type"]
            chat_frame = page.frame_locator("iframe[title='Conversation for this browser task']")
            chat_text = chat_frame.locator("body").inner_text()
            assert "Browser panel fixture" in chat_text, chat_text
            iframe_src = page.locator("iframe").get_attribute("src")
            assert iframe_src.endswith(f"/skill-runs/{run.job_id}/conversation/")
            image_src = image.get_attribute("src")
            assert page.locator("[data-browser-hide-icon][aria-hidden='true']").is_visible()
            assert not page.locator("[data-browser-show-icon]").is_visible()
            page.get_by_role("button", name="Hide browser").click()
            assert not image.is_visible()
            show_icon = page.locator("[data-browser-show-icon][aria-hidden='true']")
            icon_state = show_icon.evaluate("el => ({className: el.className, display: getComputedStyle(el).display})")
            assert show_icon.is_visible(), icon_state
            page.get_by_role("button", name="Open browser").click()
            assert image.is_visible()
            assert page.locator("[data-browser-hide-icon][aria-hidden='true']").is_visible()
            assert image.get_attribute("src") == image_src
            assert (spool_directory / f"{run.job_id}.skill-running").exists()

            page.set_viewport_size({"width": 390, "height": 844})
            panel_box = panel.bounding_box()
            assert panel_box is not None and panel_box["x"] == 0 and panel_box["y"] == 0
            assert abs(panel_box["width"] - 390) <= 1 and abs(panel_box["height"] - 844) <= 1, panel_box
            assert page.get_by_role("button", name="Return to conversation").is_visible()
            page.get_by_role("button", name="Return to conversation").click()
            page.wait_for_url(f"**/chat/{conversation.pk}/")
            assert (spool_directory / f"{run.job_id}.skill-running").exists()
            page.goto(task_url)
            assert page.locator("[data-browser-session]").is_visible()
            assert page.get_by_role("button", name="Return to conversation").is_visible()
        finally:
            browser.close()
    print("PASS: desktop conversation panel, hide/reopen same image, mobile full-screen return, session preserved")
finally:
    server.shutdown()
    thread.join()
    server.server_close()
    connections.close_all()
    temporary_directory.cleanup()
