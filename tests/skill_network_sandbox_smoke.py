"""Exercise mediated retrieval from a real network-isolated skill container."""
import threading
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from uuid import uuid4

from scripts.skill_network import NetworkMediator
from scripts.skill_sandbox import run_skill
from tests.skill_sandbox_smoke import fixture_wheel

WHEEL_PATH = "/packages/aa/bb/cca/demo_skill_dep-1.0-py3-none-any.whl"
LOCAL_WHEEL_PATH = "/packages/aa/bb/cca/demo_skill_dep-1.0%2Bcpu-py3-none-any.whl"


class Destination(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if self.path == "/simple/demo-skill-dep/":
            wheel_path = LOCAL_WHEEL_PATH if getattr(self.server, "local_version", False) else WHEEL_PATH
            self.wfile.write(f'<a href="https://files.pythonhosted.org{wheel_path}">wheel</a>'.encode())
        elif self.path == WHEEL_PATH:
            self.wfile.write(base64.b64decode(fixture_wheel()))
        elif self.path == LOCAL_WHEEL_PATH:
            self.wfile.write(base64.b64decode(fixture_wheel("1.0+cpu")))
        elif self.path == "/binary":
            self.wfile.write(b"\x00\xff")
        else:
            self.wfile.write(b"controlled destination")

    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/public"
        mediator = NetworkMediator(public_resources={url}, allow_private=True)
        outcome = run_skill({
            "files": {"main.py": "from skill_network_client import request\nprint(request('GET', input(), ''))"},
            "entrypoint": "main.py", "input": url,
        }, job_id=uuid4(), image="superharry-skill-network-test:local", network_handler=mediator.dispatch)
        assert outcome == {"status": "complete", "output": "controlled destination\n"}, outcome
        invalid_headers = """from skill_network_client import request
try:
    request('GET', input(), headers='')
except PermissionError:
    print('rejected')
else:
    print('sent')
"""
        outcome = run_skill({"files": {"main.py": invalid_headers}, "entrypoint": "main.py", "input": url},
                            job_id=uuid4(), image="superharry-skill-network-test:local", network_handler=mediator.dispatch)
        assert outcome == {"status": "complete", "output": "rejected\n"}, outcome
        binary_url = f"http://127.0.0.1:{server.server_port}/binary"
        binary_mediator = NetworkMediator(public_resources={binary_url}, allow_private=True)
        outcome = run_skill({
            "files": {"main.py": "from skill_network_client import request_bytes\nprint(request_bytes('GET', input()).hex())"},
            "entrypoint": "main.py", "input": binary_url,
        }, job_id=uuid4(), image="superharry-skill-network-test:local", network_handler=binary_mediator.dispatch)
        assert outcome == {"status": "complete", "output": "00ff\n"}, outcome
        package_requests = []
        downloaded_wheels = {}
        def package_request(action):
            package_requests.append(action["url"])
            parsed = urlsplit(action["url"])
            assert parsed.hostname in {"pypi.org", "files.pythonhosted.org"}
            local_url = f"http://127.0.0.1:{server.server_port}{parsed.path}"
            data = NetworkMediator(public_resources={local_url}, allow_private=True).dispatch({
                "method": "GET", "url": local_url, "body": "",
            })
            if parsed.path.endswith(".whl"):
                downloaded_wheels[parsed.path.rsplit("/", 1)[-1]] = base64.b64encode(data).decode("ascii")
            return data

        outcome = run_skill({
            "operation": "prepare", "files": {"main.py": "print('unused')"},
            "entrypoint": "main.py", "input": "", "dependencies": ["demo-skill-dep==1.0"],
            "wheels": {},
        }, timeout=60, job_id=uuid4(), image="superharry-skill-network-test:local", package_handler=package_request)
        assert outcome == {"status": "complete", "output": ""}, (outcome, package_requests)
        before = list(package_requests)
        outcome = run_skill({
            "operation": "prepare", "files": {"main.py": "print('unused')"},
            "entrypoint": "main.py", "input": "", "dependencies": ["demo-skill-dep==1.0"],
            "wheels": downloaded_wheels,
        }, timeout=60, job_id=uuid4(), image="superharry-skill-network-test:local", package_handler=package_request)
        assert outcome == {"status": "complete", "output": ""}, outcome
        assert package_requests == before, package_requests
        outcome = run_skill({
            "operation": "run", "files": {"main.py": "from demo_skill_dep import value\nprint(value())"},
            "entrypoint": "main.py", "input": "", "dependencies": ["demo-skill-dep==1.0"],
            "wheels": downloaded_wheels,
        }, timeout=60, job_id=uuid4(), image="superharry-skill-network-test:local", package_handler=package_request)
        assert outcome == {"status": "complete", "output": "dependency ready\n"}, outcome
        assert package_requests == before, package_requests
        server.local_version = True
        outcome = run_skill({
            "operation": "prepare", "files": {"main.py": "print('unused')"},
            "entrypoint": "main.py", "input": "", "dependencies": ["demo-skill-dep==1.0+cpu"],
            "wheels": {},
        }, timeout=60, job_id=uuid4(), image="superharry-skill-network-test:local", package_handler=package_request)
        assert outcome == {"status": "complete", "output": ""}, (outcome, package_requests)
        probe = '''import json, os, socket
for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
    sock = socket.socket(socket.AF_INET, kind)
    sock.settimeout(1)
    try:
        sock.connect(("192.0.2.1", 80))
    except OSError:
        pass
    else:
        raise RuntimeError("direct network available")
    finally:
        sock.close()
with open("/proc/1/fd/1", "w") as out:
    out.write(json.dumps({"kind":"package", "action":{"method":"GET", "url":"https://pypi.org/simple/private-data/", "body":""}}) + "\\n")
    out.flush()
print("blocked")
'''
        outcome = run_skill({"files": {"main.py": probe}, "entrypoint": "main.py", "input": ""},
                            image="superharry-skill-network-test:local", package_handler=package_request)
        assert outcome == {"status": "complete", "output": "blocked\n"}, outcome
        assert package_requests[-2:] == ["https://pypi.org/simple/demo-skill-dep/", f"https://files.pythonhosted.org{LOCAL_WHEEL_PATH}"], package_requests
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
