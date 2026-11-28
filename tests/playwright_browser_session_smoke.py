"""Exercise the persistent mediated page with and without an owner screenshot command."""
import base64
import json
import os
import subprocess
import time
from uuid import uuid4

from scripts.skill_sandbox import run_skill


IMAGE = os.environ.get("SKILL_BROWSER_TEST_IMAGE", "superharry-skill-browser-worker:local")
URL = "https://fixture.example/article"
LOGIN_URL = "https://fixture.example/login?oauth_code=QUERY_SENTINEL_951"
LOGIN_SENTINEL = "SYNTHETIC_LOGIN_SENTINEL_7f13"
COOKIE_SENTINELS = ("SESSION_COOKIE_SENTINEL_4a2c", "CSRF_COOKIE_SENTINEL_78bd", "DOMAIN_COOKIE_SENTINEL_1d0f")


def assert_job_removed(job_id):
    containers = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=15, check=True,
    )
    name = f"superharry-skill-{job_id.hex}"
    if name in containers.stdout.splitlines():
        raise AssertionError(f"Browser session container remains after cleanup: {name}")


def run_session(*, request_screenshot):
    events = []
    artifacts = []
    command_sent = False
    command_id = "fixture-owner-command"
    started_at = time.monotonic()

    def response(value, status=200):
        return {
            "status": status, "headers": {"content-type": "application/json; charset=utf-8"},
            "body": json.dumps(value).encode("ascii"),
        }

    def browser_handler(action):
        nonlocal command_sent
        operation = action.get("operation")
        if operation == "publish_view":
            data = base64.b64decode(action["data"], validate=True)
            if action.get("mime") != "image/jpeg" or not data.startswith(b"\xff\xd8\xff"):
                raise AssertionError("The session did not publish its owner panel image")
            events.append("view")
            return response({"saved": True})
        if operation == "publish_result":
            events.append("result")
            if "Session fixture text" not in action.get("message", ""):
                raise AssertionError("The early page result omitted fixture text")
            if action.get("citations") != [URL]:
                raise AssertionError(f"Unexpected initial citations: {action.get('citations')}")
            return response({"published": True})
        if operation == "poll_command":
            events.append("poll")
            if request_screenshot and not command_sent:
                command_sent = True
                return response({"command": "capture_screenshot", "command_id": command_id})
            return response({"command": None})
        if operation == "screenshot_result":
            image = base64.b64decode(action["data"], validate=True)
            if action.get("command_id") != command_id or not image.startswith(b"\xff\xd8\xff"):
                raise AssertionError("Screenshot capture was not bound to the pending command")
            events.append("screenshot")
            artifacts.append({"name": "browser-screenshot.jpg", "data": action["data"]})
            return response({"saved": True})

        if action.get("method") != "GET" or action.get("url") != URL:
            raise AssertionError(f"Unexpected request from the controlled page: {action}")
        body = ("<!doctype html><html><head><title>Session fixture</title></head>"
                "<body><h1>Session fixture text</h1><p>Read-only content.</p></body></html>").encode()
        return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}

    job_id = uuid4()
    result = run_skill(
        {"runtime": "playwright-browser", "target_url": URL, "browser_session": True, "browser_handoff": False},
        timeout=40, job_id=job_id, image=IMAGE, browser_handler=browser_handler,
    )
    assert_job_removed(job_id)
    if result.get("status") != "complete" or "Session fixture text" not in result.get("output", ""):
        raise AssertionError(f"Persistent session did not return the published result: {result}; elapsed={time.monotonic() - started_at:.1f}s; events={events}; artifacts={artifacts}")
    if events[:2] != ["view", "result"]:
        raise AssertionError(f"Page image/result were not published as soon as the page loaded: {events}")
    if request_screenshot:
        if events.count("screenshot") != 1 or len(artifacts) != 1:
            raise AssertionError(f"Requested screenshot did not arrive through the artifact flow: {events}, {artifacts}")
        if artifacts[0].get("name") != "browser-screenshot.jpg":
            raise AssertionError(f"Unexpected screenshot artifact: {artifacts[0].get('name')}")
    elif "screenshot" in events or artifacts:
        raise AssertionError("A no-screenshot run unexpectedly created a downloadable screenshot artifact")


def run_search_session_opens_multiple_pages_and_cites_only_successes():
    first = "https://fixture.example/first"
    redirected = "https://fixture.example/redirected-final"
    second = "https://fixture.example/second"
    blocked = "https://private.example/blocked"
    missing = "https://fixture.example/missing"
    opened_requests = []
    screenshot_command = "search-session-screenshot"
    screenshot_sent = False

    def response(value, status=200):
        return {"status": status, "headers": {"content-type": "application/json; charset=utf-8"},
                "body": json.dumps(value).encode("ascii")}

    def handler(action):
        if action.get("operation") == "publish_view":
            return response({"saved": True})
        if action.get("operation") == "publish_result":
            message = action.get("message", "")
            citations = action.get("citations")
            if citations != [redirected, second]:
                raise AssertionError(f"Search cited pages that did not open: {citations}")
            if "First destination excerpt" not in message or "Second destination excerpt" not in message:
                raise AssertionError(f"Search omitted opened destination excerpts: {message}")
            if "Blocked destination" in message or "Missing page" in message:
                raise AssertionError("Search exposed content from a blocked or non-2xx page")
            if "untrusted excerpts from opened public pages" not in message.lower():
                raise AssertionError("Search result did not identify untrusted page excerpts")
            return response({"published": True})
        if action.get("operation") == "poll_command":
            nonlocal screenshot_sent
            if not screenshot_sent:
                screenshot_sent = True
                return response({"command": "capture_screenshot", "command_id": screenshot_command})
            return response({"command": None})
        if action.get("operation") == "screenshot_result":
            if action.get("command_id") != screenshot_command:
                raise AssertionError("Search screenshot was not bound to its command")
            return response({"saved": True})
        if action.get("method") != "GET":
            raise AssertionError(f"Search used a non-GET request: {action}")
        url = action.get("url")
        opened_requests.append(url)
        if url == first:
            return {"status": 302, "headers": {"location": redirected}, "body": b""}
        if url == redirected:
            body = b"<html><head><title>First opened destination</title></head><body>First destination excerpt</body></html>"
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}
        if url == second:
            body = b"<html><head><title>Second opened destination</title></head><body>Second destination excerpt</body></html>"
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}
        if url == blocked:
            raise PermissionError("Private destination is unavailable")
        if url == missing:
            return {"status": 404, "headers": {"content-type": "text/html; charset=utf-8"},
                    "body": b"<html><body>Missing page</body></html>"}
        raise AssertionError(f"Unexpected search destination: {url}")

    job_id = uuid4()
    result = run_skill(
        {"runtime": "playwright-browser", "target_urls": [first, second, blocked, missing],
         "browser_search": True, "browser_session": True, "browser_handoff": False},
        timeout=45, job_id=job_id, image=IMAGE, browser_handler=handler,
    )
    assert_job_removed(job_id)
    if result.get("status") != "complete" or result.get("citations") != [redirected, second]:
        raise AssertionError(f"Search did not return only successful page citations: {result}")
    if first not in opened_requests or redirected not in opened_requests or second not in opened_requests:
        raise AssertionError(f"Search did not follow and open its destinations: {opened_requests}")


def run_direct_url_session_follows_relevant_links_with_get_only():
    start = "https://fixture.example/article"
    related = "https://fixture.example/privacy-policy"
    private = "https://private.example/privacy-policy"
    checkout = "https://fixture.example/checkout"
    unrelated = "https://fixture.example/events"
    requests = []

    def response(value, status=200):
        return {"status": status, "headers": {"content-type": "application/json; charset=utf-8"},
                "body": json.dumps(value).encode("ascii")}

    def handler(action):
        if action.get("operation") == "publish_view":
            return response({"saved": True})
        if action.get("operation") == "publish_result":
            message = action.get("message", "")
            if action.get("citations") != [start, related]:
                raise AssertionError(f"Direct URL browsing cited an invalid page set: {action.get('citations')}")
            if "Starting article excerpt" not in message or "Related privacy excerpt" not in message:
                raise AssertionError(f"Direct URL browsing omitted a relevant page: {message}")
            if "Private mirror text" in message or "Unrelated events text" in message:
                raise AssertionError("A denied or irrelevant destination contributed content")
            return response({"published": True})
        if action.get("operation") == "poll_command":
            return response({"command": None})
        if action.get("method") != "GET":
            raise AssertionError(f"Direct URL browsing used a non-GET request: {action}")
        url = action.get("url")
        requests.append(url)
        if url == start:
            body = ("<html><head><title>Public privacy information</title></head><body>"
                    "<h1>Privacy policy</h1><p>Starting article excerpt.</p>"
                    f"<a href='{related}'>Read privacy policy</a>"
                    f"<a href='{private}'>Privacy policy mirror</a>"
                    f"<a href='{checkout}'>Privacy checkout details</a>"
                    f"<a href='{unrelated}'>Upcoming events calendar</a></body></html>").encode()
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}
        if url == related:
            body = b"<html><head><title>Privacy policy details</title></head><body>Related privacy excerpt.</body></html>"
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": body}
        if url == private:
            raise PermissionError("Private destinations are unavailable")
        if url == unrelated:
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"},
                    "body": b"<html><body>Unrelated events text</body></html>"}
        if url == checkout:
            raise AssertionError("A payment-marked destination reached the mediator")
        raise AssertionError(f"Unexpected direct URL destination: {url}")

    job_id = uuid4()
    result = run_skill(
        {"runtime": "playwright-browser", "target_url": start, "follow_public_links": True,
         "browser_session": True, "browser_handoff": False},
        timeout=45, job_id=job_id, image=IMAGE, browser_handler=handler,
    )
    assert_job_removed(job_id)
    if result.get("status") != "complete" or result.get("citations") != [start, related]:
        raise AssertionError(f"Direct URL link traversal failed: {result}; requests={requests}")
    if start not in requests or related not in requests or private not in requests:
        raise AssertionError(f"Direct URL traversal did not try relevant candidates through mediation: {requests}")
    if checkout in requests or unrelated in requests:
        raise AssertionError(f"Direct URL traversal opened a sensitive or irrelevant destination: {requests}")


def run_owner_handoff_session():
    events = []
    commands = iter([
        {"type": "take_control", "sequence": 1},
        {"type": "type", "text": "owner@example.test", "sequence": 2},
        {"type": "key", "key": "Tab", "sequence": 3},
        {"type": "type", "text": LOGIN_SENTINEL, "sequence": 4},
        {"type": "key", "key": "Enter", "sequence": 5},
        {"type": "return_control", "sequence": 6},
    ])

    def response(value, status=200):
        return {"status": status, "headers": {"content-type": "application/json; charset=utf-8"},
                "body": json.dumps(value).encode("ascii")}

    def browser_handler(action):
        operation = action.get("operation")
        if operation == "poll_owner_control":
            try:
                command = next(commands)
                events.append(f"poll-{command['type']}")
                return response(command)
            except StopIteration:
                events.append("poll-idle")
                return response({"type": "idle"})
        if operation == "publish_owner_view":
            events.append("owner-frame")
            image = base64.b64decode(action["data"], validate=True)
            if not image.startswith(b"\xff\xd8\xff"):
                raise AssertionError("Owner frame was not a JPEG")
            return response({"published": True})
        if operation == "cleanup_owner_control":
            events.append("cleanup")
            return response({"cleared": True})
        if operation == "publish_result":
            events.append("result")
            message = action.get("message", "")
            if LOGIN_SENTINEL in message or any(value in message for value in COOKIE_SENTINELS):
                raise AssertionError("Credential or cookie sentinel reached the published result")
            if "Account access works" not in message or "[redacted]" not in message:
                raise AssertionError(f"Useful page text or redaction missing: {message}")
            if "Account access details" not in message or "Owner account detail" not in message:
                raise AssertionError(f"The authenticated continuation omitted its relevant GET page: {message}")
            if action.get("citations") != ["https://fixture.example/login", "https://fixture.example/private",
                                           "https://fixture.example/private/details"]:
                raise AssertionError(f"Authenticated continuation citations were not verified: {action.get('citations')}")
            if any("?" in url or "#" in url for url in action.get("citations", [])):
                raise AssertionError("A query or fragment reached a source citation")
            if "cleanup" not in events:
                raise AssertionError("The result was published before volatile handoff cleanup")
            return response({"published": True})

        method = action.get("method")
        url = action.get("url", "")
        if method == "GET" and url == LOGIN_URL:
            events.append("initial-get")
            page = ("<!doctype html><html><head><title>Sign-in fixture</title><style>body{margin:0}"
                    "input{position:absolute;left:20px;width:400px;height:40px}#u{top:20px}#p{top:80px}"
                    "button{position:absolute;left:20px;top:140px;width:120px;height:40px}</style></head><body>"
                    "<form action='/login' method='post'><input id='u' name='username' autofocus autocomplete='off'>"
                    "<input id='p' type='password' name='password' autocomplete='off'><button type='submit'>Sign in</button></form>"
                    "</body></html>").encode()
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": page}
        if method == "POST" and url == "https://fixture.example/login":
            body = base64.b64decode(action["body"], validate=True).decode("ascii")
            if LOGIN_SENTINEL not in body or "owner%40example.test" not in body:
                raise AssertionError("The native login form did not carry the fixture values")
            events.append("login-post")
            return {"status": 303, "headers": {
                "location": "/private", "set-cookie": [
                    f"session={COOKIE_SENTINELS[0]}; Path=/; HttpOnly; SameSite=Lax",
                    f"csrf={COOKIE_SENTINELS[1]}; Path=/; Secure; SameSite=Strict",
                    f"domain_session={COOKIE_SENTINELS[2]}; Domain=fixture.example; Path=/private; HttpOnly; SameSite=Lax",
                ],
            }, "body": b""}
        if method == "GET" and url == "https://fixture.example/private":
            cookies = action.get("headers", {}).get("cookie", "")
            if any(value not in cookies for value in COOKIE_SENTINELS):
                raise AssertionError("Set-Cookie values were not installed in the volatile BrowserContext")
            events.append("authenticated-get")
            page = ("<!doctype html><html><head><title>Account access works</title></head><body>"
                    f"<h1>Account access works</h1><p>{LOGIN_SENTINEL}</p><p>Owner can continue.</p>"
                    "<a href='/private/details'>Account access details</a>"
                    "</body></html>").encode()
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": page}
        if method == "GET" and url == "https://fixture.example/private/details":
            cookies = action.get("headers", {}).get("cookie", "")
            if any(value not in cookies for value in COOKIE_SENTINELS):
                raise AssertionError("The authenticated continuation did not retain the scoped BrowserContext cookies")
            events.append("authenticated-related-get")
            page = ("<!doctype html><html><head><title>Account access details</title></head><body>"
                    "<h1>Account access details</h1><p>Owner account detail.</p></body></html>").encode()
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"}, "body": page}
        raise AssertionError(f"Unexpected handoff request: {method} {url}")

    job_id = uuid4()
    result = run_skill(
        {"runtime": "playwright-browser", "target_url": LOGIN_URL,
         "browser_session": True, "browser_handoff": True, "follow_authenticated_links": True},
        timeout=50, job_id=job_id, image=IMAGE, browser_handler=browser_handler,
    )
    assert_job_removed(job_id)
    if result.get("status") != "complete" or LOGIN_SENTINEL in result.get("output", ""):
        raise AssertionError(f"Owner handoff result was incomplete or leaked a typed value: {result}; events={events}")
    if not {"login-post", "authenticated-get", "authenticated-related-get"}.issubset(events):
        raise AssertionError(f"The authenticated browser continuation did not complete: {events}")
    if "owner-frame" not in events or events.index("owner-frame") > events.index("cleanup"):
        raise AssertionError("Owner frames were not limited to the live RAM handoff")


def run_owner_handoff_rejects_http_initial_url():
    calls = []
    job_id = uuid4()
    result = run_skill(
        {"runtime": "playwright-browser", "target_url": "http://fixture.example/login",
         "browser_session": True, "browser_handoff": True},
        timeout=20, job_id=job_id, image=IMAGE,
        browser_handler=lambda action: calls.append(action) or {"status": 200, "headers": {}, "body": b""},
    )
    assert_job_removed(job_id)
    if result.get("status") == "complete" or calls:
        raise AssertionError("An insecure initial owner-handoff URL reached browser mediation")


def run_owner_handoff_rejects_http_redirect():
    events = []
    commands = iter([
        {"type": "take_control", "sequence": 1},
        {"type": "type", "text": "owner@example.test", "sequence": 2},
        {"type": "key", "key": "Tab", "sequence": 3},
        {"type": "type", "text": LOGIN_SENTINEL, "sequence": 4},
        {"type": "key", "key": "Enter", "sequence": 5},
        {"type": "return_control", "sequence": 6},
    ])

    def reply(value):
        return {"status": 200, "headers": {"content-type": "application/json; charset=utf-8"},
                "body": json.dumps(value).encode("ascii")}

    def handler(action):
        operation = action.get("operation")
        if operation == "poll_owner_control":
            try:
                command = next(commands)
                events.append(f"poll-{command['type']}")
                return reply(command)
            except StopIteration:
                return reply({"type": "idle"})
        if operation == "publish_owner_view":
            events.append("owner-frame")
            return reply({"published": True})
        if operation == "cleanup_owner_control":
            events.append("cleanup")
            return reply({"cleared": True})
        if operation == "publish_result":
            events.append("result")
            if LOGIN_SENTINEL in action.get("message", ""):
                raise AssertionError("The typed sentinel reached a rejected-redirect result")
            return reply({"published": True})
        if action.get("method") == "GET" and action.get("url") == LOGIN_URL:
            return {"status": 200, "headers": {"content-type": "text/html; charset=utf-8"},
                    "body": (b"<form action='/login' method='post'><input name='username' autofocus>"
                             b"<input name='password' type='password'><button>Sign in</button></form>")}
        if action.get("method") == "POST" and action.get("url") == "https://fixture.example/login":
            events.append("login-post")
            return {"status": 303, "headers": {"location": "http://fixture.example/private"}, "body": b""}
        if action.get("url") == "http://fixture.example/private":
            events.append("insecure-redirect-request")
            return {"status": 200, "headers": {"content-type": "text/html"}, "body": b"unsafe"}
        raise AssertionError("Unexpected owner-handoff fixture request")

    job_id = uuid4()
    result = run_skill(
        {"runtime": "playwright-browser", "target_url": LOGIN_URL,
         "browser_session": True, "browser_handoff": True},
        timeout=40, job_id=job_id, image=IMAGE, browser_handler=handler,
    )
    assert_job_removed(job_id)
    if "login-post" not in events or "insecure-redirect-request" in events:
        raise AssertionError(f"HTTP redirect escaped owner-handoff checks: {events}; result={result}")
    if events.index("login-post") > events.index("cleanup") or "cleanup" not in events:
        raise AssertionError(f"Rejected redirect did not clean up after the form response: {events}")


def main():
    run_session(request_screenshot=True)
    run_search_session_opens_multiple_pages_and_cites_only_successes()
    run_direct_url_session_follows_relevant_links_with_get_only()
    run_owner_handoff_session()
    run_owner_handoff_rejects_http_initial_url()
    run_owner_handoff_rejects_http_redirect()
    print("PASS: owner handoff installs cookies only in BrowserContext, redacts typed sentinels, and cleans up before publishing")


if __name__ == "__main__":
    main()
