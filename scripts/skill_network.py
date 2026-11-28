"""Bounded retrieval and user-approved outbound requests."""
import hashlib
import http.client
import ipaddress
import json
import re
import threading
import socket
import ssl
import time
from uuid import uuid4
from urllib.parse import urlsplit

from superharry.csv_protocol import publish, read_limited
from superharry.browser_tasks import (
    validate_browser_action, validate_browser_handoff_url,
    validate_owner_get_action, validate_owner_login_submission,
)


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


def validate_browser_request(action):
    if not isinstance(action, dict) or set(action) != {"method", "url"} or action["method"] != "GET":
        raise PermissionError("Read-only browsing permits GET requests only")
    url = action["url"]
    if not isinstance(url, str) or any(ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise ValueError("Invalid browser destination")
    destination = urlsplit(url)
    if (destination.scheme not in ("http", "https") or not destination.hostname
            or destination.username is not None or destination.password is not None
            or destination.fragment):
        raise ValueError("Invalid browser destination")
    try:
        port = destination.port
    except ValueError:
        raise ValueError("Invalid browser destination port") from None
    expected_port = 443 if destination.scheme == "https" else 80
    if port == 0 or port is not None and port != expected_port:
        raise ValueError("Browser access is limited to public HTTP and HTTPS ports")
    return {"method": "GET", "url": url}


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

    def retrieve_browser(self, action, *, authorized=None, cancelled=None, timeout=10):
        """Fetch one browser resource without following redirects or allowing writes."""
        action = validate_browser_request(action)
        return self._retrieve_browser_request(action, authorized=authorized, cancelled=cancelled,
                                              timeout=timeout, owner_control=False)

    def retrieve_browser_owner(self, action, *, job_id=None, owner_id=None, token=None,
                               action_context=None, authorized=None, cancelled=None,
                               propose_action=None, wait_action=None, report_action=None,
                               before_send=None, timeout=10):
        """Mediate owner-controlled navigation and require exact approval for form actions."""
        if (not isinstance(action, dict) or set(action) != {"method", "url", "headers", "body"}
                or action.get("method") not in ("GET", "POST") or not isinstance(action.get("url"), str)
                or not isinstance(action.get("headers"), dict) or not isinstance(action.get("body"), bytes)
                or len(action["body"]) > 64 * 1024):
            raise PermissionError("Owner-controlled browser request is invalid")
        headers = {}
        allowed = {"accept", "accept-encoding", "accept-language", "content-type", "cookie", "origin", "referer", "user-agent"}
        for name, value in action["headers"].items():
            if (not isinstance(name, str) or not isinstance(value, str)
                    or name.casefold() not in allowed or len(value) > 8192
                    or any(ord(char) < 32 or ord(char) > 126 for char in value)):
                raise PermissionError("Owner-controlled browser headers are unavailable")
            headers[name.casefold()] = value
        if action["method"] == "GET":
            if action["body"]:
                raise PermissionError("Owner-controlled GET requests cannot contain a body")
        normalized = {"method": action["method"], "url": action["url"],
                      "headers": headers, "body": action["body"]}
        if action["method"] == "GET":
            try:
                validate_owner_get_action(action["url"], action_context)
            except (PermissionError, ValueError):
                if report_action is not None:
                    report_action("This GET form, button, or unclear action was stopped before it reached the website.")
                raise PermissionError("The owner-controlled GET action could not be checked safely") from None
            if before_send is not None:
                before_send()
            return self._retrieve_browser_request(normalized, authorized=authorized, cancelled=cancelled,
                                                  timeout=timeout, owner_control=True)

        try:
            validate_owner_login_submission(action["url"], action["body"], headers.get("content-type", ""))
        except (PermissionError, ValueError):
            try:
                review = validate_browser_action(normalized, action_context)
            except (PermissionError, ValueError):
                if report_action is not None:
                    report_action("This form was blocked because its destination, fields, or effect could not be checked safely.")
                raise PermissionError("The form action could not be checked safely") from None
            if (type(owner_id) is not int or job_id is None
                    or not isinstance(token, str) or not token
                    or not callable(propose_action) or not callable(wait_action)):
                if report_action is not None:
                    report_action("This action was blocked because task approval is no longer active.")
                raise PermissionError("An owner-reviewed action requires an active browser task")
            canonical = json.dumps({
                "request": {"method": normalized["method"], "url": normalized["url"],
                            "headers": headers, "body": action["body"].hex()},
                "review": review,
            }, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
            digest = hashlib.sha256(canonical).hexdigest()
            expires_at = time.time() + 60
            proposal = propose_action({
                "digest": digest, "method": review["method"], "url": review["url"],
                "summary": review["summary"], "form_text": review["form_text"],
                "fields": review["fields"], "expires_at": expires_at,
            })
            if proposal is None:
                if report_action is not None:
                    report_action("This action was blocked because owner review is unavailable.")
                raise PermissionError("A browser action is already pending or owner review is unavailable")
            deadline = time.monotonic() + 60
            decision = None
            while time.monotonic() < deadline:
                if (cancelled is not None and cancelled.is_set()
                        or authorized is not None and not authorized()):
                    break
                decision = wait_action(proposal["id"], digest)
                if decision is not None:
                    break
                time.sleep(0.15)
            if decision != "approve":
                if report_action is not None:
                    report_action("No action was sent because approval was denied, expired, or unavailable.")
                raise PermissionError("The browser action was denied, expired, or could not be confirmed")
            if cancelled is not None and cancelled.is_set() or authorized is not None and not authorized():
                if report_action is not None:
                    report_action("The action was not sent because browser task approval ended.")
                raise PermissionError("Browser task authorization ended before the action was sent")
            if before_send is not None:
                before_send()
            try:
                response = self._retrieve_browser_request(
                    normalized, authorized=authorized, cancelled=cancelled, timeout=timeout, owner_control=False,
                )
                # This marker is carried in the internal browser protocol, not
                # in site-controlled response headers. The adapter uses it to
                # stop an unreviewed follow-up GET after an approved action.
                response["reviewed_action"] = True
                return response
            except PermissionError:
                if report_action is not None:
                    report_action("The request was blocked by the browser network policy before it could be sent.")
                raise
            except Exception:
                if report_action is not None:
                    report_action("The connection ended after approval. The site may have received the action; check the page before trying again. No rollback was attempted.")
                raise
        if before_send is not None:
            before_send()
        return self._retrieve_browser_request(normalized, authorized=authorized, cancelled=cancelled,
                                              timeout=timeout, owner_control=True)

    def _retrieve_browser_request(self, action, *, authorized=None, cancelled=None, timeout=10, owner_control=False):
        url = action["url"]
        if owner_control:
            try:
                validate_browser_handoff_url(url)
            except ValueError:
                raise PermissionError("Owner-controlled sign-in requires HTTPS.") from None
        destination = urlsplit(url)
        method = action.get("method", "GET")
        request_body = action.get("body", b"")
        request_headers = action.get("headers", {
            "Accept": "*/*", "Accept-Encoding": "identity",
            "User-Agent": "SuperHarry-ReadOnlyBrowser/1.0",
        })
        host = destination.hostname.encode("idna").decode("ascii")
        port = destination.port if destination.port is not None else (443 if destination.scheme == "https" else 80)
        deadline = time.monotonic() + min(10, max(0.1, timeout))

        def check_active():
            if cancelled is not None and cancelled.is_set():
                raise PermissionError("Browser task was cancelled")
            if authorized is not None and not authorized():
                raise PermissionError("Task-scoped browsing approval is no longer active")
            if time.monotonic() >= deadline:
                raise TimeoutError("Browser request deadline exceeded")

        resolved = []
        resolution_error = []
        resolution_done = threading.Event()

        def resolve_destination():
            try:
                resolved.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except OSError as error:
                resolution_error.append(error)
            finally:
                resolution_done.set()

        threading.Thread(target=resolve_destination, daemon=True).start()
        while not resolution_done.wait(0.05):
            check_active()
        check_active()
        if resolution_error:
            raise resolution_error[0]
        addresses = resolved[0]
        if not addresses:
            raise OSError("Destination could not be resolved")
        resolved = [ipaddress.ip_address(info[4][0]) for info in addresses]
        if not self.allow_private and any(not address.is_global or address.is_multicast for address in resolved):
            raise PermissionError("Private and special network addresses are unavailable")
        family, kind, protocol, _, address = addresses[0]
        connection = http.client.HTTPConnection(host, port, timeout=10)
        raw_socket = socket.socket(family, kind, protocol)
        watcher_stop = threading.Event()
        abort_reason = []
        watched_socket = [raw_socket]

        def abort_if_inactive():
            while not watcher_stop.wait(0.05):
                try:
                    check_active()
                except PermissionError:
                    abort_reason.append("cancelled")
                except TimeoutError:
                    abort_reason.append("timeout")
                except Exception:
                    abort_reason.append("cancelled")
                if abort_reason:
                    try:
                        watched_socket[0].shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

        watcher = None
        try:
            check_active()
            raw_socket.settimeout(min(1, max(0.1, deadline - time.monotonic())))
            raw_socket.connect(address)
            watcher = threading.Thread(target=abort_if_inactive, daemon=True)
            watcher.start()
            if destination.scheme == "https":
                raw_socket = ssl.create_default_context().wrap_socket(raw_socket, server_hostname=host)
                watched_socket[0] = raw_socket
            connection.sock = raw_socket
            check_active()
            target = destination.path or "/"
            if destination.query:
                target += "?" + destination.query
            connection.request(method, target, body=request_body if request_body else None,
                               headers=request_headers)
            try:
                response = connection.getresponse()
            except (OSError, http.client.HTTPException):
                if abort_reason:
                    raise TimeoutError("Browser request deadline exceeded") if abort_reason[0] == "timeout" else PermissionError("Browser task authorization was revoked")
                raise
            body = bytearray()
            while True:
                check_active()
                raw_socket.settimeout(min(1, max(0.1, deadline - time.monotonic())))
                try:
                    chunk = response.read1(min(64 * 1024, 2 * 1024 * 1024 + 1 - len(body)))
                except socket.timeout:
                    continue
                except (OSError, http.client.HTTPException):
                    if abort_reason:
                        raise TimeoutError("Browser request deadline exceeded") if abort_reason[0] == "timeout" else PermissionError("Browser task authorization was revoked")
                    raise
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > 2 * 1024 * 1024:
                    raise ValueError("Browser resource exceeds the size limit")
            check_active()
            safe_headers = {}
            allowed_headers = {
                "content-type", "content-encoding", "location", "cache-control", "expires",
                "last-modified", "etag", "content-language", "access-control-allow-origin",
                "access-control-allow-credentials", "access-control-expose-headers",
                "content-security-policy", "x-content-type-options", "cross-origin-resource-policy",
            }
            for name, value in response.getheaders():
                lowered = name.lower()
                if (lowered in allowed_headers or owner_control and lowered == "set-cookie") and "\r" not in value and "\n" not in value:
                    if lowered == "set-cookie":
                        cookies = safe_headers.setdefault("set-cookie", [])
                        if len(cookies) >= 16 or len(value) > 4096:
                            raise ValueError("Browser cookie response exceeded its limit")
                        cookies.append(value)
                        continue
                    safe_headers[lowered] = value
            return {"status": response.status, "headers": safe_headers, "body": bytes(body)}
        finally:
            watcher_stop.set()
            if watcher is not None:
                watcher.join(timeout=0.5)
            connection.close()
            raw_socket.close()

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
