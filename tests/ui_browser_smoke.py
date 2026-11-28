# Django must be configured before importing the application models.
# ruff: noqa: E402
import os
import json
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
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "data": [
                        {"id": "test/free-model"},
                        {
                            "id": "test/reasoning-model",
                            "capabilities": {
                                "supportsThinking": True,
                                "effort_tiers": ["low", "medium"],
                            },
                        },
                    ]
                }
            ).encode()
        )

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
gateway_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
gateway_thread.start()
settings.OMNIROUTE_URL = "http://127.0.0.1:%s" % gateway.server_port
settings.OMNIROUTE_API_KEY = "synthetic-key"
settings.CHAT_MODEL = "test/free-model"
from django.contrib.auth import get_user_model
from django.core.wsgi import get_wsgi_application
from django.contrib.staticfiles.handlers import StaticFilesHandler
import superharry.chat
from superharry.gateway import stream_reply

superharry.chat.stream_reply = stream_reply

call_command("migrate", verbosity=0)
User = get_user_model()
user = User.objects.create_user(username="ui-test", password="synthetic-password-32")
from superharry.models import Conversation, Message, Schedule
from django.utils import timezone
from datetime import timedelta

conversation = Conversation.objects.create(
    owner=user, title="Synthetic conversation", memory_enabled=False
)
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
from django.db import connections


def serve_application():
    try:
        server.serve_forever()
    finally:
        connections.close_all()


server_thread = threading.Thread(target=serve_application, daemon=True)
server_thread.start()
from playwright.sync_api import sync_playwright


def choose_theme(page, mode):
    slider = page.locator("[data-theme-select]")
    opened_popover = not slider.is_visible()
    if opened_popover:
        page.locator(".appearance-popover summary").click()
    slider.evaluate("(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }", {"light": "0", "system": "1", "dark": "2"}[mode])
    page.wait_for_function(
        "expected => document.documentElement.dataset.theme === (expected === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light') : expected)",
        arg=mode,
    )
    if opened_popover:
        page.locator(".appearance-popover[open] summary").click()


def assert_table_action_column(table):
    table_box = table.bounding_box()
    action_box = table.locator("thead th").last.bounding_box()
    content_boxes = [
        table.locator("thead th").nth(index).bounding_box()
        for index in range(table.locator("thead th").count() - 1)
    ]
    assert action_box["width"] < table_box["width"] * 0.5, (
        table.get_attribute("class"), table_box, action_box
    )
    assert max(box["width"] for box in content_boxes) > action_box["width"], (
        table.get_attribute("class"), content_boxes, action_box
    )


def capture_component_screenshot(page, name):
    screenshot_dir = os.environ.get("UI_COMPONENT_SCREENSHOT_DIR")
    if screenshot_dir:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(screenshot_dir) / (name + ".png")), full_page=True)


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
    assert page.get_by_role("navigation", name="Main navigation").get_by_role("link", name="Chat").get_attribute("aria-current") == "page"
    if os.environ.get("UI_LAYOUT_SCREENSHOT"):
        page.screenshot(path=os.environ["UI_LAYOUT_SCREENSHOT"], full_page=True)
    assert page.locator("html").get_attribute("data-theme") == "dark"
    original_url = page.url
    page.get_by_role("button", name="Search conversations (coming later)").click()
    assert page.url == original_url
    page.get_by_role("button", name="Hide conversations").click()
    page.wait_for_function("document.body.classList.contains('sidebar-closed')")
    assert not page.locator("#site-sidebar").is_visible()
    page.get_by_role("button", name="Toggle conversations").click()
    assert page.locator("#site-sidebar").is_visible()
    for path in ["/inputs/", "/schedules/", "/memories/", "/skills/", "/csv/"]:
        page.goto("http://127.0.0.1:18765" + path)
        assert page.locator("main").is_visible(), path
        assert page.get_by_role("navigation", name="Main navigation").is_visible(), path
        assert page.locator("#site-sidebar").count() == 0, path
        assert page.get_by_role("button", name="Toggle conversations").count() == 0, path
    page.goto("http://127.0.0.1:18765/chat/%s/" % conversation.pk)
    assert page.get_by_text("Incomplete reply").is_visible()
    assert page.get_by_role("button", name="Retry reply").is_visible()
    assert page.locator('[aria-label="Saved memory is off"]').count() == 1
    assert page.locator('[aria-label="Saved memory is off"]').get_attribute("title") == "Saved memory is off"
    assert page.get_by_label("Use saved memories in this conversation").count() == 0
    assert page.get_by_role("button", name="Save memory setting").count() == 0
    user_header = page.locator("#messages .message-header").first
    avatar = user_header.locator(".user-avatar")
    assert avatar.is_visible()
    label_box = user_header.locator("strong").bounding_box()
    avatar_box = avatar.bounding_box()
    assert abs((label_box["y"] + label_box["height"] / 2) - (avatar_box["y"] + avatar_box["height"] / 2)) < 2
    composer_options = page.locator("#composer .composer-options")
    model_disclosure = page.locator("#composer details.model-controls")
    assert not model_disclosure.evaluate("element => element.open")
    assert not page.locator("#conversation-model").is_visible()
    model_disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    assert model_disclosure.evaluate("element => element.open")
    assert page.locator("#conversation-model").is_visible()
    assert page.locator("#reasoning-effort").is_hidden()
    assert page.locator(".conversation-memory-state").is_visible()
    assert page.locator('[aria-label="Saved memory is off"]').is_visible()
    page.keyboard.press("Escape")
    page.goto("http://127.0.0.1:18765/chat/%s/" % conversation.pk)
    active_model = page.locator(".composer-active-model")
    model_name = page.locator(".composer-active-model-name")
    model_name.evaluate("element => { element.textContent = 'test/very-long-provider-model-name-for-ellipsis-acceptance'; element.style.maxWidth = '6rem'; }")
    assert page.evaluate("getComputedStyle(document.querySelector('.composer-active-model-name')).textOverflow") == "ellipsis"
    assert page.evaluate("document.querySelector('.composer-active-model-name').scrollWidth > document.querySelector('.composer-active-model-name').clientWidth")
    assert "test/free-model" in active_model.get_attribute("aria-label")
    assert "test/free-model" in active_model.get_attribute("title")
    rail = page.locator(".icon-rail")
    rail_icon = rail.locator(".rail-button svg.sh-icon").first
    assert 0 < float(rail_icon.evaluate("element => getComputedStyle(element).strokeWidth.replace('px', '')")) < 2
    rail_link = rail.get_by_role("link").first
    rail_link.focus()
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle !== 'none'")
    files_link = page.get_by_role("link", name="Conversation files")
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
    model_disclosure = page.locator("#composer details.model-controls")
    assert not model_disclosure.evaluate("element => element.open")
    assert not page.locator("#reasoning-effort").is_visible()
    model_disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    assert model_disclosure.evaluate("element => element.open")
    model_choice = page.locator("#conversation-model")
    assert model_choice.is_visible()
    assert page.locator("#reasoning-effort").is_hidden()
    model_choice.select_option("test/reasoning-model")
    assert page.locator("#reasoning-effort").is_visible()
    model_choice.select_option("auto")
    assert page.locator("#reasoning-effort").is_hidden()
    memory_choice = page.get_by_label("Use saved memories in this conversation")
    assert memory_choice.is_checked()
    tooltip = page.locator("#memory-toggle-tooltip")
    memory_choice.focus()
    assert tooltip.is_visible()
    assert tooltip.get_attribute("role") == "tooltip"
    model_disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    assert not model_disclosure.evaluate("element => element.open")
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
    model_disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    memory_choice = page.get_by_label("Use saved memories in this conversation")
    assert memory_choice.is_checked()
    memory_choice.focus()
    page.keyboard.press("Space")
    assert not memory_choice.is_checked()
    page.locator("#message").fill("Opt out of saved memories")
    page.locator("#composer").get_by_role("button", name="Send").click()
    page.wait_for_url("**/chat/*/")
    model_disclosure = page.locator("#composer details.model-controls")
    model_disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    assert page.locator('[aria-label="Saved memory is off"]').is_visible()
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
    model_disclosure = page.locator("#composer details.model-controls")
    model_disclosure.locator("summary").focus()
    page.keyboard.press("Enter")
    assert (
        page.locator("#messages").get_by_text("Hello controlled gateway").is_visible()
    )
    assert page.locator('[aria-label="Saved memory is on"]').is_visible()
    assert page.get_by_text("Browser streamed response").is_visible()
    if os.environ.get("UI_CHAT_ONLY") == "1":
        chat_violations = {}
        for mode in ("light", "dark"):
            choose_theme(page, mode)
            page.add_script_tag(path=axe_script)
            chat_violations[mode] = page.evaluate(
                "async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id)"
            )
        assert not any(chat_violations.values()), chat_violations
        print("PASS: Chat retry and streamed send, conversation memory opt-in, responsive sidebar, keyboard focus, axe light/dark")
        server.shutdown()
        server_thread.join(timeout=5)
        server.server_close()
        gateway.shutdown()
        gateway_thread.join(timeout=5)
        gateway.server_close()
        browser.close()
        connections.close_all()
        raise SystemExit(0)
    page.goto("http://127.0.0.1:18765/schedules/")
    assert page.get_by_text("Synthetic task").is_visible()
    assert page.get_by_role("table", name="Your scheduled tasks").is_visible()
    assert page.get_by_role("columnheader", name="Latest result").is_visible()
    page.get_by_role("link", name="View details").click()
    assert page.get_by_role("heading", name="Scheduled task #1").is_visible()
    assert page.get_by_text("Synthetic task", exact=True).is_visible()
    assert page.get_by_role("link", name="Back to schedules").is_visible()
    page.get_by_role("button", name="Cancel schedule").click()
    assert page.get_by_text("Canceled").is_visible()
    page.get_by_role("link", name="Back to schedules").click()
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
    assert page.get_by_text(long_filename[:60], exact=False).is_visible()
    page.set_viewport_size({"width": 1440, "height": 900})
    upload_form = page.locator(".input-upload-form")
    inputs_region = page.get_by_role("region", name="Chat inputs")
    upload_bottom = upload_form.bounding_box()["y"] + upload_form.bounding_box()["height"]
    table_top = inputs_region.bounding_box()["y"]
    assert table_top - upload_bottom >= 16, (upload_bottom, table_top)
    assert_table_action_column(page.get_by_role("table", name="Uploaded chat inputs"))
    page.goto("http://127.0.0.1:18765/files/")
    assert_table_action_column(page.get_by_role("table", name="Uploaded and generated files"))
    page.goto("http://127.0.0.1:18765/schedules/")
    assert_table_action_column(page.get_by_role("table", name="Your scheduled tasks"))
    page.goto("http://127.0.0.1:18765/inputs/")
    page.set_viewport_size({"width": 390, "height": 844})
    inputs_region = page.get_by_role("region", name="Chat inputs")
    assert inputs_region.evaluate("element => element.scrollWidth >= element.clientWidth")
    upload_bottom = page.locator(".input-upload-form").bounding_box()["y"] + page.locator(".input-upload-form").bounding_box()["height"]
    table_top = inputs_region.bounding_box()["y"]
    assert table_top - upload_bottom >= 16, (upload_bottom, table_top)
    assert_table_action_column(page.get_by_role("table", name="Uploaded chat inputs"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size({"width": 1440, "height": 900})
    choose_theme(page, "system")
    assert page.locator("html").get_attribute("data-theme") == "light"
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
    choose_theme(page, "light")
    for listing_name, listing_path in (
        ("inputs", "/inputs/"),
        ("files", "/files/"),
        ("memories", "/memories/"),
        ("skills", "/skills/"),
        ("schedules", "/schedules/"),
    ):
        for viewport_name, viewport in (
            ("desktop", {"width": 1440, "height": 900}),
            ("narrow", {"width": 390, "height": 844}),
        ):
            page.set_viewport_size(viewport)
            for theme_name in ("light", "dark"):
                page.goto("http://127.0.0.1:18765" + listing_path)
                choose_theme(page, theme_name)
                effective_colors = page.evaluate(
                    "() => ({theme: document.documentElement.dataset.theme, page: getComputedStyle(document.body).backgroundColor, colorScheme: getComputedStyle(document.body).colorScheme, component: getComputedStyle(document.querySelector('.sh')).getPropertyValue('--sh-bg').trim()})"
                )
                assert effective_colors["theme"] == theme_name, (listing_name, effective_colors)
                assert effective_colors["colorScheme"] == theme_name, (listing_name, effective_colors)
                expected_background = "rgb(248, 247, 244)" if theme_name == "light" else "rgb(24, 30, 34)"
                assert effective_colors["page"] == expected_background, (listing_name, effective_colors)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
                    listing_name, viewport_name, theme_name
                )
                tables = page.locator("table.sh-table-actions")
                if viewport_name == "desktop":
                    for index in range(tables.count()):
                        if tables.nth(index).is_visible():
                            assert_table_action_column(tables.nth(index))
                if listing_name in {"inputs", "files"}:
                    capture_component_screenshot(
                        page,
                        f"{listing_name}-{viewport_name}-{theme_name}",
                    )
    for viewport_name, viewport in (
        ("desktop", {"width": 1440, "height": 900}),
        ("narrow", {"width": 390, "height": 844}),
    ):
        for theme_name in ("light", "dark"):
            page.set_viewport_size(viewport)
            page.goto("http://127.0.0.1:18765/")
            choose_theme(page, theme_name)
            capture_component_screenshot(page, f"composer-{viewport_name}-{theme_name}")
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto("http://127.0.0.1:18765/inputs/")
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
    toggle = page.get_by_role("button", name="Toggle conversations")
    page.get_by_role("button", name="Toggle conversations").focus()
    page.keyboard.press("Enter")
    assert page.locator("#site-sidebar").is_visible()
    page.keyboard.press("Escape")
    assert not page.locator("#site-sidebar").is_visible()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement === document.querySelector('.menu-toggle')")
    message_box = page.locator("#message")
    message_box.focus()
    page.keyboard.press("Escape")
    assert not page.locator("#site-sidebar").is_visible()
    assert page.evaluate("document.activeElement === document.querySelector('#message')")
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
                "async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => ({target:n.target, failureSummary:n.failureSummary}))}))"
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
server_thread.join(timeout=5)
server.server_close()
gateway.shutdown()
gateway_thread.join(timeout=5)
gateway.server_close()

connections.close_all()


temporary_directory.cleanup()
