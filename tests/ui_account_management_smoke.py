"""Exercise regular-account administration with synthetic browser data."""
# ruff: noqa: E402
import os
import tempfile
import threading
from pathlib import Path
from wsgiref.simple_server import make_server

temporary_directory = tempfile.TemporaryDirectory(prefix="superharry-account-admin-")
os.environ.update(
    DJANGO_SETTINGS_MODULE="superharry.settings",
    DJANGO_SECRET_KEY="synthetic-account-admin-test",
    DJANGO_DATABASE_PATH=str(Path(temporary_directory.name) / "accounts.sqlite3"),
    UI_COMPONENT_CATALOG_ENABLED="1",
)

import django

django.setup()

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application
from django.db import connections
from django.conf import settings
from playwright.sync_api import sync_playwright
from superharry.models import AccountAudit

call_command("migrate", verbosity=0)
settings.UI_COMPONENT_CATALOG_ENABLED = True
administrators, _ = Group.objects.get_or_create(name="Administrators")
manager = get_user_model().objects.create_user(
    username="browser-manager", password="synthetic-browser-manager-password", is_staff=True
)
manager.groups.add(administrators)
get_user_model().objects.create_user(
    username="browser-regular", email="regular-browser@example.test", password="synthetic-browser-regular-password"
)
get_user_model().objects.create_user(
    username="browser-inactive", password="synthetic-browser-inactive-password", is_active=False
)
delegated = get_user_model().objects.create_user(
    username="browser-delegated", password="synthetic-browser-delegated-password", is_staff=True
)
delegated.groups.add(administrators)
privileged = get_user_model().objects.create_user(
    username="hidden-privileged", password="synthetic-hidden-password", is_staff=True
)
privileged.groups.add(administrators)
superuser = get_user_model().objects.create_superuser(
    username="browser-owner", password="synthetic-browser-owner-password"
)
AccountAudit.objects.create(
    actor=manager,
    target=privileged,
    actor_id_snapshot=manager.pk,
    actor_username=manager.username,
    actor_is_privileged=True,
    target_id_snapshot=privileged.pk,
    target_username=privileged.username,
    target_is_privileged=True,
    action="created",
)

server = make_server("127.0.0.1", 0, StaticFilesHandler(get_wsgi_application()))


def serve():
    try:
        server.serve_forever()
    finally:
        connections.close_all()


thread = threading.Thread(target=serve, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}"


def choose_theme(page, mode):
    slider = page.locator("[data-theme-select]")
    if not slider.is_visible():
        page.locator(".appearance-popover summary").click()
    slider.evaluate(
        "(element, value) => { element.value = value; element.dispatchEvent(new Event('input', {bubbles: true})); }",
        {"light": "0", "system": "1", "dark": "2"}[mode],
    )


try:
    with sync_playwright() as playwright:
        browser_path = os.environ.get("UI_BROWSER_PATH") or playwright.chromium.executable_path
        browser = playwright.chromium.launch(executable_path=browser_path, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(5000)
        page.emulate_media(color_scheme="light", reduced_motion="reduce")
        page.goto(base + "/login/")
        choose_theme(page, "dark")
        page.get_by_label("Username:").fill("browser-manager")
        page.get_by_label("Password:").fill("synthetic-browser-manager-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(base + "/dev/ui-components/")
        assert page.get_by_role("heading", name="Component library").is_visible()
        assert page.get_by_text("alex@example.com").is_visible()
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches")
        page.locator(".catalog-theme label").filter(has_text="Dark").click()
        assert page.locator("body.sh").evaluate("element => getComputedStyle(element).colorScheme") == "dark"
        page.locator(".catalog-theme label").filter(has_text="Light").click()
        assert page.locator("body.sh").evaluate("element => getComputedStyle(element).colorScheme") == "light"
        page.locator(".catalog-theme label").filter(has_text="System").click()
        page.add_script_tag(path=os.environ["UI_AXE_PATH"])
        catalog_violations = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id)")
        assert not catalog_violations, ("catalog", catalog_violations)
        page.goto(base + "/accounts/")

        assert page.get_by_role("heading", name="Accounts").is_visible()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        assert page.locator(".sh-heading").get_by_role("link", name="Account audit").is_visible()
        choose_theme(page, "system")
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        page.emulate_media(color_scheme="dark")
        page.wait_for_function("document.documentElement.dataset.theme === 'dark'")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.emulate_media(color_scheme="light")
        page.wait_for_function("document.documentElement.dataset.theme === 'light'")
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        choose_theme(page, "dark")
        page.reload()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        main_navigation = page.get_by_role("navigation", name="Main navigation")
        assert main_navigation.get_by_role("link", name="Accounts").get_attribute("aria-current") == "page"
        assert page.get_by_role("link", name="Create regular account").is_visible()
        assert page.get_by_text("browser-regular").is_visible()
        assert page.get_by_text("hidden-privileged").count() == 0
        page.goto(base + "/accounts/audit/")
        assert page.get_by_text("No account events are available.").is_visible()
        page.goto(base + "/accounts/")
        page.get_by_label("Status").focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.matches(':focus-visible')")
        assert page.evaluate("parseFloat(getComputedStyle(document.activeElement).outlineWidth) >= 2")
        page.get_by_label("Username or email").fill("")
        page.get_by_label("Status").select_option("active")
        page.get_by_label("Role").select_option("regular")
        page.get_by_role("button", name="Apply filters").click()
        assert page.get_by_label("Username or email").input_value() == ""
        assert page.get_by_text("browser-regular", exact=True).count() == 1
        assert page.get_by_text("browser-inactive", exact=True).count() == 0
        page.get_by_label("Username or email").fill("no-account-matches-this")
        page.get_by_role("button", name="Apply filters").click()
        assert page.locator(".sh-empty").is_visible()
        page.goto(base + "/accounts/")
        page.get_by_label("Role").select_option("regular")
        page.get_by_label("Status").select_option("inactive")
        page.get_by_role("button", name="Apply filters").click()
        assert page.get_by_text("browser-inactive", exact=True).count() == 1
        assert page.get_by_text("browser-regular", exact=True).count() == 0
        page.get_by_label("Status").select_option("active")
        page.get_by_label("Username or email").fill("regular-browser@example.test")
        page.get_by_label("Username or email").press("Enter")
        page.wait_for_url("**/accounts/?*")
        assert page.get_by_label("Status").input_value() == "active"
        assert page.get_by_label("Role").input_value() == "regular"
        assert page.get_by_text("browser-regular").is_visible()
        assert page.get_by_text("browser-inactive").count() == 0
        assert page.get_by_text("hidden-privileged").count() == 0
        page.add_script_tag(path=os.environ["UI_AXE_PATH"])
        account_violations = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id)")
        assert not account_violations, ("accounts", account_violations)

        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("browser-owner")
        page.get_by_label("Password:").fill("synthetic-browser-owner-password")
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("link", name="Accounts").click()
        assert page.get_by_label("Role").is_visible()
        page.get_by_label("Role").select_option("administrator")
        page.get_by_role("button", name="Apply filters").click()
        assert page.get_by_text("browser-delegated").is_visible()
        assert page.get_by_text("browser-regular").count() == 0
        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("browser-manager")
        page.get_by_label("Password:").fill("synthetic-browser-manager-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(base + "/accounts/")

        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("browser-regular")
        page.get_by_label("Password:").fill("synthetic-browser-regular-password")
        page.get_by_role("button", name="Sign in").click()
        audit_denied = page.goto(base + "/accounts/audit/")
        assert audit_denied.status == 403
        accounts_denied = page.goto(base + "/accounts/")
        assert accounts_denied.status == 403

        page.goto(base + "/login/")
        page.get_by_label("Username:").fill("browser-manager")
        page.get_by_label("Password:").fill("synthetic-browser-manager-password")
        page.get_by_role("button", name="Sign in").click()
        page.goto(base + "/accounts/")

        create_account = page.get_by_role("link", name="Create regular account")
        create_account.focus()
        page.keyboard.press("Enter")
        page.wait_for_url("**/accounts/new/")
        heading = page.locator("h1")
        heading.wait_for()
        assert heading.inner_text() == "Create a regular account"
        page.get_by_label("Username", exact=True).focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.matches(':focus-visible')")
        page.get_by_label("Username", exact=True).fill("browser-regular")
        page.get_by_label("Initial password").fill("synthetic-browser-duplicate-password")
        page.get_by_role("button", name="Create account").click()
        assert page.get_by_role("alert").is_visible()
        page.get_by_label("Username", exact=True).fill("created-in-browser")
        page.get_by_label("Initial password").fill("synthetic-browser-created-account-password")
        required_change = page.get_by_label("Require a password change at first sign-in")
        assert required_change.is_checked()
        page.get_by_role("button", name="Create account").click()
        assert page.get_by_role("heading", name="Account details").is_visible()
        assert page.get_by_text("created-in-browser").is_visible()
        created_account_url = page.url
        assert page.get_by_text("synthetic-browser-created-account-password").count() == 0
        page.get_by_role("link", name="Edit account").click()
        page.get_by_label("First name").fill("Jordan")
        page.get_by_label("Email").fill("jordan@example.test")
        page.get_by_role("button", name="Save changes").click()
        assert page.get_by_text("Jordan", exact=True).is_visible()
        assert page.get_by_text("jordan@example.test").is_visible()
        page.get_by_role("link", name="Reset password").click()
        reset_required_change = page.get_by_label("Require a password change at next sign-in")
        assert reset_required_change.is_checked()
        page.locator("#id_new_password1").fill("synthetic-browser-reset-password-4718")
        page.locator("#id_new_password2").fill("synthetic-browser-reset-password-4718")
        page.get_by_role("button", name="Set password").click()
        assert page.get_by_text("Change required at next sign-in").is_visible()
        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("created-in-browser")
        page.get_by_label("Password:").fill("synthetic-browser-reset-password-4718")
        page.get_by_role("button", name="Sign in").click()
        page.get_by_role("heading", name="Change your password").wait_for()
        page.locator("#id_old_password").fill("synthetic-browser-reset-password-4718")
        page.locator("#id_new_password1").fill("synthetic-browser-changed-password-3794")
        page.locator("#id_new_password2").fill("synthetic-browser-changed-password-3794")
        page.get_by_role("button", name="Change password").click()
        page.wait_for_url(f"http://127.0.0.1:{server.server_port}/")
        assert page.get_by_role("heading", name="New chat").is_visible()

        page.get_by_role("button", name="Sign out").click()
        page.get_by_label("Username:").fill("browser-manager")
        page.get_by_label("Password:").fill("synthetic-browser-manager-password")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url(f"http://127.0.0.1:{server.server_port}/")

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(base + "/accounts/")
        assert page.get_by_role("table").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert page.get_by_role("link", name="Accounts").is_visible()
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"
        page.get_by_text("browser-regular", exact=True).first.click()
        assert page.get_by_role("heading", name="Account details").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(base + "/accounts/audit/")
        assert page.get_by_role("heading", name="Account audit").is_visible()
        main_navigation = page.get_by_role("navigation", name="Main navigation")
        assert main_navigation.get_by_role("link", name="Accounts").get_attribute("aria-current") == "page"
        assert page.get_by_role("link", name="Account audit").count() == 0
        assert page.get_by_role("link", name="Settings", exact=True).get_attribute("aria-current") is None
        assert page.get_by_role("table").is_visible()
        assert page.get_by_role("columnheader", name="Target role").is_visible()
        assert page.get_by_role("columnheader", name="Action").is_visible()
        assert page.get_by_text("hidden-privileged", exact=True).count() == 0
        choose_theme(page, "light")
        assert page.evaluate("document.documentElement.dataset.theme") == "light"
        page.add_script_tag(path=os.environ["UI_AXE_PATH"])
        audit_violations = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id)")
        assert not audit_violations, ("account audit", audit_violations)
        choose_theme(page, "dark")
        assert page.evaluate("document.documentElement.dataset.theme") == "dark"

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(base + "/accounts/audit/")
        assert page.get_by_role("heading", name="Account audit").is_visible()
        assert page.get_by_role("table").is_visible()
        assert page.get_by_text("browser-manager", exact=True).first.is_visible()
        assert page.get_by_text("created-in-browser", exact=True).first.is_visible()
        assert page.get_by_text("hidden-privileged", exact=True).count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        audit_screenshot = Path(tempfile.gettempdir()) / "superharry-account-audit-mobile.png"
        page.screenshot(path=str(audit_screenshot), full_page=True)

        page.goto(created_account_url)
        page.get_by_role("link", name="Delete account permanently").click()
        assert page.get_by_role("heading", name="Delete account permanently").is_visible()
        assert page.get_by_text("owned local skill(s)", exact=False).is_visible()
        assert page.get_by_text("other users", exact=False).is_visible()
        page.locator("#id_confirmation").focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.matches(':focus-visible')")
        page.locator("#id_confirmation").fill("incorrect-name")
        page.get_by_role("button", name="Delete account and owned data").click()
        assert page.get_by_role("alert").is_visible()
        page.locator("#id_confirmation").fill("created-in-browser")
        page.get_by_role("button", name="Delete account and owned data").click()
        page.wait_for_url(f"{base}/accounts/")
        assert page.get_by_text("created-in-browser").count() == 0
        browser.close()
    print("Account management browser smoke passed")
    print(f"Mobile account audit screenshot: {audit_screenshot}")
finally:
    server.shutdown()
    thread.join()
    server.server_close()
    connections.close_all()
    temporary_directory.cleanup()
