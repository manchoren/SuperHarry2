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
        incoming.replace(running)
        try:
            request = json.loads(read_limited(running, MAX_JOB_BYTES))
            cancelled = threading.Event()
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

            image_options = {"image": image} if image is not None else {}
            result = run_skill(
                request, job_id=identifier, timeout=180, cancelled=cancelled,
                network_handler=lambda action: mediate_action(action, spool, identifier, active_mediator, cancelled=cancelled),
                package_handler=fetch_package if request.get("operation") == "prepare" else None,
                **image_options,
            )
            if request.get("operation") == "prepare" and result.get("status") == "complete":
                publish(spool / f"{identifier}.skill-wheels", json.dumps(wheels).encode("ascii"), replace=True)
                result["wheel_cache"] = True
        except (OSError, ValueError):
            result = {"status": "failed", "stage": "preparation", "reason": "invalid_request"}
        if result.get("reason") == "cleanup_unverified":
            publish(outcome, b'{"status":"failed","reason":"cleanup_unverified"}', replace=True)
            raise RuntimeError("Cannot verify cleanup of skill container.")
        cleanup_network_requests(spool, identifier)
        publish(outcome, json.dumps(result, ensure_ascii=True).encode("ascii"), replace=True)
        running.unlink(missing_ok=True)


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
            publish(spool / f"{identifier}.skill-result", b'{"status":"failed","reason":"interrupted"}', replace=True)
            running.unlink(missing_ok=True)


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
