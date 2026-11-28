"""Run a single skill in a network-free Docker container."""
import json
import base64
import subprocess
import threading
import time
from uuid import UUID, uuid4


MAX_REQUEST = 8 * 1024 * 1024
MAX_RESPONSE = 3 * 1024 * 1024
MAX_OUTPUT = 16 * 1024


def _valid_browser_headers(headers, *, browser_handoff):
    if not isinstance(headers, dict):
        return False
    for name, value in headers.items():
        if not isinstance(name, str):
            return False
        if name.lower() == "set-cookie":
            if (not browser_handoff or not isinstance(value, list) or not 1 <= len(value) <= 16
                    or any(not isinstance(cookie, str) or len(cookie) > 4096
                           or "\r" in cookie or "\n" in cookie for cookie in value)):
                return False
        elif not isinstance(value, str) or len(value) > 16384:
            return False
    return True


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


def run_skill(request, timeout=30, job_id=None, *, image="superharry-skill-worker:local", network_handler=None, browser_handler=None, package_handler=None, cancelled=None, artifact_handler=None):
    try:
        request = dict(request)
        request["execution_timeout"] = min(150, max(0.1, timeout - 2))
        payload = json.dumps(request, ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError):
        return {"status": "failed"}
    if len(payload) > MAX_REQUEST:
        return {"status": "failed"}
    if request.get("runtime") == "playwright-browser" and browser_handler is None:
        return {"status": "failed", "reason": "browser_mediation_unavailable"}
    job_id = uuid4() if job_id is None else UUID(str(job_id))
    name = container_name(job_id)
    browser_runtime = request.get("runtime") == "playwright-browser"
    cpu_limit, memory_limit, pids_limit, work_limit = (
        ("1.0", "512m", "128", "64m") if browser_runtime else ("0.5", "128m", "32", "16m")
    )
    command = [
        "docker", "run", "--rm", "--interactive", "--pull=never", "--name", name,
        "--network=none", "--ipc=none", f"--cpus={cpu_limit}", f"--memory={memory_limit}",
        f"--memory-swap={memory_limit}", f"--pids-limit={pids_limit}", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--user=10001:10001", "--log-driver=none",
        f"--tmpfs=/work:rw,nosuid,nodev,size={work_limit},mode=1777",
        "--tmpfs=/env:rw,nosuid,nodev,size=64m,mode=1777", image,
    ]
    try:
        outcome = _execute(command, payload, timeout, network_handler, browser_handler, package_handler, cancelled, artifact_handler,
                           browser_handoff=request.get("browser_handoff") is True)
    finally:
        cleanup_verified = remove_container(job_id)
    if not cleanup_verified:
        return {"status": "failed", "reason": "cleanup_unverified"}
    return outcome


def _execute(command, payload, timeout, network_handler=None, browser_handler=None, package_handler=None, cancelled=None, artifact_handler=None, *, browser_handoff=False):
    process = None
    results = []
    captured_artifacts = []
    overflow = threading.Event()
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        def send_response(response):
            try:
                process.stdin.write(json.dumps(response).encode("ascii") + b"\n")
                process.stdin.flush()
            except (OSError, ValueError) as error:
                if process.poll() is None:
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        raise OSError("Skill worker stopped accepting mediator responses") from error

        def read_output():
            count = 0
            browser_count = 0
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
                    elif message.get("kind") == "artifacts":
                        files = message.get("files")
                        try:
                            if captured_artifacts or not isinstance(files, list) or not 1 <= len(files) <= 5:
                                raise ValueError("Invalid output list")
                            names = set()
                            total = 0
                            for item in files:
                                if not isinstance(item, dict) or set(item) != {"name", "data"} or not isinstance(item["name"], str) or not item["name"] or len(item["name"].encode("utf-8")) > 255 or item["name"] in names or any(char in item["name"] for char in ("/", "\\", "\x00")) or not isinstance(item["data"], str):
                                    raise ValueError("Invalid output file")
                                names.add(item["name"])
                                total += len(base64.b64decode(item["data"], validate=True))
                                if total > 2 * 1024 * 1024:
                                    raise ValueError("Output is too large")
                            if artifact_handler is not None:
                                artifact_handler(files)
                            captured_artifacts.extend(files)
                            response = {"status": "complete"}
                        except (OSError, ValueError, TypeError):
                            response = {"status": "failed"}
                        send_response(response)
                    elif message.get("kind") in ("network", "package", "browser"):
                        if message.get("kind") == "browser":
                            browser_count += 1
                            over_limit = browser_count > 256
                        else:
                            count += 1
                            over_limit = count > 64
                        handler = (package_handler if message.get("kind") == "package" and preparing
                                   else network_handler if message.get("kind") == "network"
                                   else browser_handler if message.get("kind") == "browser" and not preparing else None)
                        if over_limit or handler is None:
                            response = {"status": "failed"}
                        else:
                            try:
                                mediated = handler(message.get("action"))
                                if message.get("kind") == "browser":
                                    if (not isinstance(mediated, dict)
                                            or set(mediated) not in (
                                                {"status", "headers", "body"},
                                                {"status", "headers", "body", "reviewed_action"},
                                            )
                                            or type(mediated["status"]) is not int or not 100 <= mediated["status"] <= 599
                                            or "reviewed_action" in mediated and type(mediated["reviewed_action"]) is not bool
                                            or not _valid_browser_headers(mediated["headers"], browser_handoff=browser_handoff)
                                            or not isinstance(mediated["body"], bytes)
                                            or len(mediated["body"]) > 2 * 1024 * 1024):
                                        raise ValueError("Invalid browser response")
                                    response = {
                                        "status": "complete", "http_status": mediated["status"],
                                        "headers": mediated["headers"],
                                        "body": base64.b64encode(mediated["body"]).decode("ascii"),
                                        "reviewed_action": mediated.get("reviewed_action", False),
                                    }
                                else:
                                    if not isinstance(mediated, bytes) or len(mediated) > 6 * 1024 * 1024:
                                        raise ValueError("Invalid network response")
                                    response = {"status": "complete", "body": base64.b64encode(mediated).decode("ascii")}
                            except (OSError, ValueError, TypeError):
                                response = {"status": "failed"}
                        send_response(response)
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
        if overflow.is_set() or reader.is_alive():
            return {"status": "failed"}
        if len(results) != 1:
            return {"status": "failed"}
        result = results[0]
        if not isinstance(result, dict) or result.get("status") not in ("complete", "failed", "timeout"):
            return {"status": "failed"}
        browser_failure_stages = {
            "input", "setup", "owner_control", "owner_input", "owner_redirect_manual",
            "owner_redirect_chain",
            "owner_frame_capture", "return_control", "handoff_cleanup",
            "result_extraction", "result_publish",
        }
        safe_browser_failure = (
            browser_handoff and result.get("status") == "failed"
            and result.get("reason") == "browser_failed"
            and result.get("stage") in browser_failure_stages
        )
        if code != 0 and not safe_browser_failure and not (
            result.get("status") == "timeout"
            and result.get("stage") == "execution"
            and result.get("reason") == "execution_timeout"
        ):
            return {"status": "failed"}
        if result["status"] == "complete":
            if not isinstance(result.get("output"), str) or len(result["output"].encode("utf-8")) > MAX_OUTPUT:
                return {"status": "failed"}
            outcome = {"status": "complete", "output": result["output"]}
            citations = result.get("citations")
            if citations is not None:
                from superharry.browser_tasks import validate_browser_url

                if not isinstance(citations, list) or len(citations) > 10:
                    return {"status": "failed"}
                try:
                    outcome["citations"] = [validate_browser_url(url) for url in citations]
                except (TypeError, ValueError):
                    return {"status": "failed"}
            if captured_artifacts and artifact_handler is None:
                outcome["artifacts"] = captured_artifacts
            return outcome
        if result["status"] == "timeout":
            outcome = {"status": "timeout"}
            if captured_artifacts and artifact_handler is None:
                outcome["artifacts"] = captured_artifacts
            return outcome
        if result.get("stage") in browser_failure_stages and safe_browser_failure:
            return {"status": "failed", "stage": result["stage"], "reason": result["reason"]}
        if result.get("stage") in ("preparation", "execution"):
            failure = {"status": "failed", "stage": result["stage"]}
            if result.get("reason") in ("invalid_request", "dependency_preparation_failed", "file_preparation_failed", "execution_failed", "output_limit_or_invalid", "skill_reported_error"):
                failure["reason"] = result["reason"]
            if result.get("reason") == "skill_reported_error" and isinstance(result.get("message"), str):
                failure["message"] = result["message"][:1000]
            if captured_artifacts and artifact_handler is None:
                failure["artifacts"] = captured_artifacts
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
