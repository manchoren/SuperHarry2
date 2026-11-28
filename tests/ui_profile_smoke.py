"""Exercise account settings in a real browser using isolated synthetic data."""
import os
import sys
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from wsgiref.simple_server import make_server

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
            assert page.get_by_role("heading", name="Profile").is_visible()
            assert page.get_by_role("heading", name="Change password").is_visible()
            assert page.get_by_role("heading", name="Agent").is_visible()
            assert page.get_by_label("Current repository-managed SOUL.md (read only)").get_attribute("readonly") is not None
            page.locator("#avatar").focus()
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.textContent") == "Update image"
            output = BytesIO()
            Image.new("RGB", (500, 300), "blue").save(output, "PNG")
            page.locator("#avatar").set_input_files({"name": "invalid.txt", "mimeType": "text/plain", "buffer": b"not an image"})
            page.get_by_role("button", name="Update image").click()
            assert page.get_by_role("alert").is_visible()
            page.locator("#avatar").set_input_files({"name": "image.png", "mimeType": "image/png", "buffer": output.getvalue()})
            assert page.locator("#selected-avatar-preview").is_visible()
            assert page.locator("#avatar-initial").is_hidden()
            page.get_by_role("button", name="Cancel selection").click()
            assert page.locator("#selected-avatar-preview").is_hidden()
            assert page.locator("#avatar-initial").is_visible()
            page.locator("#avatar").set_input_files({"name": "image.png", "mimeType": "image/png", "buffer": output.getvalue()})
            page.get_by_role("button", name="Update image").click()
            assert page.get_by_role("status").inner_text() == "Profile image updated."
            assert page.locator(".sidebar-footer img.user-avatar").is_visible()
            page.reload()
            assert page.locator(".sidebar-footer img.user-avatar").is_visible()
            page.locator("#avatar").set_input_files({"name": "replacement.png", "mimeType": "image/png", "buffer": output.getvalue()})
            page.get_by_role("button", name="Cancel selection").click()
            assert page.locator("#saved-avatar").is_visible()
            assert page.locator("#selected-avatar-preview").is_hidden()
            assert page.get_by_role("button", name="Remove image").is_visible()
            update_box = page.get_by_role("button", name="Update image").bounding_box()
            remove_box = page.get_by_role("button", name="Remove image").bounding_box()
            assert update_box and remove_box and remove_box["x"] > update_box["x"]
            show_passwords = page.get_by_label("Show passwords")
            password_fields = [page.locator("#id_old_password"), page.locator("#id_new_password1"), page.locator("#id_new_password2")]
            assert all(field.get_attribute("type") == "password" for field in password_fields)
            show_passwords.check()
            assert all(field.get_attribute("type") == "text" for field in password_fields)
            show_passwords.uncheck()
            assert all(field.get_attribute("type") == "password" for field in password_fields)
            theme_slider = page.locator("[data-theme-select]")
            theme_slider.evaluate("el => { el.value = '0'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
            assert page.locator("html").get_attribute("data-theme") == "light"
            theme_slider.evaluate("el => { el.value = '1'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
            assert theme_slider.get_attribute("aria-valuetext") == "System"
            theme_slider.evaluate("el => { el.value = '2'; el.dispatchEvent(new Event('input', {bubbles: true})); }")
            assert page.locator("html").get_attribute("data-theme") == "dark"
            page.set_viewport_size({"width": 375, "height": 812})
            assert page.locator("#avatar").is_visible()
            assert page.get_by_role("button", name="Update image").is_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.get_by_role("button", name="Remove image").click()
            assert page.locator(".sidebar-footer .avatar-initial").is_visible()
            password_fields[0].fill("wrong-password")
            password_fields[1].fill("new-synthetic-password-68")
            password_fields[2].fill("new-synthetic-password-68")
            page.get_by_role("button", name="Change password").click()
            assert page.get_by_role("alert").is_visible()
            password_fields[0].fill("synthetic-password-32")
            password_fields[1].fill("new-synthetic-password-68")
            password_fields[2].fill("new-synthetic-password-68")
            page.get_by_role("button", name="Change password").click()
            assert page.get_by_role("status").inner_text() == "Password updated."
            browser.close()
        print("Profile browser smoke passed")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
        connections.close_all()
