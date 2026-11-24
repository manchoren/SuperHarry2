"""Check the provisioned Django app through its HTTP interface."""

import http.cookiejar
import json
import os
import re
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


BASE_URL = "http://127.0.0.1:8000"
FREE_ROUTE = "openrouter/google/gemma-4-26b-a4b-it:free"


def main():
    cookies = http.cookiejar.CookieJar()
    browser = build_opener(ProxyHandler({}), HTTPCookieProcessor(cookies))

    def post(path, fields):
        token = next(cookie.value for cookie in cookies if cookie.name == "csrftoken")
        payload = urlencode({**fields, "csrfmiddlewaretoken": token}).encode()
        return browser.open(Request(BASE_URL + path, data=payload), timeout=60)

    with browser.open(BASE_URL + "/login/", timeout=10):
        pass
    password = Path(os.environ["RECOVERY_PASSWORD_FILE"]).read_text(encoding="utf-8").strip()
    with post("/login/", {"username": "recovery", "password": password}) as page:
        if f"Model: {FREE_ROUTE}".encode() not in page.read():
            raise SystemExit("Expected the seeded free route before sending a live request.")
    with post("/chat/send/", {"message": "Reply only with the word OK."}) as response:
        history = response.headers.get("Location")
        fragments = []
        failure = None
        for line in response:
            event = json.loads(line)
            if event.get("error"):
                failure = event["error"]
                break
            if event.get("done"):
                break
            fragments.append(event["text"])
        else:
            raise SystemExit("Live chat ended without a completion event.")
    if not history or not history.startswith("/chat/"):
        raise SystemExit("Live chat returned no saved conversation.")
    with browser.open(BASE_URL + history, timeout=10) as page:
        saved = page.read()
    if b"Reply only with the word OK." not in saved:
        raise SystemExit("Live chat did not save the submitted message.")
    incomplete = b"Incomplete reply" in saved
    if failure and incomplete:
        print(f"Live chat pending: {failure} The saved reply is marked incomplete.")
        raise SystemExit(2)
    reply = "".join(fragments)
    if incomplete or failure or not reply.strip():
        raise SystemExit("Live chat did not save a complete reply with visible text.")
    assistant_reply = rb"<strong>Assistant</strong>\s*<p>" + re.escape(escape(reply).encode()) + rb"</p>"
    if not re.search(assistant_reply, saved):
        raise SystemExit("Saved conversation does not show the streamed assistant reply.")
    print(f"Live Django chat passed via {FREE_ROUTE} ({len(fragments)} text fragments).")


if __name__ == "__main__":
    main()
