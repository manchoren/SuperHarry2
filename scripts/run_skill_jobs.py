"""Process the dedicated skill spool from a host with Docker access."""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.skill_sandbox import remove_container, run_skill
from scripts.worker_lock import lock_spool
from superharry.csv_protocol import publish, read_limited


MAX_JOB_BYTES = 8 * 1024 * 1024


def job_id(path):
    try:
        identifier = UUID(path.name.split(".", 1)[0])
        return identifier if path.name.startswith(str(identifier) + ".") else None
    except ValueError:
        return None


def process_queue(spool, runtime_check=None):
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
            result = run_skill(request, job_id=identifier)
        except (OSError, ValueError):
            result = {"status": "failed", "stage": "preparation", "reason": "invalid_request"}
        if result.get("reason") == "cleanup_unverified":
            publish(outcome, b'{"status":"failed","reason":"cleanup_unverified"}', replace=True)
            raise RuntimeError("Cannot verify cleanup of skill container.")
        publish(outcome, json.dumps(result, ensure_ascii=True).encode("ascii"), replace=True)
        running.unlink(missing_ok=True)


def validate_runtime():
    try:
        components = json.loads(subprocess.check_output(
            ["docker", "version", "--format", "{{json .Server.Components}}"],
            text=True, timeout=15,
        ))
        versions = {entry["Name"]: entry["Version"] for entry in components}
        rejected = []
        for component, minimum in (("runc", (1, 4, 3)), ("containerd", (2, 3, 6))):
            reported = versions.get(component, "")
            match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:\+[\w.-]+)?", reported)
            if not match or tuple(map(int, match.groups())) < minimum:
                rejected.append(f"{component} {reported or 'unknown'} (project minimum {'.'.join(map(str, minimum))})")
        if rejected:
            raise ValueError("; ".join(rejected))
    except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Skill worker runtime check failed: {error}") from error


def recover_interrupted(spool):
    for running in spool.glob("*.skill-running"):
        identifier = job_id(running)
        if identifier is not None:
            if not remove_container(identifier):
                raise RuntimeError("Cannot verify cleanup of interrupted skill container.")
            publish(spool / f"{identifier}.skill-result", b'{"status":"failed","reason":"interrupted"}', replace=True)
            running.unlink(missing_ok=True)


def supervise(spool, once):
    validate_runtime()
    with lock_spool(spool):
        recover_interrupted(spool)
        while True:
            process_queue(spool, runtime_check=validate_runtime)
            if once:
                return
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    supervise(args.spool.resolve(), args.once)
