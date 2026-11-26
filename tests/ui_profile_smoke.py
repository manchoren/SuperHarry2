"""Exercise account settings in a real browser using isolated synthetic data."""
import os
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from wsgiref.simple_server import make_server

from PIL import Image
from playwright.sync_api import sync_playwright

with tempfile.TemporaryDirectory(prefix="superharry-profile-") as directory:
    os.environ.update(DJANGO_SETTINGS_MODULE="superharry.settings", DJANGO_SECRET_KEY="synthetic-test", DJANGO_DATABASE_PATH=str(Path(directory) / "db.sqlite3"))
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from django.db import connections

    call_command("migrate", verbosity=0)
    get_user_model().objects.create_user(username="profile-user", password="synthetic-password-32")
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
            page.get_by_label("Username:").fill("profile-user")
            page.get_by_label("Password:").fill("synthetic-password-32")
            page.get_by_role("button", name="Sign in").click()
            page.get_by_role("link", name="Profile settings").click()
            assert page.get_by_role("heading", name="Profile image").is_visible()
            output = BytesIO()
            Image.new("RGB", (500, 300), "blue").save(output, "PNG")
            page.get_by_label("PNG or JPEG image").set_input_files({"name": "image.png", "mimeType": "image/png", "buffer": output.getvalue()})
            page.get_by_role("button", name="Update image").click()
            assert page.get_by_role("status").inner_text() == "Profile image updated."
            assert page.locator(".sidebar-footer img.user-avatar").is_visible()
            page.reload()
            assert page.locator(".sidebar-footer img.user-avatar").is_visible()
            page.get_by_role("button", name="Remove image").click()
            assert page.locator(".sidebar-footer .avatar-initial").is_visible()
            page.get_by_label("Old password:").fill("synthetic-password-32")
            page.get_by_label("New password:", exact=True).fill("new-synthetic-password-68")
            page.get_by_label("New password confirmation:").fill("new-synthetic-password-68")
            page.get_by_role("button", name="Change password").click()
            assert page.get_by_role("status").inner_text() == "Password updated."
            browser.close()
        print("Profile browser smoke passed")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        connections.close_all()
