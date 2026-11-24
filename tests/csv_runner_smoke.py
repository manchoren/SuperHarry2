import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from scripts import run_csv_jobs
from superharry.csv_protocol import enqueue


PROBE = """
import errno
import os
import socket
from pathlib import Path

assert os.getuid() == 10001
assert not Path("/run/secrets").exists()
assert not Path("/var/run/docker.sock").exists()
assert set(os.listdir("/sys/class/net")) == {"lo"}
try:
    socket.create_connection(("192.0.2.1", 8000), timeout=1)
except OSError as error:
    assert error.errno == errno.ENETUNREACH
else:
    raise AssertionError("Worker reached the network")
assert Path("/sys/fs/cgroup/cpu.max").read_text().strip() == "50000 100000"
assert Path("/sys/fs/cgroup/memory.max").read_text().strip() == "67108864"
assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "32"
try:
    Path("/tmp/write-probe").write_text("probe")
except OSError as error:
    assert error.errno == errno.EROFS
else:
    raise AssertionError("Worker could write to its root filesystem")
"""


def with_python(command, code):
    return command[:-1] + [
        "--entrypoint", "/opt/venv/bin/python", command[-1], "-Ic", code,
    ]


def main():
    probe_id = uuid4()
    subprocess.run(with_python(run_csv_jobs.job_command(probe_id), PROBE), check=True, timeout=30)
    with TemporaryDirectory() as directory:
        spool = Path(directory)
        succeeded, failed, oversized, timed_out = (uuid4() for _ in range(4))
        enqueue(spool, succeeded, b"Name,Amount\nAlpha,4\nBeta,\n")
        enqueue(spool, failed, b"Name,Amount\nWrong width\n")
        header = ",".join("\x01" * 48 for _ in range(64)).encode()
        enqueue(spool, oversized, header)
        enqueue(spool, timed_out, b"Header\nValue\n")
        original_command = run_csv_jobs.job_command
        original_run = run_csv_jobs.run_job

        def fixture_command(job_id):
            command = original_command(job_id)
            if job_id == timed_out:
                return with_python(command, "import time;time.sleep(60)")
            return command

        def fixture_run(job_id, payload):
            return original_run(job_id, payload, timeout=3 if job_id == timed_out else 30)

        with patch.object(run_csv_jobs, "job_command", fixture_command):
            with patch.object(run_csv_jobs, "run_job", fixture_run):
                run_csv_jobs.process_queue(spool)

        states = {}
        for filename in spool.iterdir():
            assert filename.suffix == ".result", filename.name
            result = json.loads(filename.read_bytes())
            states[filename.stem] = result["status"]
            if filename.stem == str(succeeded):
                assert result["summary"]["rows"] == 2
            check = subprocess.run(
                ["docker", "container", "inspect", run_csv_jobs.container_name(filename.stem)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            assert check.returncode != 0, "Disposable job container was retained"
        assert states == {
            str(succeeded): "complete", str(failed): "failed",
            str(oversized): "failed", str(timed_out): "timeout",
        }, states
    print("CSV runner limits, network denial, and success/failure/timeout cleanup passed.")


if __name__ == "__main__":
    main()
