"""Exercise the schedule listing and detail flow in an isolated browser session."""

# Django must be configured before importing application models.
# ruff: noqa: E402
import os
import atexit
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-schedule-ui-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-schedule-ui-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "ui.sqlite3"),
)

import django

django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from django.utils import timezone
from django.contrib.staticfiles.handlers import StaticFilesHandler

from superharry.models import Conversation, Schedule

call_command("migrate", verbosity=0)
user = get_user_model().objects.create_user(
    username="schedule-ui-test", password="synthetic-schedule-password"
)
conversation = Conversation.objects.create(owner=user, title="Synthetic schedule conversation")
prompt = "Synthetic schedule prompt with complete detail content. " * 4
Schedule.objects.create(
    owner=user,
    conversation=conversation,
    prompt=prompt,
    next_run=timezone.now() + timedelta(days=1),
    timezone="America/Guayaquil",
    repetition="daily",
    preferred_model="test/synthetic-model",
)

server = make_server(
    "127.0.0.1", 0, StaticFilesHandler(get_wsgi_application())
)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()
atexit.register(temporary_directory.cleanup)
atexit.register(connections.close_all)
atexit.register(server.shutdown)

from playwright.sync_api import sync_playwright


def choose_theme(page, value):
    slider = page.locator("[data-theme-select]")
    if not slider.is_visible():
        page.locator(".appearance-popover summary").click()
    slider.evaluate(
        "(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }",
        str(value),
    )


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path=os.environ["UI_BROWSER_PATH"], headless=True
    )
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme="light"
    )
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{port}/login/")
    page.get_by_label("Username").fill("schedule-ui-test")
    page.get_by_label("Password").fill("synthetic-schedule-password")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"http://127.0.0.1:{port}/")

    page.goto(f"http://127.0.0.1:{port}/schedules/")
    table = page.get_by_role("table", name="Your scheduled tasks")
    assert table.is_visible()
    for column in (
        "Task",
        "Prompt",
        "Conversation",
        "Status",
        "Next execution",
        "Latest outcome",
        "Preferred model",
        "Actions",
    ):
        assert page.get_by_role("columnheader", name=column).is_visible()
    assert table.get_by_text("Synthetic schedule conversation").is_visible()
    assert table.get_by_text("Scheduled", exact=True).is_visible()
    assert table.get_by_role("cell", name="test/synthetic-model", exact=True).is_visible()
    for preference, expected_theme in ((0, "light"), (1, "light"), (2, "dark")):
        choose_theme(page, preference)
        assert page.locator("html").get_attribute("data-theme") == expected_theme

    detail_link = page.get_by_role("link", name="Scheduled task #1")
    detail_link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url(f"http://127.0.0.1:{port}/schedules/1/")
    assert page.get_by_role("heading", name="Scheduled task #1").is_visible()
    assert page.get_by_text(prompt, exact=True).is_visible()
    assert page.get_by_text("America/Guayaquil", exact=False).is_visible()
    assert page.get_by_role("button", name="Save model").is_visible()
    assert page.get_by_role("button", name="Cancel schedule").is_visible()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"http://127.0.0.1:{port}/schedules/")
    assert page.get_by_role("table", name="Your scheduled tasks").is_visible()
    assert page.get_by_text("Scheduled", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    print("PASS: schedule table, full detail, keyboard navigation, permitted actions, and mobile layout")
    browser.close()
