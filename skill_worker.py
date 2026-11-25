"""Execute one skill inside a disposable, externally constrained container."""
import base64
import html
import json
import re
import socket
import subprocess
import sys
import threading
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from skill_files import validate_skill_files

MAX_REQUEST = 8 * 1024 * 1024
MAX_OUTPUT = 16 * 1024
exchange_lock = threading.Lock()


def exchange(kind, action):
    with exchange_lock:
        sys.stdout.write(json.dumps({"kind": kind, "action": action}, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        response = json.loads(sys.stdin.buffer.readline(9 * 1024 * 1024))
    if response.get("status") != "complete":
        raise ValueError("Network request failed")
    return base64.b64decode(response["body"], validate=True)


class WheelLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attributes = dict(attrs)
        url = attributes.get("href", "")
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org"
                or parsed.username is not None or parsed.password is not None or parsed.query
                or not parsed.path.startswith("/packages/") or not parsed.path.endswith(".whl")):
            return
        source = f"https://files.pythonhosted.org{parsed.path}"
        encoded = base64.urlsafe_b64encode(source.encode("ascii")).decode("ascii").rstrip("=")
        filename = unquote(parsed.path.rsplit("/", 1)[-1])
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", filename):
            return
        local = f"/wheel/{encoded}/{filename}"
        if parsed.fragment:
            local += "#" + parsed.fragment
        requires = attributes.get("data-requires-python")
        extra = f' data-requires-python="{html.escape(requires, quote=True)}"' if requires else ""
        if "data-yanked" in attributes:
            extra += f' data-yanked="{html.escape(attributes["data-yanked"] or "", quote=True)}"'
        self.links.append(f'<a href="{html.escape(local, quote=True)}"{extra}>{html.escape(filename)}</a>')


class PackageProxy(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            local_path = unquote(self.path)
            if re.fullmatch(r"/simple/[A-Za-z0-9._-]+/", local_path):
                remote = "https://pypi.org" + local_path
                source = exchange("package", {"method": "GET", "url": remote, "body": ""})
                parser = WheelLinks()
                parser.feed(source.decode("utf-8"))
                data = ("\n".join(parser.links)).encode("utf-8")
            elif re.fullmatch(r"/wheel/[A-Za-z0-9_-]+/[A-Za-z0-9_.+-]+\.whl", local_path):
                encoded = local_path.split("/")[2]
                remote = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("ascii")
                parsed = urlsplit(remote)
                if (parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org"
                        or parsed.username is not None or parsed.password is not None
                        or parsed.query or parsed.fragment or not parsed.path.startswith("/packages/")
                        or unquote(parsed.path.rsplit("/", 1)[-1]) != local_path.rsplit("/", 1)[-1]):
                    raise ValueError("Invalid wheel URL")
                data = exchange("package", {"method": "GET", "url": remote, "body": ""})
            else:
                raise ValueError("Invalid package request")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8" if self.path.startswith("/simple/") else "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (OSError, ValueError, TypeError, UnicodeError):
            self.send_error(502)

    def log_message(self, *args):
        pass


def serve_network(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(4)
        while True:
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as incoming:
                try:
                    raw = incoming.readline(64 * 1024 + 1)
                    if len(raw) > 64 * 1024:
                        raise ValueError("Request too large")
                    action = json.loads(raw)
                    body = exchange("network", action)
                    response = {"status": "complete", "body": base64.b64encode(body).decode("ascii")}
                    connection.sendall(json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n")
                except (OSError, ValueError, TypeError):
                    try:
                        connection.sendall(b'{"status":"failed"}\n')
                    except OSError:
                        pass


def main():
    stage = "preparation"
    reason = "invalid_request"
    try:
        raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("Request too large")
        request = json.loads(raw)
        files = validate_skill_files(request["files"])
        entrypoint = request["entrypoint"]
        input_text = request["input"]
        operation = request.get("operation", "run")
        if operation not in ("prepare", "run"):
            raise ValueError("Invalid operation")
        if entrypoint not in files or not isinstance(input_text, str):
            raise ValueError("Invalid input")
        dependencies = request.get("dependencies", [])
        wheels = request.get("wheels", {})
        if not isinstance(dependencies, list) or not isinstance(wheels, dict):
            raise ValueError("Invalid dependencies")
        if any(not isinstance(item, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*(?:==[A-Za-z0-9][A-Za-z0-9._+!-]*)?", item
        ) for item in dependencies):
            raise ValueError("Invalid dependency specification")
        wheel_dir = Path("/env/wheels")
        wheel_dir.mkdir(parents=True, exist_ok=True)
        for name, content in wheels.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name):
                raise ValueError("Invalid wheel name")
            if not isinstance(content, str):
                raise ValueError("Invalid wheel content")
            (wheel_dir / name).write_bytes(base64.b64decode(content, validate=True))
        environment = {"PATH": "/opt/venv/bin:/usr/bin:/bin", "HOME": "/work",
                       "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null",
                       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "TMPDIR": "/work"}
        if dependencies:
            reason = "dependency_preparation_failed"
            proxy = None
            index_args = ["--no-index"]
            if operation == "prepare" and not wheels:
                proxy = ThreadingHTTPServer(("127.0.0.1", 0), PackageProxy)
                threading.Thread(target=proxy.serve_forever, daemon=True).start()
                index_args = ["--index-url", f"http://127.0.0.1:{proxy.server_port}/simple/"]
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "install", "--no-cache-dir", "--retries", "0",
                     *index_args,
                     "--only-binary=:all:", "--find-links", str(wheel_dir), "--target", "/env/packages",
                     "--no-compile", *dependencies],
                    cwd="/", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=90, check=True, env=environment,
                )
            finally:
                if proxy is not None:
                    proxy.shutdown()
                    proxy.server_close()
        root = Path("/work")
        reason = "file_preparation_failed"
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if operation == "prepare":
            sys.stdout.write(json.dumps({"status": "complete", "output": ""}) + "\n")
            return
        stage = "execution"
        reason = "execution_failed"
        sys.stdout.write('{"kind":"execution"}\n')
        sys.stdout.flush()
        socket_path = root / ".skill-network.sock"
        threading.Thread(target=serve_network, args=(socket_path,), daemon=True).start()
        # Wait until the socket is bound before the skill tries to connect.
        import time
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.01)
        completed = subprocess.run(
            [sys.executable, "-sB", str(root / entrypoint)],
            input=input_text.encode("utf-8"), cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=150, check=True,
            env={**environment, "PYTHONPATH": "/env/packages:/", "SKILL_NETWORK_SOCKET": str(socket_path)},
        )
        if len(completed.stdout) > MAX_OUTPUT:
            raise ValueError("Output too large")
        result = {"status": "complete", "output": completed.stdout.decode("utf-8")}
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        result = {"status": "failed", "stage": stage, "reason": reason}
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
