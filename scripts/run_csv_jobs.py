import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from superharry.csv_protocol import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES, publish, read_limited


def container_name(job_id):
    return f"superharry-csv-{job_id}"


def job_command(job_id):
    return [
        "docker", "run", "--rm", "--interactive", "--pull=never",
        "--name", container_name(job_id), "--network=none", "--ipc=none",
        "--cpus=0.5", "--memory=64m", "--memory-swap=64m", "--pids-limit=32",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user", "10001:10001", "--log-driver=none", "superharry-csv-worker:local",
    ]


def remove_container(job_id):
    subprocess.run(
        ["docker", "rm", "-f", container_name(job_id)], check=False, timeout=15,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run_job(job_id, payload, timeout=15):
    try:
        completed = subprocess.run(
            job_command(job_id), input=payload, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True, timeout=timeout,
        )
        if len(completed.stdout) > MAX_OUTPUT_BYTES:
            raise ValueError("Summary exceeded the output limit.")
        summary = json.loads(completed.stdout)
        return {"status": "complete", "summary": summary}
    except subprocess.TimeoutExpired:
        remove_container(job_id)
        return {"status": "timeout"}
    except subprocess.CalledProcessError as error:
        # The worker is a Linux container even when the Docker host is Windows.
        alarm_signal = getattr(signal, "SIGALRM", 14)
        if error.returncode in (-alarm_signal, 128 + alarm_signal):
            return {"status": "timeout"}
        return {"status": "failed"}
    except (OSError, ValueError):
        return {"status": "failed"}


def valid_id(path):
    try:
        identifier = UUID(path.stem)
        return identifier if str(identifier) == path.stem else None
    except ValueError:
        return None


def process_queue(spool):
    for incoming in spool.glob("*.input"):
        job_id = valid_id(incoming)
        if job_id is None:
            continue
        running = spool / f"{job_id}.running"
        outcome = spool / f"{job_id}.result"
        if outcome.exists():
            incoming.unlink(missing_ok=True)
            continue
        incoming.replace(running)
        try:
            try:
                result = run_job(job_id, read_limited(running, MAX_INPUT_BYTES))
            except (OSError, ValueError):
                result = {"status": "failed"}
            publish(outcome, json.dumps(result, ensure_ascii=False).encode(), replace=True)
        finally:
            running.unlink(missing_ok=True)


def supervise(spool, once):
    import fcntl

    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(spool / ".worker.lock", flags, 0o600), "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        for running in spool.glob("*.running"):
            job_id = valid_id(running)
            if job_id is not None:
                remove_container(job_id)
                publish(spool / f"{job_id}.result", b'{"status":"failed"}', replace=False)
                running.unlink(missing_ok=True)
        while True:
            process_queue(spool)
            if once:
                return
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    supervise(args.spool.resolve(), args.once)
