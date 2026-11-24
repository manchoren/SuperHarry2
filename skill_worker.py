"""Execute one skill inside a disposable, externally constrained container."""
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

from skill_files import validate_skill_files

MAX_REQUEST = 8 * 1024 * 1024
MAX_OUTPUT = 16 * 1024


def main():
    stage = "preparation"
    reason = "invalid_request"
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("Request too large")
        request = json.loads(raw)
        files = validate_skill_files(request["files"])
        entrypoint = request["entrypoint"]
        input_text = request["input"]
        operation = request.get("operation", "run")
        if operation not in ("prepare", "run"):
            raise ValueError("Invalid operation")
        if entrypoint not in files or not isinstance(input_text, str):
            raise ValueError("Invalid input")
        dependencies = request.get("dependencies", [])
        wheels = request.get("wheels", {})
        if not isinstance(dependencies, list) or not isinstance(wheels, dict):
            raise ValueError("Invalid dependencies")
        if any(not isinstance(item, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*(?:==[A-Za-z0-9][A-Za-z0-9._+!-]*)?", item
        ) for item in dependencies):
            raise ValueError("Invalid dependency specification")
        wheel_dir = Path("/env/wheels")
        wheel_dir.mkdir(parents=True, exist_ok=True)
        for name, content in wheels.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name):
                raise ValueError("Invalid wheel name")
            if not isinstance(content, str):
                raise ValueError("Invalid wheel content")
            (wheel_dir / name).write_bytes(base64.b64decode(content, validate=True))
        environment = {"PATH": "/opt/venv/bin:/usr/bin:/bin", "HOME": "/work",
                       "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": "/dev/null",
                       "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1", "TMPDIR": "/work"}
        if dependencies:
            reason = "dependency_preparation_failed"
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "--no-index", "--no-cache-dir",
                 "--only-binary=:all:", "--find-links", str(wheel_dir), "--target", "/env/packages",
                 "--no-compile", *dependencies],
                cwd="/", stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=20, check=True, env=environment,
            )
        root = Path("/work")
        reason = "file_preparation_failed"
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if operation == "prepare":
            sys.stdout.write(json.dumps({"status": "complete", "output": ""}))
            return
        stage = "execution"
        reason = "execution_failed"
        completed = subprocess.run(
            [sys.executable, "-sB", str(root / entrypoint)],
            input=input_text.encode("utf-8"), cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=True,
            env={**environment, "PYTHONPATH": "/env/packages"},
        )
        if len(completed.stdout) > MAX_OUTPUT:
            raise ValueError("Output too large")
        result = {"status": "complete", "output": completed.stdout.decode("utf-8")}
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        result = {"status": "failed", "stage": stage, "reason": reason}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
