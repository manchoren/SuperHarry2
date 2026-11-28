"""Exercise sign-in and required password-change presentation in isolation."""

# Django must be configured before importing application models.
# ruff: noqa: E402
import os
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-auth-ui-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-auth-ui-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "auth.sqlite3"),
)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections

from superharry.models import UserProfile

call_command("migrate", verbosity=0)
regular = get_user_model().objects.create_user(
    username="auth-regular",
    password="synthetic-auth-regular-password-39",
)
forced_change = get_user_model().objects.create_user(
    username="auth-change",
    password="synthetic-auth-temporary-password-39",
)
UserProfile.objects.update_or_create(user=forced_change, defaults={"must_change_password": True})

server = make_server(
    "127.0.0.1", 0, StaticFilesHandler(get_wsgi_application())
)
port = server.server_port


def serve_application():
    try:
        server.serve_forever()
    finally:
        connections.close_all()


server_thread = threading.Thread(target=serve_application, daemon=True)
server_thread.start()
base = f"http://127.0.0.1:{port}"

from playwright.sync_api import sync_playwright


def choose_theme(page, preference):
    slider = page.locator("[data-theme-select]")
    slider.evaluate(
        "(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }",
        {"light": "0", "system": "1", "dark": "2"}[preference],
    )


def assert_axe_clean(page, screen):
    page.add_script_tag(path=os.environ["UI_AXE_PATH"])
    violations = page.evaluate(
        "async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id)"
    )
    assert not violations, (screen, violations)


try:
    with sync_playwright() as playwright:
        browser_path = os.environ.get("UI_BROWSER_PATH") or playwright.chromium.executable_path
        browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(5000)
        page.emulate_media(color_scheme="light", reduced_motion="reduce")

        page.goto(base + "/login/")
        assert page.get_by_role("heading", name="Sign in").is_visible()
        assert page.locator("[data-theme-select]").is_visible()
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        assert_axe_clean(page, "login light")
        choose_theme(page, "dark")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        assert_axe_clean(page, "login dark")
        choose_theme(page, "system")
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        page.emulate_media(color_scheme="dark")
        page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
        page.emulate_media(color_scheme="light")
        page.wait_for_function("document.documentElement.dataset.theme === 'light'")
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "light"

        choose_theme(page, "dark")
        page.goto(base + "/login/?next=/settings/")
        page.get_by_label("Username:").fill("auth-regular")
        page.get_by_label("Password:").fill("synthetic-auth-regular-password-39")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/settings/")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"

        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("auth-change")
        page.get_by_label("Password:").fill("synthetic-auth-temporary-password-39")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/password/change/")
        assert page.get_by_role("heading", name="Change your password").is_visible()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        assert_axe_clean(page, "required password change dark")

        page.locator("#id_old_password").focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.matches(':focus-visible')")
        assert page.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth) >= 2")
        page.locator("#id_old_password").fill("synthetic-auth-temporary-password-39")
        page.locator("#id_new_password1").fill("Violet!Quartz7Harbor42")
        page.locator("#id_new_password2").fill("Violet!Quartz7Harbor43")
        page.get_by_role("button", name="Change password").click()
        assert page.get_by_role("alert").is_visible()

        page.goto(base + "/password/change/")
        page.locator("#id_old_password").fill("synthetic-auth-temporary-password-39")
        page.locator("#id_new_password1").fill("Violet!Quartz7Harbor42")
        page.locator("#id_new_password2").fill("Violet!Quartz7Harbor42")
        page.get_by_role("button", name="Change password").click()
        page.wait_for_url(base + "/")
        assert page.get_by_role("heading", name="New chat").is_visible()

        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("auth-change")
        page.get_by_label("Password:").fill("Violet!Quartz7Harbor42")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(base + "/")
        assert page.get_by_role("heading", name="New chat").is_visible()
        page.get_by_role("button", name="Sign out").click()
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(base + "/login/")
        page.get_by_label("Username:").fill("auth-regular")
        page.get_by_label("Password:").fill("incorrect-auth-password-39")
        page.get_by_role("button", name="Sign in").click()
        assert page.get_by_role("alert").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert_axe_clean(page, "login error mobile")
        browser.close()
    print("Authentication browser smoke passed")
finally:
    server.shutdown()
    server_thread.join(timeout=5)
    server.server_close()
    connections.close_all()
    temporary_directory.cleanup()
