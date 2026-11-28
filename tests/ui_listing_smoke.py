"""Exercise skills and saved-memory listings in a real browser with synthetic data."""
# ruff: noqa: E402
import os
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-listing-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-listing-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "listing.sqlite3"),
)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from playwright.sync_api import sync_playwright

from superharry.models import SavedMemory, VmSkill

call_command("migrate", verbosity=0)
owner = get_user_model().objects.create_user(
    username="listing-user", password="synthetic-password-32"
)
skill = VmSkill.objects.create(
    owner=owner,
    name="listing-draft",
    purpose="Prepare a concise example report",
    instructions="Synthetic skill instructions",
)
memory_content = "Synthetic saved memory with complete detail. " * 12
SavedMemory.objects.create(owner=owner, content=memory_content)

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
        browser = playwright.chromium.launch(
            executable_path=os.environ["UI_BROWSER_PATH"], headless=True
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base + "/login/")
        page.get_by_label("Username:").fill("listing-user")
        page.get_by_label("Password:").fill("synthetic-password-32")
        page.get_by_role("button", name="Sign in").click()

        page.goto(base + "/skills/")
        assert page.get_by_role("table").is_visible()
        assert page.get_by_role("link", name="Create a skill draft").is_visible()
        assert page.get_by_role("link", name="listing-draft").is_visible()
        assert page.get_by_text("Prepare a concise example report").is_visible()
        assert page.get_by_text("Preparation required").is_visible()
        assert page.get_by_role("link", name="Edit").get_attribute("href") == "/skills/listing-draft/edit/"
        assert page.get_by_role("button", name="Delete").is_visible()
        page.get_by_role("link", name="summarize").click()
        assert page.get_by_text("Repository skill, read-only.").is_visible()
        assert page.get_by_text("supplied text in its original language").is_visible()
        page.get_by_role("link", name="All skills").click()

        page.goto(base + "/memories/")
        assert page.get_by_role("table").is_visible()
        assert page.get_by_role("link", name="Add a memory").is_visible()
        assert page.get_by_role("link", name="View memory").is_visible()
        assert memory_content not in page.locator("main").inner_text()
        page.get_by_role("link", name="View memory").click()
        assert page.get_by_text(memory_content).is_visible()
        assert page.get_by_role("link", name="Edit").is_visible()
        assert page.get_by_role("link", name="Delete").is_visible()

        page.set_viewport_size({"width": 375, "height": 812})
        for path in ("/skills/", "/memories/"):
            page.goto(base + path)
            assert page.get_by_role("table").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator("[data-theme-select]").evaluate(
                "element => { element.value = '0'; element.dispatchEvent(new Event('input', {bubbles: true})); }"
            )
            assert page.locator("html").get_attribute("data-theme") == "light"
            page.locator("[data-theme-select]").evaluate(
                "element => { element.value = '2'; element.dispatchEvent(new Event('input', {bubbles: true})); }"
            )
            assert page.locator("html").get_attribute("data-theme") == "dark"

        browser.close()
    print("Listing browser smoke passed")
finally:
    server.shutdown()
    thread.join()
    server.server_close()
    connections.close_all()
    temporary_directory.cleanup()
