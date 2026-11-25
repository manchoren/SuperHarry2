"""Run a single skill in a network-free Docker container."""
import json
import subprocess
import threading
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


def run_skill(request, timeout=30, job_id=None):
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
        "--tmpfs=/env:rw,nosuid,nodev,size=64m,mode=1777", "superharry-skill-worker:local",
    ]
    try:
        outcome = _execute(command, payload, timeout)
    finally:
        cleanup_verified = remove_container(job_id)
    if not cleanup_verified:
        return {"status": "failed", "reason": "cleanup_unverified"}
    return outcome


def _execute(command, payload, timeout):
    process = None
    output = bytearray()
    overflow = threading.Event()
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def read_output():
            while chunk := process.stdout.read(4096):
                if len(output) + len(chunk) > MAX_RESPONSE:
                    overflow.set()
                    process.kill()
                    break
                output.extend(chunk)

        def write_input():
            try:
                process.stdin.write(payload)
                process.stdin.close()
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read_output, daemon=True)
        writer = threading.Thread(target=write_input, daemon=True)
        reader.start()
        writer.start()
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            return {"status": "timeout"}
        reader.join(timeout=5)
        writer.join(timeout=5)
        if code != 0 or overflow.is_set() or reader.is_alive():
            return {"status": "failed"}
        result = json.loads(output)
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
        try:
            if process and process.poll() is None:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            pass
