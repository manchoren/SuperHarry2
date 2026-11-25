"""Process the dedicated skill spool from a host with Docker access."""
import argparse
import base64
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.skill_sandbox import remove_container, run_skill
from scripts.runtime_policy import check_component_versions
from scripts.skill_network import NetworkMediator, mediate_action
from scripts.worker_lock import lock_spool
from superharry.csv_protocol import publish, read_limited


MAX_JOB_BYTES = 8 * 1024 * 1024
DEFAULT_PUBLIC_RESOURCES = frozenset({"https://docs.python.org/3/"})


def job_id(path):
    try:
        identifier = UUID(path.name.split(".", 1)[0])
        return identifier if path.name.startswith(str(identifier) + ".") else None
    except ValueError:
        return None


def cleanup_network_requests(spool, identifier):
    for path in spool.glob(f"{identifier}.*.network-*"):
        parts = path.name.split(".")
        try:
            request_id = UUID(parts[1])
        except (IndexError, ValueError):
            continue
        if (len(parts) == 3 and parts[0] == str(identifier) and parts[1] == str(request_id)
                and parts[2] in ("network-request", "network-decision")):
            path.unlink(missing_ok=True)


def process_queue(spool, runtime_check=None, *, image=None, package_handler=None, public_resources=DEFAULT_PUBLIC_RESOURCES, mediator=None):
    while _process_queue_once(spool, runtime_check=runtime_check, image=image, package_handler=package_handler,
                              public_resources=public_resources, mediator=mediator):
        pass


def _process_queue_once(spool, runtime_check=None, *, image=None, package_handler=None, public_resources=DEFAULT_PUBLIC_RESOURCES, mediator=None):
    rescan = False
    for control in spool.glob("*.skill-control"):
        identifier = job_id(control)
        if identifier is None or (spool / f"{identifier}.skill-running").exists():
            continue
        applying = spool / f"{identifier}.skill-applying"
        try:
            control.replace(applying)
            request = json.loads(read_limited(applying, MAX_JOB_BYTES))
            generation = request.get("generation")
            if (not isinstance(generation, int) or isinstance(generation, bool) or generation < 1
                    or request.get("action") not in ("updated", "deleted")
                    or (request.get("action") == "updated" and not isinstance(request.get("payload"), dict))):
                raise ValueError("Invalid lifecycle control")
            incoming = spool / f"{identifier}.skill-input"
            outcome = spool / f"{identifier}.skill-result"
            try:
                previous = json.loads(read_limited(outcome, 100 * 1024))
            except (OSError, ValueError):
                previous = {}
            if isinstance(previous, dict) and previous.get("generation", 0) >= generation:
                applying.unlink(missing_ok=True)
                continue
            action = request.get("action")
            if action == "deleted" or (action == "updated" and (
                    (spool / f"{identifier}.skill-external").exists() or request.get("uncertain") or not request.get("payload"))):
                incoming.unlink(missing_ok=True)
                cleanup_network_requests(spool, identifier)
                reason = ("skill_deleted" if action == "deleted" else
                          "skill_changed_external" if (spool / f"{identifier}.skill-external").exists()
                          else "skill_changed_uncertain" if request.get("uncertain")
                          else "skill_changed_unavailable")
                publish(outcome, json.dumps({"status": "failed", "reason": reason, "generation": generation}).encode("ascii"), replace=True)
            elif action == "updated":
                payload = request["payload"]
                payload["_lifecycle_generation"] = generation
                outcome.unlink(missing_ok=True)
                publish(incoming, json.dumps(payload, ensure_ascii=True).encode("ascii"), replace=True)
            applying.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError, TypeError) as error:
            raise RuntimeError("Cannot safely apply skill lifecycle control.") from error
    for incoming in spool.glob("*.skill-input"):
        identifier = job_id(incoming)
        if identifier is None:
            continue
        running = spool / f"{identifier}.skill-running"
        outcome = spool / f"{identifier}.skill-result"
        if outcome.exists():
            incoming.unlink(missing_ok=True)
            continue
        if runtime_check is not None:
            runtime_check()
        try:
            incoming.replace(running)
        except FileNotFoundError:
            # A concurrent lifecycle operation may have claimed the queued job.
            continue
        request = {}
        try:
            request = json.loads(read_limited(running, MAX_JOB_BYTES))
            cancelled = threading.Event()
            control = spool / f"{identifier}.skill-control"
            stop_watcher = threading.Event()

            def watch_control():
                while not stop_watcher.wait(0.05):
                    if control.exists():
                        cancelled.set()

            watcher = threading.Thread(target=watch_control, daemon=True)
            watcher.start()
            active_mediator = mediator or NetworkMediator(public_resources=public_resources)
            wheels = dict(request.get("wheels", {}))

            def fetch_package(action):
                content = (package_handler or active_mediator.retrieve_package)(action)
                name = unquote(urlsplit(action["url"]).path.rsplit("/", 1)[-1])
                if name.endswith(".whl"):
                    if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name):
                        raise ValueError("Invalid wheel filename")
                    encoded = base64.b64encode(content).decode("ascii")
                    if name in wheels and wheels[name] != encoded:
                        raise ValueError("Conflicting wheel contents")
                    wheels[name] = encoded
                    if len(json.dumps({**request, "wheels": wheels}).encode("utf-8")) > MAX_JOB_BYTES:
                        raise ValueError("Prepared dependencies exceed the job size limit")
                return content

            def send_network(action):
                # This marker survives interruption and must precede any external request.
                publish(spool / f"{identifier}.skill-external", b"requested", replace=False)
                if cancelled.is_set():
                    raise PermissionError("Execution is no longer active")
                return mediate_action(action, spool, identifier, active_mediator, cancelled=cancelled)

            image_options = {"image": image} if image is not None else {}
            try:
                if request.get("_lifecycle_generation") and request.get("dependencies") and not wheels:
                    preparation = {**request, "operation": "prepare", "input": ""}
                    result = run_skill(preparation, job_id=identifier, timeout=180, cancelled=cancelled,
                                       package_handler=fetch_package, **image_options)
                    if result.get("status") == "complete" and not control.exists():
                        request["wheels"] = wheels
                        cancelled.clear()
                    else:
                        preparation = None
                else:
                    preparation = True
                if preparation is not None:
                    result = run_skill(
                        request, job_id=identifier, timeout=180, cancelled=cancelled,
                        network_handler=send_network,
                        package_handler=fetch_package if request.get("operation") == "prepare" else None,
                        **image_options,
                    )
            finally:
                stop_watcher.set()
                watcher.join()
            if request.get("operation") == "prepare" and result.get("status") == "complete":
                publish(spool / f"{identifier}.skill-wheels", json.dumps(wheels).encode("ascii"), replace=True)
                result["wheel_cache"] = True
        except (OSError, ValueError):
            result = {"status": "failed", "stage": "preparation", "reason": "invalid_request"}
        if result.get("reason") == "cleanup_unverified":
            publish(outcome, b'{"status":"failed","reason":"cleanup_unverified"}', replace=True)
            raise RuntimeError("Cannot verify cleanup of skill container.")
        cleanup_network_requests(spool, identifier)
        result["generation"] = request.get("_lifecycle_generation", 0)
        publish(outcome, json.dumps(result, ensure_ascii=True).encode("ascii"), replace=True)
        running.unlink(missing_ok=True)
        if (spool / f"{identifier}.skill-control").exists():
            rescan = True
    return rescan


def validate_runtime():
    try:
        components = json.loads(subprocess.check_output(
            ["docker", "version", "--format", "{{json .Server.Components}}"],
            text=True, timeout=15,
        ))
        versions = {entry["Name"]: entry["Version"] for entry in components}
        check_component_versions(versions)
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Skill worker runtime check failed: {error}") from error


def recover_interrupted(spool):
    for running in spool.glob("*.skill-running"):
        identifier = job_id(running)
        if identifier is not None:
            if not remove_container(identifier):
                raise RuntimeError("Cannot verify cleanup of interrupted skill container.")
            cleanup_network_requests(spool, identifier)
            try:
                request = json.loads(read_limited(running, MAX_JOB_BYTES))
                generation = request.get("_lifecycle_generation", 0)
            except (OSError, ValueError, AttributeError):
                generation = 0
            interrupted = {"status": "failed", "reason": "interrupted", "generation": generation}
            outcome = spool / f"{identifier}.skill-result"
            try:
                previous = json.loads(read_limited(outcome, 100 * 1024))
            except (OSError, ValueError):
                previous = {}
            cleanup_pending = isinstance(previous, dict) and previous.get("reason") == "cleanup_unverified"
            publish(outcome, json.dumps(interrupted).encode("ascii"), replace=cleanup_pending)
            running.unlink(missing_ok=True)
    for applying in spool.glob("*.skill-applying"):
        identifier = job_id(applying)
        if identifier is None:
            continue
        control = spool / f"{identifier}.skill-control"
        if control.exists():
            applying.unlink(missing_ok=True)
        else:
            applying.replace(control)


def supervise(spool, once, public_resources=DEFAULT_PUBLIC_RESOURCES, *, image=None):
    validate_runtime()
    with lock_spool(spool):
        recover_interrupted(spool)
        while True:
            process_queue(spool, runtime_check=validate_runtime, public_resources=public_resources, image=image)
            if once:
                return
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--public-resource", action="append", default=[],
                        help="An exact, independently verified public HTTPS URL eligible for retrieval without approval; may be repeated.")
    args = parser.parse_args()
    resources = set(DEFAULT_PUBLIC_RESOURCES)
    for resource in args.public_resource:
        url = urlsplit(resource)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.password is not None or url.fragment):
            parser.error("Public resources must be exact HTTPS URLs without credentials or fragments.")
        resources.add(resource)
    supervise(args.spool.resolve(), args.once, public_resources=resources)
