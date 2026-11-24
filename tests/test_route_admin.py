import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase


class RouteAdminTests(TestCase):
    admin_url = "/admin/superharry/chatroute/"

    def setUp(self):
        self.administrator = get_user_model().objects.create_user(username="route-admin", is_staff=True)
        self.administrator.groups.add(Group.objects.get(name="Administrators"))
        self.client.force_login(self.administrator)
        self.requests = []
        requests = self.requests

        class Gateway(BaseHTTPRequestHandler):
            def do_GET(self):
                authorization = self.headers.get("Authorization")
                requests.append((self.command, self.path, authorization, None))
                if self.path == "/v1/models" and authorization == "Bearer inference-test-key":
                    self.send_json(200, {"data": [{"id": "nous/original:free"}, {"id": "nous/other:free"}]})
                elif self.path == "/api/providers" and authorization == "Bearer management-test-key":
                    self.send_json(200, {"connections": [
                        {"id": "connection-1", "provider": "nous", "name": "Existing Nous",
                         "apiKey": "upstream-private-value"}
                    ]})
                else:
                    self.send_json(403, {"error": "private gateway diagnostic"})

            def do_POST(self):
                authorization = self.headers.get("Authorization")
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.command, self.path, authorization, payload))
                if self.path == "/v1/chat/completions" and authorization == "Bearer inference-test-key":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(
                        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                        b"data: [DONE]\n\n"
                    )
                elif self.path == "/api/provider-models" and authorization == "Bearer management-test-key":
                    self.send_json(200, {"id": payload["modelId"]})
                else:
                    self.send_json(403, {"error": "private gateway diagnostic"})

            def do_PUT(self):
                self.do_POST()

            def send_json(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Gateway)
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.gateway_url = f"http://127.0.0.1:{server.server_port}"
        self.enterContext(self.settings(
            OMNIROUTE_URL=self.gateway_url,
            OMNIROUTE_API_KEY="inference-test-key",
            OMNIROUTE_MANAGEMENT_TOKEN="management-test-key",
            CHAT_MODEL="nous/original:free",
        ))

    def test_administrator_can_list_routes_and_existing_connections(self):
        self.assertContains(self.client.get("/admin/"), self.admin_url)
        page = self.client.get(self.admin_url)
        self.assertContains(page, "nous/original:free")
        self.assertContains(page, "nous/other:free")
        self.assertContains(page, "Existing Nous")
        for secret in ("inference-test-key", "management-test-key", "upstream-private-value"):
            self.assertNotContains(page, secret)

    def test_selected_default_is_used_by_chat(self):
        response = self.client.post(
            self.admin_url, {"action": "select", "route_id": "nous/other:free"}
        )
        self.assertRedirects(response, self.admin_url)
        regular = Client()
        regular.force_login(get_user_model().objects.create_user(username="route-chat-user"))
        self.assertContains(regular.get("/"), "Model: nous/other:free")
        reply = regular.post("/chat/send/", {"message": "Use the new default"})
        self.assertEqual([json.loads(chunk) for chunk in reply.streaming_content][-1], {"done": True})
        self.assertEqual(self.requests[-1][2], "Bearer inference-test-key")
        self.assertEqual(self.requests[-1][3]["model"], "nous/other:free")

    def test_administrator_can_probe_a_route_without_switching_default(self):
        result = self.client.post(
            self.admin_url, {"action": "test", "route_id": "nous/other:free"}, follow=True
        )
        self.assertContains(result, "Route test succeeded")
        self.assertContains(result, "<p>Default route: <strong>nous/original:free</strong></p>", html=True)
        probes = [
            (key, body) for method, path, key, body in self.requests
            if method == "POST" and path == "/v1/chat/completions"
        ]
        self.assertEqual(probes, [(
            "Bearer inference-test-key",
            {
                "model": "nous/other:free",
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "stream": True,
                "max_tokens": 8,
            },
        )])

    def test_administrator_can_add_and_update_a_model_on_an_existing_connection(self):
        for action, method in (("add-model", "POST"), ("update-model", "PUT")):
            with self.subTest(action=action):
                result = self.client.post(self.admin_url, {
                    "action": action,
                    "connection": "connection-1",
                    "model_id": "upstage/solar-mini4:free",
                    "model_name": "Solar Mini",
                }, follow=True)
                self.assertContains(result, "The model entry was saved.")
                mutations = [item for item in self.requests if item[1] == "/api/provider-models"]
                self.assertEqual(mutations[-1], (
                    method,
                    "/api/provider-models",
                    "Bearer management-test-key",
                    {"provider": "nous", "modelId": "upstage/solar-mini4:free", "modelName": "Solar Mini"},
                ))
        self.assertEqual(sum(item[1] == "/api/provider-models" for item in self.requests), 2)

    def test_route_mutation_requires_admin_change_permission_and_csrf(self):
        viewer = get_user_model().objects.create_user(username="route-viewer", is_staff=True)
        viewer.user_permissions.add(Permission.objects.get(codename="view_chatroute"))
        browser = Client()
        browser.force_login(viewer)
        listing = browser.get(self.admin_url)
        self.assertContains(listing, "nous/other:free")
        self.assertNotContains(listing, "Set as default")
        self.requests.clear()
        change = {"action": "select", "route_id": "nous/other:free"}
        self.assertEqual(browser.post(self.admin_url, change).status_code, 403)
        regular = get_user_model().objects.create_user(username="regular-user")
        browser.force_login(regular)
        self.assertRedirects(browser.post(self.admin_url, change), f"/admin/login/?next={self.admin_url}")
        browser = Client(enforce_csrf_checks=True)
        browser.force_login(self.administrator)
        self.assertEqual(browser.post(self.admin_url, change).status_code, 403)
        self.assertEqual(self.requests, [])

    def test_inference_key_is_denied_for_management(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(
                f"{self.gateway_url}/api/providers",
                headers={"Authorization": "Bearer inference-test-key"},
            ), timeout=3)
        self.assertEqual(raised.exception.code, 403)
        raised.exception.close()

        self.requests.clear()
        with self.settings(OMNIROUTE_MANAGEMENT_TOKEN="inference-test-key"):
            page = self.client.get(self.admin_url)
        self.assertContains(page, "Configure a separate gateway management credential.")
        self.assertNotContains(page, "inference-test-key")
        self.assertEqual([request[1] for request in self.requests], ["/v1/models"])

    def test_unknown_connections_cannot_mutate_the_gateway(self):
        page = self.client.post(self.admin_url, {
            "action": "add-model",
            "connection": "unregistered-connection",
            "model_id": "example",
        }, follow=True)
        self.assertContains(page, "Select an existing connection")
        self.assertTrue(all(request[0] == "GET" for request in self.requests))
