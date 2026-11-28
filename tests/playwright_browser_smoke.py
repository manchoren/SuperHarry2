"""Exercise the pinned Chromium worker with synthetic mediated web resources."""
import os
import json
import subprocess
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4

from scripts.skill_sandbox import run_skill
from scripts.skill_network import NetworkMediator


class FixtureMediator:
    def __init__(self):
        self.urls = []

    def retrieve_browser(self, action, *, authorized=None, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise PermissionError("Execution cancelled")
        if authorized is not None and not authorized():
            raise PermissionError("Consent expired")
        self.urls.append(action["url"])
        path = urlsplit(action["url"]).path
        if path == "/start":
            return {"status": 302, "headers": {"location": "/redirect/step/start.html"}, "body": b""}
        if path == "/redirect/step/start.html":
            return {"status": 301, "headers": {"Location": "../../content/article/index.html"}, "body": b""}
        if path == "/content/article/index.html":
            body = (b"<!doctype html><html><head><title>Controlled page</title>"
                    b"<link rel=stylesheet href='../styles/main.css'></head>"
                    b"<body><h1>Public fixture</h1><p>Read-only mediated content.</p>"
                    b"<a href='../related.html'>More public details</a>"
                    b"<img src='../../assets/pixel.png'>"
                    b"<img src='http://127.0.0.1/private.png'></body></html>")
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}
        if path == "/content/related.html":
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"},
                    "body": b"<html><head><title>More public details</title></head><body>Related fixture content.</body></html>"}
        if path == "/content/styles/main.css":
            return {"status": 200, "headers": {"content-type": "text/css"},
                    "body": b"body { color: black; background-image: url(assets/pixel.png); }"}
        if path == "/assets/pixel.png" or path == "/content/styles/assets/pixel.png":
            return {"status": 200, "headers": {"content-type": "image/png"}, "body": b"not-a-real-image"}
        raise ValueError("Unexpected fixture URL")


def verify_direct_egress_is_blocked(image):
    probe = subprocess.run([
        "docker", "run", "--rm", "--pull=never", "--network=none", "--ipc=none",
        "--cpus=1.0", "--memory=512m", "--memory-swap=512m", "--pids-limit=128",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=10001:10001", "--log-driver=none", "--entrypoint", "node", image,
        "-e", "fetch('https://example.com/').then(()=>process.exit(2),()=>process.exit(0))",
    ], capture_output=True, timeout=30, check=False)
    if probe.returncode != 0:
        raise AssertionError("A direct public HTTP request escaped the isolated browser container")


def verify_browser_url_policy(image):
    cases = (
        ("https://fixture.example/article", "Network mediation is unavailable"),
        ("https://fixture.example/search?q=50%25", "Network mediation is unavailable"),
        ("https://fixture.example/delete", "A valid public HTTP(S) URL is required"),
        ("https://fixture.example/%64elete", "A valid public HTTP(S) URL is required"),
        ("https://fixture.example/%2564elete", "A valid public HTTP(S) URL is required"),
        ("https://fixture.example/action%ZZ", "A valid public HTTP(S) URL is required"),
        ("https://fixture.example/?%61ction=update", "A valid public HTTP(S) URL is required"),
        ("https://fixture.example/?act%2569on=%2564elete", "A valid public HTTP(S) URL is required"),
        ("https://fixture.example/?action=%ZZ", "A valid public HTTP(S) URL is required"),
    )
    command = [
        "docker", "run", "--rm", "--pull=never", "--network=none", "--ipc=none",
        "--cpus=1.0", "--memory=512m", "--memory-swap=512m", "--pids-limit=128",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=10001:10001", "--log-driver=none", "--interactive", "--entrypoint=node", image,
        "/opt/playwright-skill/main.mjs",
    ]
    for target_url, expected in cases:
        probe = subprocess.run(
            command,
            input=json.dumps({"runtime": "playwright-browser", "target_url": target_url,
                              "execution_timeout": 120}).encode("ascii") + b"\n",
            capture_output=True, timeout=30, check=False,
        )
        error = probe.stderr.decode("utf-8", errors="replace")
        if probe.returncode == 0 or expected not in error:
            raise AssertionError(f"Browser URL policy mismatch for {target_url}: {error}")


def verify_execution_timeout_validation(image):
    command = [
        "docker", "run", "--rm", "--pull=never", "--network=none", "--ipc=none",
        "--cpus=1.0", "--memory=512m", "--memory-swap=512m", "--pids-limit=128",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=10001:10001", "--log-driver=none", "--interactive", "--entrypoint=node", image,
        "/opt/playwright-skill/main.mjs",
    ]
    target = "https://fixture.example/article"
    for timeout in (0.1, 120, 150):
        probe = subprocess.run(
            command,
            input=json.dumps({"runtime": "playwright-browser", "target_url": target,
                              "execution_timeout": timeout}).encode("ascii") + b"\n",
            capture_output=True, timeout=30, check=False,
        )
        error = probe.stderr.decode("utf-8", errors="replace")
        if probe.returncode == 0 or "Network mediation is unavailable" not in error:
            raise AssertionError(f"A valid execution timeout was rejected: {timeout}: {error}")
    for timeout in (0, -1, 150.1, True, "120", None):
        probe = subprocess.run(
            command,
            input=json.dumps({"runtime": "playwright-browser", "target_url": target,
                              "execution_timeout": timeout}).encode("ascii") + b"\n",
            capture_output=True, timeout=30, check=False,
        )
        error = probe.stderr.decode("utf-8", errors="replace")
        if probe.returncode == 0 or "A valid public HTTP(S) URL is required" not in error:
            raise AssertionError(f"An invalid execution timeout was accepted: {timeout}: {error}")


def assert_job_removed(job_id):
    name = f"superharry-skill-{job_id.hex}"
    containers = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                                capture_output=True, text=True, timeout=15, check=True)
    if name in containers.stdout.splitlines():
        raise AssertionError(f"Browser job container remains after completion: {name}")


def run_job(image, request, *, handler, timeout=120, cancelled=None):
    job_id = uuid4()
    result = run_skill(request, timeout=timeout, job_id=job_id, image=image,
                       browser_handler=handler, cancelled=cancelled)
    assert_job_removed(job_id)
    return result


def main():
    image = os.environ.get("SKILL_BROWSER_TEST_IMAGE", "superharry-skill-browser-worker:local")
    verify_direct_egress_is_blocked(image)
    verify_browser_url_policy(image)
    verify_execution_timeout_validation(image)

    mediator = FixtureMediator()
    private_guard = NetworkMediator(set())
    private_attempts = []

    def controlled_handler(action, *, authorized=None):
        if action["url"] == "http://127.0.0.1/private.png":
            private_attempts.append(action["url"])
            return private_guard.retrieve_browser(action, authorized=authorized)
        return mediator.retrieve_browser(action, authorized=authorized)

    request = {"runtime": "playwright-browser", "target_url": "https://fixture.example/start"}
    result = run_job(image, request, handler=controlled_handler)
    if result.get("status") != "complete":
        raise AssertionError(f"Mediated browser failed: {result}")
    if "Public fixture" not in result.get("output", "") or "Read-only mediated content." not in result["output"]:
        raise AssertionError("The browser did not return the controlled page text")
    if result.get("citations") != ["https://fixture.example/content/article/index.html"]:
        raise AssertionError(f"Only the successfully opened final main page should be cited: {result.get('citations')}")
    if mediator.urls != [
        "https://fixture.example/start", "https://fixture.example/redirect/step/start.html",
        "https://fixture.example/content/article/index.html", "https://fixture.example/content/styles/main.css",
        "https://fixture.example/assets/pixel.png", "https://fixture.example/content/styles/assets/pixel.png",
    ]:
        raise AssertionError(f"The browser did not mediate each navigation/resource: {mediator.urls}")
    if private_attempts != ["http://127.0.0.1/private.png"]:
        raise AssertionError("The private-address subresource did not reach the denying mediator")

    for destination, leak_marker in (("http://127.0.0.1/secret", ""),
                                     ("file:///etc/passwd", "root:"),
                                     ("/account/delete", "")):
        class RedirectMediator:
            def retrieve_browser(self, action, *, authorized=None):
                if action["url"] == "https://fixture.example/start":
                    return {"status": 302, "headers": {"location": destination}, "body": b""}
                return private_guard.retrieve_browser(action, authorized=authorized)

        blocked_result = run_job(image, request, handler=RedirectMediator().retrieve_browser)
        if blocked_result.get("status") == "complete" or blocked_result.get("citations"):
            raise AssertionError(f"Unsafe redirect was not blocked: {destination}")
        if leak_marker and leak_marker in blocked_result.get("output", ""):
            raise AssertionError("A non-HTTP redirect exposed local file content")

    class LoopMediator:
        def __init__(self):
            self.urls = []

        def retrieve_browser(self, action, *, authorized=None):
            self.urls.append(action["url"])
            destination = ("https://fixture.example/loop" if urlsplit(action["url"]).path == "/start"
                           else "https://fixture.example/start")
            return {"status": 302, "headers": {"location": destination}, "body": b""}

    loop = LoopMediator()
    loop_result = run_job(image, request, handler=loop.retrieve_browser)
    if loop_result.get("status") == "complete" or loop_result.get("citations"):
        raise AssertionError("A redirect loop was accepted")
    if loop.urls != ["https://fixture.example/start", "https://fixture.example/loop"]:
        raise AssertionError(f"Redirect loop was not stopped before revisiting a URL: {loop.urls}")

    class LongRedirectMediator:
        def __init__(self):
            self.urls = []

        def retrieve_browser(self, action, *, authorized=None):
            self.urls.append(action["url"])
            index = int(urlsplit(action["url"]).path.rsplit("/", 1)[-1])
            return {"status": 302, "headers": {"location": f"https://fixture.example/hop/{index + 1}"}, "body": b""}

    too_many = LongRedirectMediator()
    long_request = {"runtime": "playwright-browser", "target_url": "https://fixture.example/hop/0"}
    long_result = run_job(image, long_request, handler=too_many.retrieve_browser)
    if long_result.get("status") == "complete" or long_result.get("citations"):
        raise AssertionError("An overlong redirect chain was accepted")
    if len(too_many.urls) != 11 or too_many.urls[-1].endswith("/11"):
        raise AssertionError(f"Redirect limit did not stop before hop 11: {too_many.urls}")

    def slow_handler(action, *, authorized=None):
        time.sleep(4)
        return mediator.retrieve_browser(action, authorized=authorized)

    timeout_result = run_job(image, request, handler=slow_handler, timeout=5)
    if timeout_result.get("status") != "timeout":
        raise AssertionError(f"Slow mediated request did not time out: {timeout_result}")

    started = threading.Event()
    release = threading.Event()

    def held_handler(action, *, authorized=None):
        started.set()
        release.wait(10)
        return mediator.retrieve_browser(action, authorized=authorized)

    cancelled = threading.Event()
    cancel_result = {}
    cancel_job_id = uuid4()

    def run_cancelled_job():
        cancel_result.update(run_skill(request, timeout=120, job_id=cancel_job_id, image=image,
                                       browser_handler=held_handler, cancelled=cancelled))

    worker = threading.Thread(target=run_cancelled_job)
    worker.start()
    if not started.wait(30):
        cancelled.set()
        release.set()
        worker.join(30)
        raise AssertionError("Browser cancellation fixture never reached the mediator")
    cancelled.set()
    release.set()
    worker.join(30)
    if worker.is_alive():
        raise AssertionError("Cancelled browser worker did not stop")
    assert_job_removed(cancel_job_id)
    if cancel_result.get("reason") not in ("interrupted", "cleanup_unverified"):
        raise AssertionError(f"Cancelled browser job returned an unexpected state: {cancel_result}")

    live_url = os.environ.get("SKILL_BROWSER_LIVE_URL")
    if live_url:
        public_result = run_skill({"runtime": "playwright-browser", "target_url": live_url},
                                  timeout=120, image=image,
                                  browser_handler=NetworkMediator(set()).retrieve_browser)
        if public_result.get("status") != "complete" or live_url not in public_result.get("citations", []):
            raise AssertionError(f"Public URL was not read and cited through the mediator: {public_result}")
        print(f"Mediated public URL smoke passed: {live_url}")

    print("Playwright browser Docker smoke passed")
    print("Direct egress blocked; every fixture page/resource was mediated; redirect citations and private-hop checks passed.")


if __name__ == "__main__":
    main()
