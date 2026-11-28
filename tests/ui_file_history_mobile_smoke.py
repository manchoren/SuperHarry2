"""Check file-history layout, button navigation, and long names at desktop and mobile widths."""
# ruff: noqa: E402

import os
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-file-history-ui-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-file-history-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "ui.sqlite3"),
)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from playwright.sync_api import sync_playwright

from superharry.models import ChatInput

call_command("migrate", verbosity=0)
user = get_user_model().objects.create_user(
    username="file-history-ui-test", password="synthetic-password-32"
)
long_name = "synthetic-" + ("reference-material-" * 3) + "mobile-layout-check.csv"
ChatInput.objects.create(
    owner=user,
    name=long_name,
    media_type="text/plain",
    payload=b"name,amount\nexample,4\n",
)

server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()

try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ["UI_BROWSER_PATH"], headless=True
        )
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{server.server_port}/login/")
            page.locator("#id_username").fill("file-history-ui-test")
            page.locator("#id_password").fill("synthetic-password-32")
            page.get_by_role("button", name="Sign in").click()
            page.wait_for_url(f"http://127.0.0.1:{server.server_port}/")
            page.goto(f"http://127.0.0.1:{server.server_port}/files/")
            assert page.get_by_role("table").is_visible()
            file_name = page.get_by_role("rowheader").filter(has_text=long_name[:32])
            assert file_name.is_visible()
            assert page.get_by_role("navigation", name="Folder sections").get_by_role("link").count() == 2
            header_widths = page.locator("thead th").evaluate_all(
                "headers => headers.map(header => header.getBoundingClientRect().width)"
            )
            assert min(header_widths) >= 70, header_widths
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

            page.set_viewport_size({"width": 1158, "height": 828})
            assert file_name.is_visible()
            assert page.get_by_role("link", name="Download").is_visible()
            assert page.get_by_role("group", name="File actions").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

            page.get_by_role("link", name="Details").click()
            assert page.get_by_text(long_name, exact=True).is_visible()

            page.goto(f"http://127.0.0.1:{server.server_port}/files/")
            page.set_viewport_size({"width": 375, "height": 812})
            assert page.get_by_role("table").is_visible()
            assert page.get_by_role("link", name="Download").is_visible()
            assert page.get_by_role("group", name="File actions").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        finally:
            browser.close()
    print("PASS: desktop columns, shared file actions, and mobile file-history layout")
finally:
    server.shutdown()
    thread.join()
    server.server_close()
    connections.close_all()
    temporary_directory.cleanup()
