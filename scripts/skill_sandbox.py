"""Run a single skill in a network-free Docker container."""
import json
import base64
import subprocess
import threading
import time
from uuid import UUID, uuid4


MAX_REQUEST = 8 * 1024 * 1024
MAX_RESPONSE = 100 * 1024
MAX_OUTPUT = 16 * 1024


def container_name(job_id):
    return f"superharry-skill-{UUID(str(job_id)).hex}"


def remove_container(job_id):
    try:
        subprocess.run(["docker", "rm", "-f", container_name(job_id)], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, check=False)
        containers = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}"], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=15, check=True, text=True,
        )
        return container_name(job_id) not in containers.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return False


def run_skill(request, timeout=30, job_id=None, *, image="superharry-skill-worker:local", network_handler=None, package_handler=None, cancelled=None):
    try:
        payload = json.dumps(request, ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError):
        return {"status": "failed"}
    if len(payload) > MAX_REQUEST:
        return {"status": "failed"}
    job_id = uuid4() if job_id is None else UUID(str(job_id))
    name = container_name(job_id)
    command = [
        "docker", "run", "--rm", "--interactive", "--pull=never", "--name", name,
        "--network=none", "--ipc=none", "--cpus=0.5", "--memory=128m",
        "--memory-swap=128m", "--pids-limit=32", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--user=10001:10001", "--log-driver=none",
        "--tmpfs=/work:rw,nosuid,nodev,size=16m,mode=1777",
        "--tmpfs=/env:rw,nosuid,nodev,size=64m,mode=1777", image,
    ]
    try:
        outcome = _execute(command, payload, timeout, network_handler, package_handler, cancelled)
    finally:
        cleanup_verified = remove_container(job_id)
    if not cleanup_verified:
        return {"status": "failed", "reason": "cleanup_unverified"}
    return outcome


def _execute(command, payload, timeout, network_handler=None, package_handler=None, cancelled=None):
    process = None
    results = []
    overflow = threading.Event()
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def read_output():
            count = 0
            preparing = True
            try:
                while chunk := process.stdout.readline(MAX_RESPONSE + 1):
                    if len(chunk) > MAX_RESPONSE:
                        raise ValueError("Worker output too large")
                    message = json.loads(chunk)
                    if not isinstance(message, dict):
                        raise ValueError("Invalid worker message")
                    if message.get("kind") == "execution":
                        preparing = False
                    elif message.get("kind") in ("network", "package"):
                        count += 1
                        handler = (package_handler if message.get("kind") == "package" and preparing
                                   else network_handler if message.get("kind") == "network" else None)
                        if count > 64 or handler is None:
                            response = {"status": "failed"}
                        else:
                            try:
                                body = handler(message.get("action"))
                                if not isinstance(body, bytes) or len(body) > 6 * 1024 * 1024:
                                    raise ValueError("Invalid network response")
                                response = {"status": "complete", "body": base64.b64encode(body).decode("ascii")}
                            except (OSError, ValueError, TypeError):
                                response = {"status": "failed"}
                        process.stdin.write(json.dumps(response).encode("ascii") + b"\n")
                        process.stdin.flush()
                    else:
                        results.append(message)
                        if len(results) > 1:
                            raise ValueError("Duplicate worker result")
            except (OSError, ValueError, TypeError):
                overflow.set()
                try:
                    process.kill()
                except OSError:
                    pass

        def write_input():
            try:
                process.stdin.write(payload + b"\n")
                process.stdin.flush()
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read_output, daemon=True)
        writer = threading.Thread(target=write_input, daemon=True)
        reader.start()
        writer.start()
        deadline = time.monotonic() + timeout
        while True:
            if cancelled is not None and cancelled.is_set():
                process.kill()
                process.wait(timeout=5)
                reader.join(timeout=15)
                writer.join(timeout=5)
                if reader.is_alive() or writer.is_alive():
                    return {"status": "failed", "reason": "cleanup_unverified"}
                return {"status": "failed", "reason": "interrupted"}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if cancelled is not None:
                    cancelled.set()
                process.kill()
                process.wait(timeout=5)
                return {"status": "timeout"}
            try:
                code = process.wait(timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                continue
        if cancelled is not None:
            cancelled.set()
        reader.join(timeout=5)
        writer.join(timeout=5)
        if code != 0 or overflow.is_set() or reader.is_alive():
            return {"status": "failed"}
        if len(results) != 1:
            return {"status": "failed"}
        result = results[0]
        if not isinstance(result, dict) or result.get("status") not in ("complete", "failed"):
            return {"status": "failed"}
        if result["status"] == "complete":
            if not isinstance(result.get("output"), str) or len(result["output"].encode("utf-8")) > MAX_OUTPUT:
                return {"status": "failed"}
            return {"status": "complete", "output": result["output"]}
        if result.get("stage") in ("preparation", "execution"):
            failure = {"status": "failed", "stage": result["stage"]}
            if result.get("reason") in ("invalid_request", "dependency_preparation_failed", "file_preparation_failed", "execution_failed"):
                failure["reason"] = result["reason"]
            return failure
        return {"status": "failed"}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"status": "failed"}
    finally:
        if cancelled is not None:
            cancelled.set()
        try:
            if process and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            pass
        if process:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass
