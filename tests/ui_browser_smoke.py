# Django must be configured before importing the application models.
# ruff: noqa: E402
import os
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-ui-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-ui-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "ui.sqlite3"),
)
import django

django.setup()
from django.core.management import call_command
from django.conf import settings
from http.server import BaseHTTPRequestHandler, HTTPServer
import time


class Gateway(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(
            b'data: {"choices":[{"delta":{"content":"Browser streamed "}}]}\n\n'
        )
        self.wfile.flush()
        time.sleep(3)
        self.wfile.write(
            b'data: {"choices":[{"delta":{"content":"response"}}]}\n\ndata: [DONE]\n\n'
        )
        self.wfile.flush()

    def log_message(self, *args):
        pass


gateway = HTTPServer(("127.0.0.1", 0), Gateway)
threading.Thread(target=gateway.serve_forever, daemon=True).start()
settings.OMNIROUTE_URL = "http://127.0.0.1:%s" % gateway.server_port
settings.OMNIROUTE_API_KEY = "synthetic-key"
settings.CHAT_MODEL = "test/free-model"
from django.contrib.auth import get_user_model
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler

call_command("migrate", verbosity=0)
User = get_user_model()
user = User.objects.create_user(username="ui-test", password="synthetic-password-32")
from superharry.models import Conversation, Message, Schedule
from django.utils import timezone
from datetime import timedelta

conversation = Conversation.objects.create(owner=user, title="Synthetic conversation")
prompt = Message.objects.create(
    conversation=conversation, role="user", content="Synthetic prompt"
)
Message.objects.create(
    conversation=conversation,
    role="assistant",
    content="Partial reply",
    complete=False,
    prompt=prompt,
)
Schedule.objects.create(
    owner=user,
    conversation=conversation,
    prompt="Synthetic task",
    next_run=timezone.now() + timedelta(days=1),
    timezone="America/Guayaquil",
    repetition="daily",
)
server = make_server("127.0.0.1", 18765, StaticFilesHandler(get_wsgi_application()))
threading.Thread(target=server.serve_forever, daemon=True).start()
from playwright.sync_api import sync_playwright


def choose_theme(page, mode):
    slider = page.locator("[data-theme-select]")
    if not slider.is_visible():
        page.locator(".appearance-popover summary").click()
    slider.evaluate("(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }", {"light": "0", "system": "1", "dark": "2"}[mode])


with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path=os.environ["UI_BROWSER_PATH"], headless=True
    )
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme="light"
    )
    page = context.new_page()
    page.goto("http://127.0.0.1:18765/login/")
    assert page.locator("h1").inner_text() == "Sign in"
    axe_script = os.environ["UI_AXE_PATH"]
    page.add_script_tag(path=axe_script)
    for mode in ["light", "dark"]:
        choose_theme(page, mode)
        violations = page.evaluate(
            "async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id)"
        )
        assert not violations, ("login", mode, violations)
    assert page.locator("html").get_attribute("data-theme") == "dark"
    page.reload()
    assert page.locator("[data-theme-select]").input_value() == "2"
    page.locator("#id_username").fill("ui-test")
    page.locator("#id_password").fill("synthetic-password-32")
    page.get_by_role("button", name="Sign in").click()
    assert page.get_by_role("heading", name="New chat").is_visible()
    if os.environ.get("UI_LAYOUT_SCREENSHOT"):
        page.screenshot(path=os.environ["UI_LAYOUT_SCREENSHOT"], full_page=True)
    assert page.locator("html").get_attribute("data-theme") == "dark"
    original_url = page.url
    page.get_by_role("button", name="Search conversations (coming later)").click()
    assert page.url == original_url
    page.get_by_role("button", name="Hide conversations").click()
    assert not page.locator("#site-sidebar").is_visible()
    page.get_by_role("button", name="Toggle conversations").click()
    assert page.locator("#site-sidebar").is_visible()
    for path in ["/inputs/", "/schedules/", "/memories/", "/skills/", "/csv/"]:
        page.goto("http://127.0.0.1:18765" + path)
        assert page.locator("main").is_visible(), path
        assert page.get_by_role("navigation", name="Main navigation").is_visible(), path
    page.goto("http://127.0.0.1:18765/chat/%s/" % conversation.pk)
    assert page.get_by_text("Incomplete reply").is_visible()
    assert page.get_by_role("button", name="Retry reply").is_visible()
    assert page.locator('[aria-label="Memory: Off"]').is_visible()
    assert page.locator('[aria-label="Memory: Off"]').get_attribute("title") == "Memory: Off"
    assert page.get_by_label("Use saved memories in this conversation").count() == 0
    assert page.get_by_role("button", name="Save memory setting").count() == 0
    user_header = page.locator("#messages .message-header").first
    avatar = user_header.locator(".user-avatar")
    assert avatar.is_visible()
    label_box = user_header.locator("strong").bounding_box()
    avatar_box = avatar.bounding_box()
    assert abs((label_box["y"] + label_box["height"] / 2) - (avatar_box["y"] + avatar_box["height"] / 2)) < 2
    composer_options = page.locator("#composer .composer-options")
    files_link = composer_options.get_by_role("link", name="Conversation files")
    assert files_link.is_visible()
    assert composer_options.locator("details.composer-popover summary").nth(0).is_visible()
    assert composer_options.locator("details.composer-popover summary").nth(1).is_visible()
    files_link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/chat/%s/files/" % conversation.pk)
    page.goto("http://127.0.0.1:18765/chat/%s/" % conversation.pk)
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 1440, "height": 900})
    retry_box = page.get_by_role("button", name="Retry reply").bounding_box()
    assert retry_box["height"] >= 44
    page.get_by_role("button", name="Retry reply").click()
    page.locator("#chat-status").get_by_text("Waiting", exact=False).wait_for(
        timeout=10000
    )
    page.locator("#messages").get_by_text("Browser streamed", exact=False).wait_for(
        timeout=10000
    )
    assert "Receiving" in page.locator("#chat-status").inner_text()
    page.get_by_text("Browser streamed response").wait_for(timeout=10000)
    page.wait_for_url("**/chat/%s/" % conversation.pk)
    assert page.get_by_text("Browser streamed response").is_visible()
    page.goto("http://127.0.0.1:18765/")
    message_box = page.locator("#message")
    message_box.fill("Please use /summ")
    suggestions = page.get_by_role("listbox", name="Skill suggestions")
    suggestions.wait_for(state="visible")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert message_box.input_value() == "Please use /summarize "
    message_box.fill("Use /summ")
    suggestions.wait_for(state="visible")
    page.locator('#skill-suggestions [role="option"]').filter(has_text="/summarize").click()
    assert message_box.input_value() == "Use /summarize "
    message_box.fill("Mention /not-available")
    assert page.locator("#message-highlights .skill-mention.is-unavailable").is_visible()
    message_box.fill("Corrected request")
    assert page.locator("#message-highlights .skill-mention.is-unavailable").count() == 0
    message_box.fill("Please use /summ")
    suggestions.wait_for(state="visible")
    page.keyboard.press("Escape")
    assert suggestions.is_hidden()
    message_box.fill("")
    memory_choice = page.get_by_label("Use saved memories in this conversation")
    assert not memory_choice.is_checked()
    memory_choice.focus()
    page.keyboard.press("Space")
    assert memory_choice.is_checked()
    page.locator("#message").fill("Opt into saved memories")
    page.locator("#composer").get_by_role("button", name="Send").click()
    page.wait_for_url("**/chat/*/")
    assert page.locator('[aria-label="Memory: On"]').is_visible()
    assert page.get_by_label("Use saved memories in this conversation").count() == 0
    page.goto("http://127.0.0.1:18765/")
    page.locator("#message").fill("Hello controlled gateway")
    page.locator("#composer").get_by_role("button", name="Send").click()
    page.locator("#chat-status").get_by_text("Waiting", exact=False).wait_for(
        timeout=10000
    )
    page.locator("#messages").get_by_text("Browser streamed", exact=False).wait_for(
        timeout=10000
    )
    assert "Receiving" in page.locator("#chat-status").inner_text()
    page.get_by_text("Browser streamed response").wait_for(timeout=10000)
    page.wait_for_url("**/chat/*/")
    assert (
        page.locator("#messages").get_by_text("Hello controlled gateway").is_visible()
    )
    assert page.get_by_text("Browser streamed response").is_visible()
    page.goto("http://127.0.0.1:18765/schedules/")
    assert page.get_by_text("Synthetic task").is_visible()
    assert page.get_by_role("table", name="Your scheduled tasks").is_visible()
    assert page.get_by_role("columnheader", name="Latest outcome").is_visible()
    page.get_by_role("link", name="View details").click()
    assert page.get_by_role("heading", name="Scheduled task #1").is_visible()
    assert page.get_by_text("Synthetic task", exact=True).is_visible()
    assert page.get_by_role("link", name="Back to schedules").is_visible()
    page.get_by_role("link", name="Back to schedules").click()
    page.get_by_role("button", name="Cancel schedule").click()
    assert page.get_by_text("Canceled").is_visible()
    page.goto("http://127.0.0.1:18765/memories/new/")
    page.locator("textarea").fill("Synthetic memory for browser validation")
    page.get_by_role("button", name="Save memory").click()
    assert page.get_by_text("Synthetic memory for browser validation").is_visible()
    page.goto("http://127.0.0.1:18765/inputs/")
    page.locator("input[type=file]").set_input_files(
        {
            "name": "synthetic.csv",
            "mimeType": "text/csv",
            "buffer": b"name,amount\nexample,3\n",
        }
    )
    page.get_by_role("button", name="Upload").click()
    assert page.get_by_text("synthetic.csv").is_visible()
    long_filename = "synthetic-" + ("reference-material-" * 3) + "mobile-layout-check.csv"
    page.locator("input[type=file]").set_input_files(
        {
            "name": long_filename,
            "mimeType": "text/csv",
            "buffer": b"name,amount\nexample,4\n",
        }
    )
    page.get_by_role("button", name="Upload").click()
    assert page.get_by_text(long_filename).is_visible()
    choose_theme(page, "system")
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    choose_theme(page, "light")
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://127.0.0.1:18765/")
    assert not page.locator("#site-sidebar").is_visible()
    page.get_by_role("button", name="Toggle conversations").click()
    assert page.locator("#site-sidebar").is_visible()
    assert (
        page.get_by_role("button", name="Toggle conversations").get_attribute("aria-expanded") == "true"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.get_by_role("button", name="Toggle conversations").click()
    assert not page.locator("#site-sidebar").is_visible()
    page.get_by_role("button", name="Toggle conversations").focus()
    page.keyboard.press("Enter")
    assert page.locator("#site-sidebar").is_visible()
    page.keyboard.press("Escape")
    assert not page.locator("#site-sidebar").is_visible()
    page.keyboard.press("Tab")
    assert page.evaluate(
        "getComputedStyle(document.activeElement).outlineStyle !== 'none'"
    )
    page.emulate_media(reduced_motion="reduce")
    assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
    page.set_viewport_size({"width": 1440, "height": 900})
    axe_script = os.environ["UI_AXE_PATH"]
    for path in [
        "/",
        "/inputs/",
        "/schedules/",
        "/memories/",
        "/memories/new/",
        "/skills/",
        "/skills/new/",
        "/csv/",
    ]:
        page.goto("http://127.0.0.1:18765" + path)
        for mode in ["light", "dark"]:
            choose_theme(page, mode)
            page.add_script_tag(path=axe_script)
            results = page.evaluate(
                "async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => ({id:v.id, nodes:v.nodes.length, impact:v.impact}))"
            )
            assert not results, ("AXE", path, mode, results)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto("http://127.0.0.1:18765/schedules/")
    assert page.get_by_role("table", name="Your scheduled tasks").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 375, "height": 812})
    page.goto("http://127.0.0.1:18765/files/")
    assert page.get_by_role("table").locator("tbody tr").count() == 2
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    print(
        "PASS: login, navigation, desktop, mobile menu, light/dark/system persistence and system changes"
    )
    browser.close()
server.shutdown()
gateway.shutdown()
from django.db import connections

connections.close_all()


temporary_directory.cleanup()
