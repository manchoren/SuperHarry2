import json
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile


MAX_INPUT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 16 * 1024


def read_limited(path, limit):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid job data.")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Invalid job data.")
    return data


def publish(path, data, *, replace):
    with NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        # The spool directory has restricted access. On Linux both the web
        # process and the host worker need to read the files created here.
        temporary.chmod(0o644)
        if replace:
            temporary.replace(path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
    finally:
        temporary.unlink(missing_ok=True)


def enqueue(directory, job_id, payload):
    if any((directory / f"{job_id}.{suffix}").exists() for suffix in ("running", "result")):
        return
    publish(directory / f"{job_id}.input", payload, replace=False)


def read_result(directory, job_id):
    if directory is None:
        return None
    try:
        result = json.loads(read_limited(directory / f"{job_id}.result", MAX_OUTPUT_BYTES))
        if not isinstance(result, dict):
            return {"status": "failed"}
        if result.get("status") == "complete" and not isinstance(result.get("summary"), dict):
            return {"status": "failed"}
        return result
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"status": "failed"}
