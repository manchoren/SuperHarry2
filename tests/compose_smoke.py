import http.cookiejar
import os
import secrets
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def smoke_test(directory):
    project = f"superharry-smoke-{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(32)
    key_file = directory / "django_key"
    password_file = directory / "recovery_password"
    for path, value in ((key_file, secrets.token_urlsafe(48)), (password_file, password)):
        path.write_text(value, encoding="utf-8")
        # The temporary parent directory is private. The bind-mounted file
        # must be readable by the unprivileged container user.
        path.chmod(0o644)

    env = {
        **os.environ,
        "DJANGO_SECRET_KEY_PATH": str(key_file),
        "RECOVERY_PASSWORD_PATH": str(password_file),
        "SUPERHARRY_PORT": "0",
    }

    def compose(*args, capture=False):
        return subprocess.run(
            ["docker", "compose", "-p", project, *args],
            env=env,
            check=True,
            text=True,
            capture_output=capture,
        ).stdout

    def wait_for_login_page(browser, url):
        for _ in range(60):
            try:
                with browser.open(url, timeout=2) as response:
                    return response.read()
            except (ConnectionError, TimeoutError, URLError):
                time.sleep(1)
        raise RuntimeError("Django did not serve the login page.")

    def localhost_url():
        address = compose("port", "django", "8000", capture=True).strip()
        assert address.startswith("127.0.0.1:"), address
        return f"http://{address}"

    try:
        compose("up", "--build", "-d")
        base = localhost_url()
        cookies = http.cookiejar.CookieJar()
        browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies))

        def sign_in():
            login = f"{base}/admin/login/"
            wait_for_login_page(browser, login)
            token = next(cookie.value for cookie in cookies if cookie.name == "csrftoken")
            form = urlencode({
                "username": "recovery",
                "password": password,
                "csrfmiddlewaretoken": token,
                "next": "/admin/",
            }).encode()
            # Password hashing can take several seconds under CPU emulation.
            with browser.open(Request(login, data=form), timeout=30) as response:
                assert response.geturl() == f"{base}/admin/"
                assert b"Site administration" in response.read()

        sign_in()
        with browser.open(f"{base}/static/admin/css/base.css", timeout=5) as response:
            assert response.headers.get_content_type() == "text/css"
        try:
            browser.open(f"{base}/register/", timeout=5).close()
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("The public registration URL is accessible.")

        password_file.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        compose("restart", "django")
        base = localhost_url()
        assert b"Site administration" in wait_for_login_page(browser, f"{base}/admin/")
        cookies.clear()
        sign_in()
        compose("exec", "-T", "django", "python", "-m", "django", "test")
    except Exception:
        compose("logs", "--no-color")
        raise
    finally:
        compose("down", "--volumes", "--rmi", "local")


if __name__ == "__main__":
    with TemporaryDirectory(prefix="superharry-smoke-") as directory:
        smoke_test(Path(directory))
    print("Compose recovery smoke test passed.")
