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
import urllib.request


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
OLLAMA_API_URL = "http://127.0.0.1:11434"


def ensure_ollama_model(model, *, executable="ollama", api_url=OLLAMA_API_URL):
    """Pull the configured model on the host and confirm Ollama lists it."""
    model = (model or "").strip()
    if not model:
        return "disabled"
    if len(model) > 200 or any(character.isspace() for character in model):
        raise ValueError("OLLAMA_MODEL must be a single model name.")
    try:
        subprocess.run(
            [executable, "pull", model], check=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3600,
        )
        request = urllib.request.Request(f"{api_url.rstrip('/')}/api/tags")
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.load(response)
        installed = {
            item.get("name") for item in payload.get("models", [])
            if isinstance(item, dict)
        }
        if model not in installed:
            return "unavailable"
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unavailable"
    return "ready"


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


def configured_value(env_file, name):
    if name in os.environ:
        return os.environ[name]
    if env_file is None or not env_file.is_file():
        return ""
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        variable, separator, value = raw_line.partition("=")
        if separator and variable.strip().removeprefix("export ").strip() == name:
            tokens = shlex.split(value, comments=True)
            return tokens[0] if len(tokens) == 1 else ""
    return ""


def ollama_reachable_from_gateway(env, model, *, compose_command=None):
    if not model:
        return False
    script = (
        "fetch('http://host.docker.internal:11434/api/tags', {signal: AbortSignal.timeout(5000)})"
        ".then(async r=>{if(!r.ok)throw 0;const j=await r.json();"
        + "process.exit(j.models.some(m=>m.name===" + json.dumps(model)
        + ")?0:1)}).catch(()=>process.exit(1))"
    )
    result = subprocess.run(
        (compose_command or BASE) + ["exec", "-T", "omniroute", "node", "-e", script],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def provision(directory, env_file, ollama_model=None):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    upstream = provider_keys(env_file)
    ollama_model = (ollama_model or configured_value(env_file, "OLLAMA_MODEL")).strip()
    if not ollama_model and sys.stdin.isatty():
        ollama_model = input("Ollama model to provision (blank to skip): ").strip()
    ollama_status = ensure_ollama_model(ollama_model)
    env = dict(os.environ, OMNIROUTE_URL="http://omniroute:20128", COMPOSE_DISABLE_ENV_FILE="1")
    app = APP + (["-f", "compose.csv.yaml"] if env.get("CSV_SPOOL_DIRECTORY") else [])
    if env.get("SKILL_SPOOL_DIRECTORY"):
        app += ["-f", "compose.skill.yaml"]
    for name in PROVIDER_VARIABLES.values():
        env.pop(name, None)
    if env.get("SKILL_SPOOL_DIRECTORY"):
        compose(["docker", "build", "--target", "skill-worker", "-t", "superharry-skill-worker:local", "."], env)
        compose(["docker", "build", "--target", "skill-browser-worker", "-t", "superharry-skill-browser-worker:local", "."], env)
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

    ollama_available = ollama_status == "ready" and ollama_reachable_from_gateway(env, ollama_model)

    result = compose(BASE + [
        "exec", "-T", "omniroute", "node", "-e",
        (ROOT / "scripts" / "bootstrap-gateway.cjs").read_text(encoding="utf-8"),
    ], env, input=json.dumps({
        "password": read(directory / "omniroute_initial_password"),
        "inference": read(directory / "omniroute_api_key"),
        "management": read(directory / "omniroute_management_token"),
        "providers": upstream,
        "ollama": {"model": ollama_model, "available": ollama_available},
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
    auto_route = values.get("autoRoute") or values["route"]
    save(directory / "omniroute_auto_route", auto_route)
    env["CHAT_MODEL"] = values["route"]
    env["AUTO_CHAT_MODEL"] = auto_route
    compose(app + ["up", "--build", "-d", "django", "scheduler"], env)
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
    local_route = values.get("localRoute") or "unavailable"
    print(f"Provider configuration: {statuses}; canonical route verified: {values['verified']}; Ollama={local_route}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--secrets-dir", type=Path, default=ROOT / ".secrets")
    parser.add_argument("--provider-env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--ollama-model", default="", help="Named Ollama model; defaults to OLLAMA_MODEL or the interactive prompt")
    args = parser.parse_args()
    directory = args.secrets_dir.resolve()
    try:
        env_file = None if args.provider_env_file == Path("-") else args.provider_env_file.resolve()
        provision(directory, env_file, args.ollama_model)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, RuntimeError):
            parser.exit(1, f"{error}\n")
        parser.exit(1, "Provisioning failed. Check Docker service status and local configuration.\n")
