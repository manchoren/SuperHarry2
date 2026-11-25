"""Client for the skill runner's mediated network channel."""
import base64
import json
import os
import socket


def request_bytes(method, url, body="", headers=None):
    action = {"method": method, "url": url, "body": body, "headers": {} if headers is None else headers}
    payload = json.dumps(action, ensure_ascii=True).encode("ascii") + b"\n"
    if len(payload) > 64 * 1024:
        raise ValueError("Network request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(150)
        channel.connect(os.environ["SKILL_NETWORK_SOCKET"])
        channel.sendall(payload)
        with channel.makefile("rb") as response:
            line = response.readline(2 * 1024 * 1024)
    value = json.loads(line)
    if value.get("status") != "complete":
        raise PermissionError("Mediated request failed or was denied")
    return base64.b64decode(value["body"], validate=True)


def request(method, url, body="", headers=None):
    return request_bytes(method, url, body, headers).decode("utf-8")
