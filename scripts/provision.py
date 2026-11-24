import argparse
import json
import os
from pathlib import Path
import secrets
import shlex
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
BASE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.gateway.yaml"]
APP = BASE + ["-f", "compose.omniroute.yaml", "-f", "compose.omniroute-management.yaml"]
GATEWAY_SECRETS = {
    "omniroute_initial_password": "OMNIROUTE_INITIAL_PASSWORD_PATH",
    "omniroute_jwt_secret": "OMNIROUTE_JWT_SECRET_PATH",
    "omniroute_api_key_secret": "OMNIROUTE_API_KEY_SECRET_PATH",
}
PROVIDER_VARIABLES = {
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def read(path):
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def save(path, value):
    # Temporary files are created inside the private deployment directory.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(value + "\n")
        temporary = Path(handle.name)
    try:
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def compose(command, env, **kwargs):
    if "input" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    return subprocess.run(command, cwd=ROOT, env=env, text=True, check=True, **kwargs)


def provider_keys(env_file):
    keys = {}
    variables = {value: provider for provider, value in PROVIDER_VARIABLES.items()}
    if env_file is None:
        provider_env = sys.stdin.read()
    else:
        provider_env = env_file.read_text(encoding="utf-8-sig") if env_file.is_file() else ""
    for raw_line in provider_env.splitlines():
        name, separator, value = raw_line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        if separator and name in variables:
            try:
                tokens = shlex.split(value, comments=True)
            except ValueError:
                raise RuntimeError(f"Invalid format for {name} in the provider file.") from None
            if len(tokens) == 1:
                keys[variables[name]] = tokens[0]
    for provider, name in PROVIDER_VARIABLES.items():
        if name in os.environ:
            keys[provider] = os.environ[name]
    return keys


def provision(directory, env_file):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    upstream = provider_keys(env_file)
    env = dict(os.environ, OMNIROUTE_URL="http://omniroute:20128", COMPOSE_DISABLE_ENV_FILE="1")
    app = APP + (["-f", "compose.csv.yaml"] if env.get("CSV_SPOOL_DIRECTORY") else [])
    for name in PROVIDER_VARIABLES.values():
        env.pop(name, None)
    local_secrets = {
        **GATEWAY_SECRETS,
        "django_secret_key": "DJANGO_SECRET_KEY_PATH",
        "recovery_password": "RECOVERY_PASSWORD_PATH",
    }
    for filename, variable in local_secrets.items():
        path = directory / filename
        if not read(path):
            save(path, secrets.token_urlsafe(48))
        env[variable] = str(path)

    compose(BASE + ["up", "-d", "omniroute"], env)
    health_check = (
        "fetch('http://127.0.0.1:20128/api/health', {signal: AbortSignal.timeout(3000)})"
        ".then(r=>process.exit(r.ok?0:1),()=>process.exit(1))"
    )
    for _ in range(60):
        health = subprocess.run(
            BASE + ["exec", "-T", "omniroute", "node", "-e", health_check],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if health.returncode == 0:
            break
        time.sleep(2)
    else:
        raise RuntimeError("OmniRoute did not become healthy.")

    result = compose(BASE + [
        "exec", "-T", "omniroute", "node", "-e",
        (ROOT / "scripts" / "bootstrap-gateway.cjs").read_text(encoding="utf-8"),
    ], env, input=json.dumps({
        "password": read(directory / "omniroute_initial_password"),
        "inference": read(directory / "omniroute_api_key"),
        "management": read(directory / "omniroute_management_token"),
        "providers": upstream,
    }), capture_output=True)
    values = json.loads(result.stdout)
    for name, variable in (
        ("inference", "OMNIROUTE_API_KEY_PATH"),
        ("management", "OMNIROUTE_MANAGEMENT_TOKEN_PATH"),
    ):
        filename = "omniroute_api_key" if name == "inference" else "omniroute_management_token"
        path = directory / filename
        if read(path) != values[name]:
            save(path, values[name])
        env[variable] = str(path)
    save(directory / "omniroute_default_route", values["route"])
    env["CHAT_MODEL"] = values["route"]
    compose(app + ["up", "--build", "-d", "django"], env)
    for _ in range(30):
        report = subprocess.run(app + [
            "exec", "-T", "django", "python", "-m", "django", "shell", "-c",
            (
                "from urllib.request import urlopen; "
                "from superharry.models import ChatRoute; "
                "urlopen('http://127.0.0.1:8000/login/', timeout=3).close(); "
                "print(ChatRoute.active_id())"
            ),
        ], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if report.returncode == 0:
            print(f"Active route: {report.stdout.strip().splitlines()[-1]}")
            break
        time.sleep(2)
    else:
        raise RuntimeError("Django did not finish provisioning.")
    statuses = ", ".join(f"{name}={value}" for name, value in values["provisioning"].items())
    print(f"Provider configuration: {statuses}; canonical route verified: {values['verified']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--secrets-dir", type=Path, default=ROOT / ".secrets")
    parser.add_argument("--provider-env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    directory = args.secrets_dir.resolve()
    try:
        env_file = None if args.provider_env_file == Path("-") else args.provider_env_file.resolve()
        provision(directory, env_file)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, RuntimeError):
            parser.exit(1, f"{error}\n")
        parser.exit(1, "Provisioning failed. Check Docker service status and local configuration.\n")
