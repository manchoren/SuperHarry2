"""Exercise multi-skill mentions with a synthetic gateway and user."""

# ruff: noqa: E402
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-skill-mentions-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-skill-mentions-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "ui.sqlite3"),
)

import django

django.setup()

# Keep the HTTP/browser smoke process single-threaded on Windows; the focused
# gateway contract tests cover the production child-process stream reader.
import superharry.chat
from superharry.gateway import stream_reply

superharry.chat.stream_reply = stream_reply

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from playwright.sync_api import sync_playwright


gateway_requests = []


class Gateway(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [
            {"id": "test/alternate", "capabilities": {
                "supportsThinking": True, "thinking": True, "effort_tiers": ["low", "medium"],
            }},
            {"id": "test/free-model", "capabilities": {"supportsThinking": False, "thinking": False}},
            {"id": "test/skill-mentions"},
        ]}).encode())

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        gateway_requests.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b'data: {"choices":[{"delta":{"reasoning":"Temporary thought"}}]}\n\n')
        self.wfile.flush()
        time.sleep(0.5)
        self.wfile.write(b'data: {"choices":[{"delta":{"content":"Synthetic reply"}}]}\n\n')
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


call_command("migrate", verbosity=0)
user = get_user_model().objects.create_user(
    username="skill-mentions-user", password="synthetic-password-32"
)
gateway = HTTPServer(("127.0.0.1", 0), Gateway)
threading.Thread(target=gateway.serve_forever, daemon=True).start()
settings.OMNIROUTE_URL = f"http://127.0.0.1:{gateway.server_port}"
settings.OMNIROUTE_API_KEY = "synthetic-gateway-key"
settings.CHAT_MODEL = "test/skill-mentions"
server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ["UI_BROWSER_PATH"], headless=True
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base + "/login/")
        page.get_by_label("Username:").fill("skill-mentions-user")
        page.get_by_label("Password:").fill("synthetic-password-32")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(base + "/")

        message = page.locator("#message")
        suggestions = page.get_by_role("listbox", name="Skill suggestions")
        page.locator("#conversation-model").select_option("test/alternate")
        reasoning = page.get_by_label("Reasoning")
        assert reasoning.is_visible()
        reasoning.fill("2")
        assert page.locator("#reasoning-label").inner_text() == "Medium"
        message.fill("Please mention /summ")
        suggestions.wait_for(state="visible")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        assert message.input_value() == "Please mention /summarize "

        message.fill("Please mention /summarize and /sum")
        suggestions.wait_for(state="visible")
        page.locator('#skill-suggestions [role="option"]').filter(
            has_text="/summarize"
        ).click()
        assert message.input_value() == "Please mention /summarize and /summarize "

        message.fill("Use /summ")
        suggestions.wait_for(state="visible")
        page.keyboard.press("Escape")
        assert suggestions.is_hidden()
        message.fill("Remove /not-ready")
        assert page.locator("#message-highlights .skill-mention.is-unavailable").is_visible()
        message.fill("Corrected request")
        assert page.locator("#message-highlights .skill-mention.is-unavailable").count() == 0
        message.fill("Please use /summarize and /not-ready, then /summarize again.")
        assert page.locator("#message-highlights .skill-mention").count() == 3
        assert page.locator("#message-highlights .skill-mention.is-unavailable").count() == 1
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.locator("#message-highlights .skill-mention.is-unavailable").is_visible()

        page.locator("#composer").get_by_role("button", name="Send").click()
        page.locator(".temporary-reasoning").get_by_text("Temporary thought").wait_for(state="visible")
        assert page.locator(".temporary-reasoning").is_visible()
        page.locator(".temporary-reasoning").wait_for(state="detached")
        page.wait_for_url("**/chat/*/")
        assert page.locator('[aria-label="Memory: Off"]').is_visible()
        assert page.locator("#reasoning-effort").input_value() == "2"
        assert page.locator("#reasoning-label").inner_text() == "Medium"
        assert "Context unknown" in page.locator(".context-doughnut").inner_text()
        page.locator("#compact-context").click()
        page.get_by_text("There are not enough older messages to compact.").wait_for(state="visible")
        assert page.locator("#composer a", has_text="Conversation files").is_visible()
        assert page.locator("#messages").get_by_text(
            "Please use /summarize and /not-ready, then /summarize again.", exact=True
        ).is_visible()
        assert page.locator("#messages .skill-mention").count() == 2
        unavailable = page.locator("#messages .skill-mention.is-unavailable")
        assert unavailable.is_visible()
        assert unavailable.get_attribute("aria-label") == "/not-ready, unavailable"
        assert gateway_requests[0]["model"] == "test/alternate"
        assert gateway_requests[0]["reasoning_effort"] == "medium"
        assert "Temporary thought" not in page.locator("#messages").inner_text()
        skill_request = gateway_requests[0]
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#conversation-model").select_option("free")
        page.get_by_text("Model changed to Free.").wait_for(state="visible")
        message.fill("Follow-up on Free")
        page.locator("#composer").get_by_role("button", name="Send").click()
        page.wait_for_function("() => document.querySelector('#message').value === ''")
        assert gateway_requests[-1]["model"] == "test/skill-mentions"

        context = next(
            item["content"]
            for item in skill_request["messages"]
            if "prompt mentions these skills" in item["content"]
        )
        descriptions = json.loads(context.split("Mentions: ", 1)[1])
        assert [item["name"] for item in descriptions] == ["summarize", "not-ready"]
        assert descriptions[0]["available"] is True
        assert descriptions[1]["available"] is False
        browser.close()
    print("PASS: model selection and confirmation, reasoning and temporary reasoning, context estimate and manual-compaction no-op, skill mentions, persistence, unavailable reporting, and mobile layout")
finally:
    server.shutdown()
    server.server_close()
    gateway.shutdown()
    gateway.server_close()
    connections.close_all()
    temporary_directory.cleanup()
