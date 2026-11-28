"""Browser-check private memory and legacy CSV result screens with synthetic data."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix="superharry-memories-csv-") as directory:
    root = Path(directory)
    spool = root / "spool"
    spool.mkdir()
    os.environ.update(
        DJANGO_SETTINGS_MODULE="superharry.settings",
        DJANGO_SECRET_KEY="synthetic-test",
        DJANGO_DATABASE_PATH=str(root / "db.sqlite3"),
        CSV_SPOOL=str(spool),
    )
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connections
    from superharry.models import CsvFile, CsvSummary, SavedMemory

    call_command("migrate", verbosity=0)
    user = get_user_model().objects.create_user(username="pages-user", password="synthetic-password-48")
    other = get_user_model().objects.create_user(username="other-pages-user", password="synthetic-password-48")
    empty_user = get_user_model().objects.create_user(username="empty-pages-user", password="synthetic-password-48")
    memory = SavedMemory.objects.create(owner=user, content="Synthetic private memory with a long enough sample.")
    csv_file = CsvFile.objects.create(owner=user, name="synthetic-results.csv", payload=b"amount\n5\n")
    job = CsvSummary.objects.create(file=csv_file)
    (spool / f"{job.job_id}.result").write_text(json.dumps({
        "status": "complete", "summary": {"rows": 1, "columns": [{"name": "Amount", "empty": 0}]},
    }), encoding="utf-8")
    failed_file = CsvFile.objects.create(owner=user, name="synthetic-failure.csv", payload=b"broken")
    failed_job = CsvSummary.objects.create(file=failed_file)
    (spool / f"{failed_job.job_id}.result").write_text(json.dumps({"status": "failed"}), encoding="utf-8")
    owned_record_id = csv_file.pk
    owned_memory_id = memory.pk
    other_memory = SavedMemory.objects.create(owner=other, content="Other user private memory.")
    other_memory_id = other_memory.pk

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
            page = browser.new_page()
            page.goto(base + "/login/")
            page.get_by_label("Username:").fill("pages-user")
            page.get_by_label("Password:").fill("synthetic-password-48")
            page.get_by_role("button", name="Sign in").click()

            page.goto(base + "/memories/")
            assert page.get_by_role("heading", name="Saved memories").is_visible()
            assert page.get_by_text("Synthetic private memory", exact=False).is_visible()
            page.goto(base + f"/memories/{owned_memory_id}/")
            assert page.get_by_text("Synthetic private memory", exact=False).is_visible()
            page.goto(base + "/memories/new/")
            page.get_by_label("Content").fill("Synthetic memory created in the browser.")
            page.get_by_role("button", name="Save memory").click()
            assert page.get_by_text("Synthetic memory created in the browser.").is_visible()
            created_memory_link = page.locator("tr").filter(has_text="Synthetic memory created in the browser.").get_by_role("link", name="View memory")
            created_memory_id = created_memory_link.get_attribute("href").rstrip("/").split("/")[-1]
            page.goto(base + f"/memories/{created_memory_id}/edit/")
            page.get_by_label("Content").fill("Synthetic memory edited in the browser.")
            page.get_by_role("button", name="Save memory").click()
            assert page.get_by_text("Synthetic memory edited in the browser.").is_visible()
            page.goto(base + f"/memories/{created_memory_id}/delete/")
            page.get_by_role("button", name="Delete memory").click()
            assert page.get_by_role("heading", name="Saved memories").is_visible()
            assert page.get_by_text("Synthetic memory edited in the browser.").count() == 0
            other_context = browser.new_context()
            other_page = other_context.new_page()
            other_page.goto(base + "/login/")
            other_page.get_by_label("Username:").fill("other-pages-user")
            other_page.get_by_label("Password:").fill("synthetic-password-48")
            other_page.get_by_role("button", name="Sign in").click()
            assert other_page.goto(base + f"/memories/{owned_memory_id}/").status == 404
            assert other_page.goto(base + f"/csv/{owned_record_id}/").status == 404
            other_context.close()

            empty_context = browser.new_context()
            empty_page = empty_context.new_page()
            empty_page.goto(base + "/login/")
            empty_page.get_by_label("Username:").fill("empty-pages-user")
            empty_page.get_by_label("Password:").fill("synthetic-password-48")
            empty_page.get_by_role("button", name="Sign in").click()
            empty_page.goto(base + "/memories/")
            assert empty_page.get_by_text("No saved memories yet").is_visible()
            empty_page.goto(base + "/csv/")
            assert empty_page.get_by_text("No previous CSV results").is_visible()
            empty_context.close()

            page.goto(base + "/csv/")
            assert page.get_by_role("heading", name="Previous CSV results").is_visible()
            assert page.get_by_text("synthetic-results.csv").is_visible()
            page.goto(base + f"/csv/{owned_record_id}/")
            assert page.get_by_role("status").inner_text().find("Summary complete") >= 0
            assert page.get_by_text("Amount (0 empty cells)").is_visible()
            with page.expect_download() as download_info:
                page.get_by_role("link", name="Download summary").click()
            assert download_info.value.suggested_filename == "csv-summary.json"
            page.goto(base + f"/csv/{failed_file.pk}/")
            assert page.get_by_role("status").inner_text() == "The CSV could not be summarized."

            page.set_viewport_size({"width": 375, "height": 812})
            for theme in ("light", "dark"):
                page.goto(base + "/memories/")
                page.locator("[data-theme-select]").evaluate("(el, value) => { el.value = value; el.dispatchEvent(new Event('input', {bubbles:true})); }", "0" if theme == "light" else "2")
                assert page.locator("html").get_attribute("data-theme") == theme
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.goto(base + f"/csv/{owned_record_id}/")
                assert page.get_by_role("link", name="Download summary").is_visible()

            page.get_by_role("link", name="Profile settings").click()
            assert page.get_by_label("Current repository-managed SOUL.md (read only)").get_attribute("readonly") is not None
            browser.close()
        print("Memories and CSV browser smoke passed")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        connections.close_all()
