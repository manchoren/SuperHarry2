"""Deploy the existing SuperHarry Compose installation, optionally watching for changes."""

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILES = (
    "compose.yaml",
    "compose.gateway.yaml",
    "compose.omniroute.yaml",
    "compose.omniroute-management.yaml",
)
BUILD_FILES = ("Dockerfile", ".dockerignore", "requirements.txt", *COMPOSE_FILES, "compose.csv.yaml")
BUILD_DIRECTORIES = ("superharry", "tests")
POLL_SECONDS = 1


def deployment_environment():
    env = os.environ.copy()
    env.setdefault("OMNIROUTE_URL", "http://omniroute:20128")
    if not env.get("CHAT_MODEL"):
        route_path = ROOT / ".secrets" / "omniroute_default_route"
        try:
            env["CHAT_MODEL"] = route_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise RuntimeError("Set CHAT_MODEL or provision the existing gateway first (python scripts/provision.py).") from None
    if not env["CHAT_MODEL"]:
        raise RuntimeError("CHAT_MODEL must not be empty.")
    return env


def compose_command(env):
    command = ["docker", "compose"]
    for name in COMPOSE_FILES:
        command.extend(("-f", name))
    if env.get("CSV_SPOOL_DIRECTORY"):
        command.extend(("-f", "compose.csv.yaml"))
    return command


def fingerprint():
    """Track additions, deletions and modifications to files used by the build."""
    paths = [ROOT / name for name in BUILD_FILES]
    for name in BUILD_DIRECTORIES:
        directory = ROOT / name
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    result = {}
    for path in paths:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        result[str(path.relative_to(ROOT))] = (stat.st_mtime_ns, stat.st_size)
    return result


def deploy(env):
    command = compose_command(env)
    subprocess.run(command + ["up", "--build", "-d", "--no-deps", "django", "scheduler"],
                   cwd=ROOT, env=env, check=True)
    probe = (
        "from urllib.request import urlopen; "
        "urlopen('http://127.0.0.1:8000/login/', timeout=3).close()"
    )
    for _ in range(60):
        result = subprocess.run(
            command + ["exec", "-T", "django", "python", "-m", "django", "shell", "-c", probe],
            cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            running = subprocess.run(command + ["ps", "--status", "running", "--services"],
                                     cwd=ROOT, env=env, capture_output=True, text=True, check=True)
            if {"django", "scheduler"}.issubset(set(running.stdout.splitlines())):
                print("Deployment ready: Django and scheduler are running.", flush=True)
                return
        time.sleep(1)
    raise RuntimeError("Django or the scheduler did not become ready; inspect the Compose logs.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="Redeploy when application or build files change.")
    args = parser.parse_args()
    env = deployment_environment()
    previous = fingerprint()
    deploy(env)
    if not args.watch:
        return
    print("Watching application and build files. Press Ctrl+C to stop.", flush=True)
    while True:
        time.sleep(POLL_SECONDS)
        current = fingerprint()
        if current == previous:
            continue
        # Wait for a quiet interval so a group of editor saves causes one build.
        time.sleep(POLL_SECONDS)
        current = fingerprint()
        print("Changes detected; deploying again.", flush=True)
        try:
            deploy(env)
        except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
            print(f"Deployment failed: {error}", file=sys.stderr, flush=True)
        previous = current


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped watching.")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        sys.exit(1)
