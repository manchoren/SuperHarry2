"""Exercise schedule list, detail, actions, and responsive states in isolation."""

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
database_path = Path(temporary_directory.name) / "ui.sqlite3"
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-schedule-ui-test",
    DJANGO_DATABASE_PATH=str(database_path),
)

import django

django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from django.utils import timezone
from django.contrib.staticfiles.handlers import StaticFilesHandler

from superharry.models import Conversation, Schedule, ScheduleOccurrence

call_command("migrate", verbosity=0)
user = get_user_model().objects.create_user(
    username="schedule-ui-test", password="synthetic-schedule-password"
)
conversation = Conversation.objects.create(
    owner=user,
    title="Synthetic schedule conversation with a deliberately long title to test wrapping",
)
prompt = "Synthetic schedule prompt with complete detail content. " * 8
scheduled = Schedule.objects.create(
    owner=user,
    conversation=conversation,
    prompt=prompt,
    next_run=timezone.now() + timedelta(days=1),
    timezone="America/Guayaquil",
    repetition="daily",
    preferred_model="test/synthetic-model",
)
ScheduleOccurrence.objects.create(
    schedule=scheduled,
    due_at=timezone.now(),
    status="needs_user_input",
    user_request="Synthetic approval request",
)
running_conversation = Conversation.objects.create(owner=user, title="Running work")
running = Schedule.objects.create(
    owner=user,
    conversation=running_conversation,
    prompt="Synthetic running task",
    next_run=None,
    timezone="UTC",
    repetition="once",
)
ScheduleOccurrence.objects.create(
    schedule=running,
    due_at=timezone.now(),
    status="running",
)
canceled_conversation = Conversation.objects.create(owner=user, title="Canceled work")
canceled = Schedule.objects.create(
    owner=user,
    conversation=canceled_conversation,
    prompt="Synthetic canceled task",
    next_run=None,
    timezone="UTC",
    repetition="once",
    canceled=True,
)
empty_user = get_user_model().objects.create_user(
    username="empty-schedule-ui-test", password="synthetic-empty-password"
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


def sign_in(page, username, password):
    page.goto(f"http://127.0.0.1:{port}/login/")
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"http://127.0.0.1:{port}/")


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(
        executable_path=os.environ["UI_BROWSER_PATH"], headless=True
    )
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme="light"
    )
    page = context.new_page()
    sign_in(page, "schedule-ui-test", "synthetic-schedule-password")

    page.goto(f"http://127.0.0.1:{port}/chat/{conversation.pk}/")
    summary = page.locator(".chat-scheduled-tasks")
    assert summary.get_by_text("Scheduled", exact=True).is_visible()
    assert summary.get_by_text("Next:", exact=False).is_visible()
    assert summary.get_by_text("America/Guayaquil", exact=False).is_visible()
    assert summary.get_by_role("link", name="View task details").get_attribute("href") == f"/schedules/{scheduled.pk}/"
    assert summary.get_by_text(prompt, exact=True).count() == 0
    assert summary.get_by_role("button", name="Cancel schedule").count() == 0
    page.goto(f"http://127.0.0.1:{port}/chat/{running_conversation.pk}/")
    assert page.locator(".chat-scheduled-tasks").get_by_text("Running", exact=True).is_visible()
    page.goto(f"http://127.0.0.1:{port}/chat/{canceled_conversation.pk}/")
    assert page.locator(".chat-scheduled-tasks").get_by_text("Canceled", exact=True).is_visible()

    page.goto(f"http://127.0.0.1:{port}/schedules/")
    table = page.get_by_role("table", name="Your scheduled tasks")
    assert table.is_visible()
    for column in ("Task", "Status", "Next execution", "Latest result"):
        assert page.get_by_role("columnheader", name=column).is_visible()
    assert page.get_by_role("columnheader", name="Details").is_visible()
    assert table.get_by_text("Scheduled", exact=True).is_visible()
    assert table.get_by_text("Running", exact=True).is_visible()
    assert table.get_by_text("Canceled", exact=True).is_visible()
    assert table.get_by_text("Needs user input", exact=True).is_visible()
    assert table.get_by_text("Running work", exact=True).is_visible()
    assert table.get_by_text("Canceled work", exact=True).is_visible()
    assert table.get_by_role("link", name=f"View details for scheduled task #{scheduled.pk}").is_visible()
    assert not table.get_by_text("test/synthetic-model", exact=True).count()
    assert not table.get_by_role("button", name="Cancel schedule").count()
    assert not table.get_by_role("button", name="Save model").count()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(Path(tempfile.gettempdir()) / "superharry-schedules-desktop.png"), full_page=True)

    page.get_by_role("link", name=f"View details for scheduled task #{scheduled.pk}").click()
    page.wait_for_url(f"http://127.0.0.1:{port}/schedules/{scheduled.pk}/")
    assert page.get_by_role("heading", name=f"Scheduled task #{scheduled.pk}").is_visible()
    prompt_detail = page.locator(".schedule-prompt-full")
    assert prompt_detail.is_visible()
    assert prompt_detail.text_content() == prompt
    assert page.get_by_text("America/Guayaquil", exact=False).is_visible()
    assert page.get_by_text("Synthetic approval request", exact=True).is_visible()
    assert page.get_by_label("Preferred model").is_visible()
    assert page.get_by_role("button", name="Save model").is_visible()
    assert page.get_by_role("button", name="Cancel schedule").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(Path(tempfile.gettempdir()) / "superharry-schedule-detail-desktop.png"), full_page=True)

    page.set_viewport_size({"width": 390, "height": 844})
    assert page.get_by_role("button", name="Save model").is_visible()
    assert page.get_by_role("button", name="Cancel schedule").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(Path(tempfile.gettempdir()) / "superharry-schedule-detail-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 820, "height": 1180})
    assert page.get_by_role("button", name="Save model").is_visible()
    assert page.get_by_role("button", name="Cancel schedule").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.goto(f"http://127.0.0.1:{port}/schedules/")
    for value in ("Scheduled", "Running", "Canceled", "Needs user input"):
        assert page.get_by_text(value, exact=True).is_visible()
    assert page.get_by_role("link", name=f"View details for scheduled task #{scheduled.pk}").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(Path(tempfile.gettempdir()) / "superharry-schedules-mobile.png"), full_page=True)

    page.get_by_role("link", name=f"View details for scheduled task #{scheduled.pk}").click()
    page.wait_for_url(f"http://127.0.0.1:{port}/schedules/{scheduled.pk}/")
    page.get_by_label("Preferred model").select_option("")
    page.get_by_role("button", name="Save model").click()
    page.wait_for_url(f"http://127.0.0.1:{port}/schedules/{scheduled.pk}/")
    page.get_by_role("button", name="Cancel schedule").click()
    page.wait_for_url(f"http://127.0.0.1:{port}/schedules/{scheduled.pk}/")
    assert page.get_by_text("Canceled", exact=True).is_visible()
    assert not page.get_by_role("button", name="Cancel schedule").count()
    page.goto(f"http://127.0.0.1:{port}/schedules/")
    canceled_row = page.get_by_role("row").filter(has_text=f"Scheduled task #{scheduled.pk}")
    assert canceled_row.get_by_text("Canceled", exact=True).is_visible()

    context.clear_cookies()
    sign_in(page, "empty-schedule-ui-test", "synthetic-empty-password")
    page.goto(f"http://127.0.0.1:{port}/schedules/")
    assert page.get_by_role("heading", name="No scheduled tasks").is_visible()
    assert page.get_by_text("next run and latest result", exact=False).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    print("PASS: schedule states, compact table, detail actions, empty state, and desktop/mobile layouts")
    browser.close()
