"""Behavioral tests for mediated skill network requests."""
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from unittest.mock import patch
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Destination(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"public information")

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.requests.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"accepted")


class MediatedNetworkTests(unittest.TestCase):
    def test_zero_port_is_rejected_instead_of_sending_to_a_default_port(self):
        from scripts.skill_network import NetworkMediator

        with self.assertRaises(ValueError):
            NetworkMediator(set(), allow_private=True).dispatch_approved({
                "method": "GET", "url": "http://127.0.0.1:0/", "body": "",
            })

    def test_approved_request_is_not_sent_after_execution_is_cancelled_during_dns(self):
        from scripts.skill_network import NetworkMediator

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        entered, release, cancelled = threading.Event(), threading.Event(), threading.Event()
        resolver = socket.getaddrinfo

        def slow_resolution(*args, **kwargs):
            entered.set()
            release.wait(3)
            return resolver(*args, **kwargs)

        errors = []
        def send():
            try:
                NetworkMediator(set(), allow_private=True).dispatch_approved({
                    "method": "POST", "url": f"http://127.0.0.1:{server.server_port}/send", "body": "private",
                }, cancelled=cancelled)
            except Exception as error:
                errors.append(error)

        with patch("socket.getaddrinfo", side_effect=slow_resolution):
            thread = threading.Thread(target=send)
            thread.start()
            self.assertTrue(entered.wait(2))
            cancelled.set()
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PermissionError)
        self.assertEqual(server.requests, [])

    def test_request_framing_and_injected_headers_are_rejected(self):
        from scripts.skill_network import NetworkMediator

        mediator = NetworkMediator(set())
        for headers in ({"Host": "other.example"}, {"Transfer-Encoding": "chunked"},
                        {"X-Data": "ok\r\nInjected: yes"}, {"X-Data": "a", "x-data": "b"}):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                mediator.dispatch_approved({
                    "method": "POST", "url": "https://example.invalid/", "body": "data", "headers": headers,
                })

    def test_approved_action_can_include_explicit_headers(self):
        from scripts.skill_network import NetworkMediator

        received = []

        class DestinationWithHeaders(Destination):
            def do_POST(self):
                received.append((self.headers.get("Content-Type"), self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"accepted")

        server = ThreadingHTTPServer(("127.0.0.1", 0), DestinationWithHeaders)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        result = NetworkMediator(set(), allow_private=True).dispatch_approved({
            "method": "POST", "url": f"http://127.0.0.1:{server.server_port}/submit",
            "body": '{"value":1}', "headers": {"Content-Type": "application/json"},
        })
        self.assertEqual(result, b"accepted")
        self.assertEqual(received, [("application/json", b'{"value":1}')])

    def test_registered_public_resource_cannot_redirect_to_a_second_destination(self):
        from scripts.skill_network import NetworkMediator

        received = []

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                if self.path == "/public":
                    self.send_response(302)
                    self.send_header("Location", "/other")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/public"
        with self.assertRaises(OSError):
            NetworkMediator({url}, allow_private=True).retrieve_public(url)
        self.assertEqual(received, ["/public"])

    def test_get_with_data_is_not_sent_without_an_approval(self):
        from scripts.skill_network import NetworkMediator, mediate_action

        received = []

        class Recorder(Destination):
            def do_GET(self):
                received.append(self.path)
                super().do_GET()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with TemporaryDirectory() as directory:
            action = {"method": "GET", "url": f"http://127.0.0.1:{server.server_port}/?secret=private", "body": ""}
            with self.assertRaises(TimeoutError):
                mediate_action(action, Path(directory), uuid4(), NetworkMediator(set(), allow_private=True), timeout=0.2)
        self.assertEqual(received, [])

    def test_package_download_rejects_queries_and_non_registry_destinations(self):
        from scripts.skill_network import NetworkMediator

        mediator = NetworkMediator(public_resources=set())
        for url in ("https://pypi.org/simple/example/?secret=data", "https://example.org/package.whl"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                mediator.retrieve_package({"method": "GET", "url": url, "body": ""})

    def test_even_an_approved_request_cannot_reach_host_loopback_by_default(self):
        from scripts.skill_network import NetworkMediator

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/action"
        with self.assertRaises(PermissionError):
            NetworkMediator(public_resources=set()).dispatch_approved({
                "method": "POST", "url": url, "body": "data",
            })
        self.assertEqual(server.requests, [])

    def test_approved_action_reaches_only_the_selected_destination_with_selected_data(self):
        from scripts.skill_network import NetworkMediator

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/action"
        result = NetworkMediator(public_resources=set(), allow_private=True).dispatch_approved({
            "method": "POST", "url": url, "body": "one report",
        })
        self.assertEqual(result, b"accepted")
        self.assertEqual(server.requests, [b"one report"])

    def test_request_headers_cannot_be_silently_authorized(self):
        from scripts.skill_network import NetworkMediator

        url = "https://example.invalid/public"
        with self.assertRaises(PermissionError):
            NetworkMediator(public_resources={url}).dispatch({
                "method": "GET", "url": url, "body": "", "headers": {"Authorization": "secret"},
            })

    def test_unapproved_external_action_is_not_dispatched(self):
        from scripts.skill_network import NetworkMediator

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        server.requests = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/action"
        with self.assertRaises(PermissionError):
            NetworkMediator(public_resources=set()).dispatch({
                "method": "POST", "url": url, "body": "private data",
            })
        self.assertEqual(server.requests, [])

    def test_registered_non_http_resource_is_rejected(self):
        from scripts.skill_network import NetworkMediator

        with self.assertRaises(ValueError):
            NetworkMediator(public_resources={"file:///private"}).retrieve_public("file:///private")

    def test_unregistered_destination_is_not_contacted(self):
        from scripts.skill_network import NetworkMediator

        with self.assertRaises(PermissionError):
            NetworkMediator(public_resources=set()).retrieve_public("http://127.0.0.1/private")

    def test_explicitly_registered_public_resource_can_be_retrieved(self):
        from scripts.skill_network import NetworkMediator

        server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/public"
        mediator = NetworkMediator(public_resources={url}, allow_private=True)
        self.assertEqual(mediator.retrieve_public(url), b"public information")
