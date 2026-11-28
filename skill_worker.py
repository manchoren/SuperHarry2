"""Execute one skill inside a disposable, externally constrained container."""
import base64
import html
import hashlib
import json
import os
import re
import stat
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
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_COUNT = 5
exchange_lock = threading.Lock()


def exchange(kind, action):
    with exchange_lock:
        sys.stdout.write(json.dumps({"kind": kind, "action": action}, ensure_ascii=True) + "\n")
        sys.stdout.flush()
        response = json.loads(sys.stdin.buffer.readline(9 * 1024 * 1024))
    if not isinstance(response, dict) or response.get("status") != "complete":
        raise ValueError("Network request failed")
    if kind == "browser":
        return response
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


def serve_network(path, *, max_request_bytes=64 * 1024):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(4)
        while True:
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as incoming:
                try:
                    raw = incoming.readline(max_request_bytes + 1)
                    if len(raw) > max_request_bytes:
                        raise ValueError("Request too large")
                    action = json.loads(raw)
                    if not isinstance(action, dict):
                        raise ValueError("Invalid network request")
                    kind = action.pop("kind", "network")
                    if kind not in ("network", "browser"):
                        raise ValueError("Invalid network request")
                    value = exchange(kind, action)
                    if kind == "browser":
                        response = value
                        if (set(response) != {"status", "http_status", "headers", "body", "reviewed_action"}
                                or type(response["http_status"]) is not int
                                or type(response["reviewed_action"]) is not bool
                                or not isinstance(response["headers"], dict)
                                or not isinstance(response["body"], str)):
                            raise ValueError("Invalid mediated browser response")
                        body = base64.b64decode(response["body"], validate=True)
                        if len(body) > 2 * 1024 * 1024:
                            raise ValueError("Browser response too large")
                    else:
                        body = value
                        response = {"status": "complete", "body": base64.b64encode(body).decode("ascii")}
                    connection.sendall(json.dumps(response, ensure_ascii=True).encode("ascii") + b"\n")
                except (OSError, ValueError, TypeError):
                    try:
                        connection.sendall(b'{"status":"failed"}\n')
                    except OSError:
                        pass


def run_playwright_request(request):
    from browser_tasks import validate_browser_url

    output = {"status": "failed", "stage": "execution", "reason": "browser_failed"}
    try:
        if set(request) != {"runtime", "target_url", "execution_timeout"} or request["runtime"] != "playwright-browser":
            raise ValueError("Invalid browser request")
        target_url = validate_browser_url(request["target_url"])
        timeout = request["execution_timeout"]
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 150:
            raise ValueError("Invalid execution timeout")
        root = Path("/work")
        socket_path = root / ".skill-network.sock"
        threading.Thread(target=serve_network, args=(socket_path,), daemon=True).start()
        import time
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.01)
        if not socket_path.exists():
            raise OSError("Browser mediator unavailable")
        sys.stdout.write('{"kind":"execution"}\n')
        sys.stdout.flush()
        completed = subprocess.run(
            ["node", os.environ["SKILL_PLAYWRIGHT_ENTRYPOINT"]],
            input=json.dumps({"runtime": "playwright-browser", "target_url": target_url,
                              "execution_timeout": timeout}).encode("utf-8"),
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/work", "TMPDIR": "/work",
                 "PLAYWRIGHT_BROWSERS_PATH": os.environ["PLAYWRIGHT_BROWSERS_PATH"],
                 "SKILL_NETWORK_SOCKET": str(socket_path)},
        )
        if len(completed.stdout) > MAX_OUTPUT:
            raise ValueError("Browser output limit")
        structured = json.loads(completed.stdout.decode("utf-8"))
        if (not isinstance(structured, dict) or set(structured) != {"ok", "message", "citations"}
                or structured["ok"] is not True or not isinstance(structured["message"], str)
                or len(structured["message"].encode("utf-8")) > MAX_OUTPUT
                or not isinstance(structured["citations"], list) or len(structured["citations"]) > 10):
            raise ValueError("Invalid browser response")
        citations = [validate_browser_url(value) for value in structured["citations"]]
        output = {"status": "complete", "output": structured["message"], "citations": citations}
    except subprocess.TimeoutExpired:
        output = {"status": "timeout", "stage": "execution", "reason": "execution_timeout"}
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        pass
    sys.stdout.write(json.dumps(output, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def run_playwright_session(request):
    """Keep one mediated Chromium page alive until screenshot, cancel, or TTL."""
    from browser_tasks import validate_browser_url

    output = {"status": "failed", "stage": "execution", "reason": "browser_failed"}
    process = None
    try:
        browser_search = request.get("browser_search") is True
        follow_public_links = request.get("follow_public_links") is True
        follow_authenticated_links = request.get("follow_authenticated_links") is True
        base_expected = {"runtime", "execution_timeout", "browser_session", "browser_handoff"}
        base_expected |= {"target_urls", "browser_search"} if browser_search else {"target_url"}
        if follow_public_links:
            base_expected.add("follow_public_links")
        if follow_authenticated_links:
            base_expected.add("follow_authenticated_links")
        action_keys = {"action_request", "action_route_id"}
        expected = base_expected if browser_search or not action_keys.issubset(request) else base_expected | action_keys
        if (set(request) != expected or request["runtime"] != "playwright-browser"
                or request["browser_session"] is not True or type(request["browser_handoff"]) is not bool
                or browser_search and request["browser_handoff"]
                or follow_public_links and (browser_search or request["browser_handoff"] or action_keys.issubset(request))
                or follow_authenticated_links and (not request["browser_handoff"] or browser_search or follow_public_links)
                or action_keys.issubset(request) and (request["browser_handoff"] is not True
                   or not isinstance(request["action_request"], str) or not 1 <= len(request["action_request"]) <= 1000
                   or not isinstance(request["action_route_id"], str) or not 1 <= len(request["action_route_id"]) <= 255)):
            raise ValueError("Invalid browser session request")
        if browser_search:
            target_urls = request["target_urls"]
            if not isinstance(target_urls, list) or not 1 <= len(target_urls) <= 5:
                raise ValueError("Invalid browser search destinations")
            target_urls = list(dict.fromkeys(validate_browser_url(value) for value in target_urls))
            if not target_urls:
                raise ValueError("At least one public search destination is required")
        else:
            target_url = validate_browser_url(request["target_url"])
        timeout = request["execution_timeout"]
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 150:
            raise ValueError("Invalid execution timeout")
        root = Path("/work")
        socket_path = root / ".skill-network.sock"
        threading.Thread(target=serve_network, args=(socket_path,),
                         kwargs={"max_request_bytes": 3 * 1024 * 1024}, daemon=True).start()
        import time
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.01)
        if not socket_path.exists():
            raise OSError("Browser mediator unavailable")
        sys.stdout.write('{"kind":"execution"}\n')
        sys.stdout.flush()
        process = subprocess.Popen(
            ["node", os.environ["SKILL_PLAYWRIGHT_SESSION_ENTRYPOINT"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=root,
            env={"PATH": "/usr/bin:/bin", "HOME": "/work", "TMPDIR": "/work",
                 "PLAYWRIGHT_BROWSERS_PATH": os.environ["PLAYWRIGHT_BROWSERS_PATH"],
                 "SKILL_NETWORK_SOCKET": str(socket_path)},
        )
        worker_payload = ({"runtime": "playwright-browser", "target_urls": target_urls,
                         "browser_search": True, "execution_timeout": timeout, "browser_session": True,
                         "browser_handoff": False} if browser_search else
                        {"runtime": "playwright-browser", "target_url": target_url,
                         "execution_timeout": timeout, "browser_session": True,
                         "browser_handoff": request["browser_handoff"]})
        if follow_public_links:
            worker_payload["follow_public_links"] = True
        if follow_authenticated_links:
            worker_payload["follow_authenticated_links"] = True
        if not browser_search and action_keys.issubset(request):
            worker_payload["action_request"] = request["action_request"]
        raw, _ = process.communicate(
            json.dumps(worker_payload).encode("utf-8") + b"\n",
            timeout=timeout,
        )
        if len(raw) > MAX_OUTPUT:
            raise ValueError("Browser session output limit")
        code = process.returncode
        structured = json.loads(raw.decode("utf-8"))
        if (isinstance(structured, dict) and set(structured) == {"status", "stage"}
                and structured.get("status") == "failed"
                and structured.get("stage") in {
                    "input", "setup", "owner_control", "owner_input", "owner_redirect_manual",
                    "owner_redirect_chain",
                    "owner_frame_capture", "return_control", "authenticated_continuation", "handoff_cleanup",
                    "result_extraction", "result_publish",
                }):
            output = {"status": "failed", "stage": structured["stage"], "reason": "browser_failed"}
        elif code != 0:
            raise subprocess.CalledProcessError(code, process.args)
        elif (not isinstance(structured, dict) or structured.get("status") not in ("complete", "timeout")):
            raise ValueError("Invalid browser session response")
        elif structured["status"] == "timeout":
            output = {"status": "timeout", "stage": "execution", "reason": "browser_session_idle_timeout"}
        elif (set(structured) != {"status", "message", "citations"}
              or not isinstance(structured["message"], str)
              or len(structured["message"].encode("utf-8")) > MAX_OUTPUT
              or not isinstance(structured["citations"], list) or len(structured["citations"]) > 10):
            raise ValueError("Invalid browser session result")
        else:
            citations = [validate_browser_url(value) for value in structured["citations"]]
            output = {"status": "complete", "output": structured["message"], "citations": citations}
    except subprocess.TimeoutExpired:
        output = {"status": "timeout", "stage": "execution", "reason": "browser_session_timeout"}
    except subprocess.CalledProcessError:
        pass
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        pass
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                output = {"status": "failed", "stage": "execution", "reason": "browser_cleanup_unverified"}
    sys.stdout.write(json.dumps(output, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def main():
    stage = "preparation"
    reason = "invalid_request"
    collect_outputs = False
    output_dir = Path("/work/output")
    artifact_count = MAX_ARTIFACT_COUNT
    artifact_bytes = MAX_ARTIFACT_BYTES
    baselines = {}
    try:
        raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("Request too large")
        request = json.loads(raw)
        if isinstance(request, dict) and request.get("runtime") == "playwright-browser":
            if request.get("browser_session") is True:
                run_playwright_session(request)
            else:
                run_playwright_request(request)
            return
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
        artifact_count = request.get("output_max_count", MAX_ARTIFACT_COUNT)
        artifact_bytes = request.get("output_max_bytes", MAX_ARTIFACT_BYTES)
        if type(artifact_count) is not int or not 1 <= artifact_count <= MAX_ARTIFACT_COUNT or type(artifact_bytes) is not int or not 1 <= artifact_bytes <= MAX_ARTIFACT_BYTES:
            raise ValueError("Invalid output limits")
        selected_files = request.get("input_files", [])
        if not isinstance(selected_files, list) or len(selected_files) > 5 or (request.get("requires_file_inputs") and not selected_files):
            raise ValueError("Invalid selected files")
        if selected_files and any(name == "inputs" or name.startswith("inputs/") for name in files):
            raise ValueError("The inputs directory is reserved")
        manifest = []
        total = 0
        for item in selected_files:
            if not isinstance(item, dict) or set(item) != {"id", "name", "data"} or not (type(item["id"]) is int and item["id"] > 0 or isinstance(item["id"], str) and re.fullmatch(r"artifact-[1-9][0-9]*", item["id"])) or not isinstance(item["name"], str) or len(item["name"]) > 255 or not isinstance(item["data"], str):
                raise ValueError("Invalid selected file")
            if any(existing["id"] == item["id"] for existing in manifest):
                raise ValueError("Duplicate selected file")
            contents = base64.b64decode(item["data"], validate=True)
            total += len(contents)
            if total > 4 * 1024 * 1024:
                raise ValueError("Selected files are too large")
            target = root / "inputs" / str(item["id"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents)
            manifest.append({"id": item["id"], "name": item["name"], "path": str(target)})
            digest = hashlib.sha256(contents).digest()
            baselines[item["name"]] = digest if item["name"] not in baselines else None
        environment["SKILL_INPUT_MANIFEST"] = json.dumps(manifest, ensure_ascii=True)
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
        if any(name == "output" or name.startswith("output/") for name in files):
            raise ValueError("The output directory is reserved")
        output_dir.mkdir(exist_ok=True)
        environment["SKILL_OUTPUT_DIR"] = str(output_dir)
        collect_outputs = True
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
        timeout = request.get("execution_timeout", 150)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 150:
            raise ValueError("Invalid execution timeout")
        completed = subprocess.run(
            [sys.executable, "-sB", str(root / entrypoint)],
            input=input_text.encode("utf-8"), cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, check=True,
            env={**environment, "PYTHONPATH": "/env/packages:/", "SKILL_NETWORK_SOCKET": str(socket_path)},
        )
        if len(completed.stdout) > MAX_OUTPUT:
            raise ValueError("Output too large")
        output = completed.stdout.decode("utf-8")
        if request.get("output_protocol", "text") == "structured":
            structured = json.loads(output)
            if not isinstance(structured, dict) or type(structured.get("ok")) is not bool or not isinstance(structured.get("message"), str):
                raise ValueError("Invalid skill response")
            if structured["ok"]:
                result = {"status": "complete", "output": structured["message"]}
            else:
                result = {"status": "failed", "stage": "execution", "reason": "skill_reported_error", "message": structured["message"][:1000]}
        elif request.get("output_protocol", "text") == "text":
            result = {"status": "complete", "output": output}
        else:
            raise ValueError("Invalid output protocol")
    except subprocess.TimeoutExpired:
        result = {"status": "timeout", "stage": stage, "reason": "execution_timeout"}
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        result = {"status": "failed", "stage": stage, "reason": reason}
    if collect_outputs:
        try:
            artifacts = []
            total = 0
            directory_fd = os.open(output_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for name in sorted(os.listdir(directory_fd)):
                    if not name or len(name.encode("utf-8")) > 255:
                        raise ValueError("Invalid output file")
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    with os.fdopen(os.open(name, flags, dir_fd=directory_fd), "rb") as handle:
                        verified = os.fstat(handle.fileno())
                        if not stat.S_ISREG(verified.st_mode) or verified.st_nlink != 1 or verified.st_size > artifact_bytes - total:
                            raise ValueError("Invalid output file")
                        contents = handle.read(artifact_bytes - total + 1)
                    total += len(contents)
                    if total > artifact_bytes:
                        raise ValueError("Output limit exceeded")
                    if baselines.get(name) == hashlib.sha256(contents).digest():
                        total -= len(contents)
                        continue
                    if len(artifacts) >= artifact_count:
                        raise ValueError("Output limit exceeded")
                    artifacts.append({"name": name, "data": base64.b64encode(contents).decode("ascii")})
            finally:
                os.close(directory_fd)
            if artifacts:
                with exchange_lock:
                    sys.stdout.write(json.dumps({"kind": "artifacts", "files": artifacts}, ensure_ascii=True) + "\n")
                    sys.stdout.flush()
                    acknowledgement = json.loads(sys.stdin.buffer.readline(4096))
                if acknowledgement.get("status") != "complete":
                    raise ValueError("Output persistence failed")
        except (OSError, ValueError, UnicodeError):
            result = {"status": "failed", "stage": "execution", "reason": "output_limit_or_invalid"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
