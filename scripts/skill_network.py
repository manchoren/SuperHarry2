"""Bounded retrieval and user-approved outbound requests."""
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from uuid import uuid4
from urllib.parse import urlsplit

from superharry.csv_protocol import publish, read_limited


def validate_action(action):
    if (not isinstance(action, dict) or set(action) not in ({"method", "url", "body"}, {"method", "url", "body", "headers"})
            or any(not isinstance(action[key], str) for key in ("method", "url", "body"))
            or action["method"] not in ("GET", "POST", "PUT", "PATCH", "DELETE")):
        raise ValueError("Invalid request")
    headers = action.get("headers", {})
    if not isinstance(headers, dict):
        raise ValueError("Invalid request headers")
    forbidden = {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization",
                 "proxy-connection", "te", "trailer", "upgrade"}
    seen = set()
    total = 0
    for name, value in headers.items():
        if (not isinstance(name, str) or not isinstance(value, str)
                or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or name.lower() in forbidden or name.lower() in seen
                or any(ord(ch) < 32 or ord(ch) > 126 for ch in value)):
            raise ValueError("Invalid request headers")
        seen.add(name.lower())
        total += len(name) + len(value)
    if total > 16 * 1024 or len(action["body"].encode("utf-8")) > 48 * 1024:
        raise ValueError("Request exceeds the size limit")
    return {"method": action["method"], "url": action["url"], "body": action["body"], "headers": headers.copy()}


class NetworkMediator:
    def __init__(self, public_resources, *, allow_private=False):
        self.public_resources = frozenset(public_resources)
        self.allow_private = allow_private

    def dispatch(self, action):
        action = validate_action(action)
        if action["method"] == "GET" and not action["body"] and not action["headers"]:
            return self.retrieve_public(action["url"])
        raise PermissionError("This action requires approval")

    def dispatch_approved(self, action, *, cancelled=None):
        action = validate_action(action)
        return self._send(action["method"], action["url"], action["body"].encode("utf-8"),
                          headers=action["headers"], cancelled=cancelled)

    def retrieve_public(self, url):
        if url not in self.public_resources:
            raise PermissionError("Resource is not registered for public retrieval")
        return self._send("GET", url, b"")

    def retrieve_package(self, action):
        if (not isinstance(action, dict) or set(action) != {"method", "url", "body"}
                or action["method"] != "GET" or action["body"] != "" or not isinstance(action["url"], str)):
            raise ValueError("Invalid package request")
        target = urlsplit(action["url"])
        index = target.hostname == "pypi.org" and re.fullmatch(r"/simple/[A-Za-z0-9._-]+/", target.path)
        wheel = (target.hostname == "files.pythonhosted.org" and
                 re.fullmatch(r"/packages/[a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]{32,64}/(?:[A-Za-z0-9_.+-]|%2[Bb])+\.whl", target.path))
        if (target.scheme != "https" or target.port not in (None, 443) or target.username is not None
                or target.password is not None or target.query or target.fragment or not (index or wheel)):
            raise ValueError("Invalid package destination")
        return self._send("GET", action["url"], b"", limit=6 * 1024 * 1024)

    def _send(self, method, url, body, limit=1024 * 1024, headers=None, cancelled=None):
        if not isinstance(url, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
            raise ValueError("Invalid destination")
        destination = urlsplit(url)
        if (destination.scheme not in ("http", "https") or not destination.hostname
                or destination.username is not None or destination.password is not None or destination.fragment):
            raise ValueError("Invalid destination")
        host = destination.hostname.encode("idna").decode("ascii")
        port = destination.port if destination.port is not None else (443 if destination.scheme == "https" else 80)
        if port == 0:
            raise ValueError("Invalid destination port")
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if cancelled is not None and cancelled.is_set():
            raise PermissionError("Execution is no longer active")
        if not addresses:
            raise OSError("Destination could not be resolved")
        if not self.allow_private and any(
            not ipaddress.ip_address(info[4][0]).is_global or ipaddress.ip_address(info[4][0]).is_multicast
            for info in addresses
        ):
            raise PermissionError("Private and special network addresses are unavailable")
        family, kind, protocol, _, address = addresses[0]
        connection = http.client.HTTPConnection(host, port, timeout=10)
        raw_socket = socket.socket(family, kind, protocol)
        try:
            raw_socket.settimeout(10)
            raw_socket.connect(address)
            if destination.scheme == "https":
                raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
            connection.sock = raw_socket
            if cancelled is not None and cancelled.is_set():
                raise PermissionError("Execution is no longer active")
            target = destination.path or "/"
            if destination.query:
                target += "?" + destination.query
            connection.request(method, target, body=body if body else None, headers=headers or {})
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise OSError(f"Destination returned HTTP {response.status}")
            content = response.read(limit + 1)
            if len(content) > limit:
                raise ValueError("Response exceeds the size limit")
            return content
        finally:
            connection.close()
            raw_socket.close()


def mediate_action(action, spool, job_id, mediator, timeout=120, cancelled=None):
    action = validate_action(action)
    raw = json.dumps(action, ensure_ascii=True, sort_keys=True).encode("ascii")
    if len(raw) > 64 * 1024 or len(action["body"].encode("utf-8")) > 48 * 1024:
        raise ValueError("Request exceeds the size limit")
    try:
        return mediator.dispatch(action)
    except PermissionError:
        pass
    request_id = uuid4()
    request_path = spool / f"{job_id}.{request_id}.network-request"
    decision_path = spool / f"{job_id}.{request_id}.network-decision"
    digest = hashlib.sha256(raw).hexdigest()
    publish(request_path, raw, replace=False)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled.is_set():
                raise PermissionError("Execution is no longer active")
            try:
                decision = json.loads(read_limited(decision_path, 4096))
            except FileNotFoundError:
                time.sleep(0.1)
                continue
            if decision != {"decision": "approve", "digest": digest}:
                raise PermissionError("Request denied or invalid approval")
            if cancelled is not None and cancelled.is_set():
                raise PermissionError("Execution is no longer active")
            return mediator.dispatch_approved(action, cancelled=cancelled)
        raise TimeoutError("Approval timed out")
    finally:
        request_path.unlink(missing_ok=True)
        decision_path.unlink(missing_ok=True)
