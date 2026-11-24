"""Exercise an isolated, already provisioned Compose project from Django's container."""

import json
import secrets
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from superharry.gateway import GatewayFailure, available_routes, gateway_json
from superharry.models import ChatRoute, CsvFile


ALTERNATE = "openrouter/google/gemma-4-31b-it:free"
MESSAGE = "Provisioned VM smoke question"
ANSWER = "Provisioned VM smoke answer"
CSV_FILENAME = "provisioned-smoke.csv"


def regular_account():
    return get_user_model().objects.get(username="provision-smoke-regular")


def seed():
    users = get_user_model().objects
    administrator, _ = users.get_or_create(username="provision-smoke-admin", defaults={"is_staff": True})
    administrator.groups.add(Group.objects.get(name="Administrators"))
    regular, _ = users.get_or_create(username="provision-smoke-regular")
    for account, login_page in ((administrator, "/admin/login/"), (regular, "/login/")):
        password = secrets.token_urlsafe(32)
        account.set_password(password)
        account.save(update_fields=("password",))
        browser = Client(HTTP_HOST="localhost")
        assert browser.post(login_page, {
            "username": account.username, "password": password,
        }).status_code == 302
        if account == administrator:
            assert ALTERNATE in available_routes()
            assert browser.post("/admin/superharry/chatroute/", {
                "action": "select", "route_id": ALTERNATE,
            }).status_code == 302
        else:
            assert browser.get("/admin/").status_code == 302

    try:
        gateway_json("/api/providers", settings.OMNIROUTE_API_KEY)
    except GatewayFailure as failure:
        assert "(HTTP 403)" in str(failure)
    else:
        raise AssertionError("The inference key reached the management API")

    class Fixture(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["model"] == ALTERNATE
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            delta = {"choices": [{"delta": {"content": ANSWER}}]}
            self.wfile.write(b"data: " + json.dumps(delta).encode() + b"\n\ndata: [DONE]\n\n")

        def log_message(self, *args):
            pass

    with HTTPServer(("127.0.0.1", 0), Fixture) as fixture:
        thread = Thread(target=fixture.serve_forever)
        thread.start()
        try:
            with override_settings(
                OMNIROUTE_URL=f"http://127.0.0.1:{fixture.server_port}",
                OMNIROUTE_API_KEY="fixture-inference-key",
            ):
                reply = browser.post("/chat/send/", {"message": MESSAGE})
                assert reply.status_code == 200
                assert [json.loads(chunk) for chunk in reply.streaming_content] == [
                    {"text": ANSWER}, {"done": True},
                ]
        finally:
            fixture.shutdown()
            thread.join()
    print("Roles, actual gateway credential isolation, admin route selection and controlled streaming passed.")


def verify():
    assert ChatRoute.active_id() == ALTERNATE
    user = regular_account()
    conversation = user.conversation_set.get(title=MESSAGE)
    browser = Client(HTTP_HOST="localhost")
    browser.force_login(user)
    page = browser.get(f"/chat/{conversation.pk}/")
    assert page.status_code == 200 and ANSWER.encode() in page.content
    assert ALTERNATE.encode() in page.content
    print("The saved conversation, account and admin-selected route survived the restart and rerun.")


def approve_csv():
    browser = Client(HTTP_HOST="localhost")
    browser.force_login(regular_account())
    upload = browser.post("/csv/", {
        "file": SimpleUploadedFile(CSV_FILENAME, b"Name,Amount\nAlpha,4\nBeta,\n"),
    })
    assert upload.status_code == 302
    detail = upload["Location"]
    assert browser.get(detail).status_code == 200
    assert browser.post(f"{detail}approve/", {"confirm": "yes"}).status_code == 302
    print("The regular user approved the uploaded CSV.")


def verify_csv():
    user = regular_account()
    record = CsvFile.objects.filter(owner=user, name=CSV_FILENAME).latest("pk")
    browser = Client(HTTP_HOST="localhost")
    browser.force_login(user)
    report = browser.get(f"/csv/{record.pk}/download/")
    assert report.status_code == 200
    assert report.json() == {
        "rows": 2,
        "columns": [{"name": "Name", "empty": 0}, {"name": "Amount", "empty": 1}],
    }
    print("The owner downloaded the result produced by the isolated Docker worker.")


if __name__ == "__main__":
    {
        "seed": seed,
        "verify": verify,
        "approve-csv": approve_csv,
        "verify-csv": verify_csv,
    }[sys.argv[1]]()
