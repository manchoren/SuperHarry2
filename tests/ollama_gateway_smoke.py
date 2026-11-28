"""Exercise a real host Ollama model through an isolated OmniRoute container."""

import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.provision import ensure_ollama_model, ollama_reachable_from_gateway, provider_keys

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:4b")
REQUEST_SCRIPT = r"""
let body = "";
process.stdin.on("data", chunk => body += chunk).on("end", async () => {
  const input = JSON.parse(body);
  try {
    const response = await fetch("http://127.0.0.1:20128/v1/chat/completions", {
      method: "POST",
      headers: {Authorization: `Bearer ${input.key}`, "Content-Type": "application/json"},
      body: JSON.stringify({model: input.model, stream: true, max_tokens: 128, reasoning_effort: "none",
        messages: [{role: "user", content: "Reply with the word ready."}]}),
      signal: AbortSignal.timeout(180000),
    });
    const raw = await response.text();
    let reply = "";
    let done = false;
    let finishReason = "";
    const lines = raw.split(/\r?\n/);
    let firstChoiceKeys = [];
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      if (line === "data: [DONE]") { done = true; continue; }
      try {
        const payload = JSON.parse(line.slice(6));
        firstChoiceKeys = firstChoiceKeys.length ? firstChoiceKeys : Object.keys(payload.choices?.[0]?.delta || {});
        reply += payload.choices?.[0]?.delta?.content || "";
        finishReason = payload.choices?.[0]?.finish_reason || finishReason;
        done = done || Boolean(finishReason);
      } catch {}
    }
    let errorCode = "";
    try { const error = JSON.parse(raw).error; errorCode = error?.code || error?.type || ""; } catch {}
    process.stdout.write(JSON.stringify({status: response.status, reply, done, errorCode, dataLines: lines.filter(line => line.startsWith("data: ")).length, firstChoiceKeys, finishReason, bodyLength: raw.length}));
    if (!response.ok || !reply || !done) process.exitCode = 1;
  } catch {
    process.stdout.write(JSON.stringify({status: 0, reply: "", done: false}));
    process.exitCode = 1;
  }
});
"""


def run(command, env, *, input_text=None, capture=True, check=True):
    return subprocess.run(
        command, cwd=ROOT, env=env, input=input_text, text=True,
        capture_output=capture, check=check, timeout=240,
    )


def main():
    if ensure_ollama_model(MODEL) != "ready":
        raise RuntimeError("The configured host Ollama model is unavailable.")
    with tempfile.TemporaryDirectory(prefix="superharry-ollama-smoke-") as temporary:
        secret_dir = Path(temporary)
        project = f"superharry-ollama-{uuid.uuid4().hex[:8]}"
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            host_port = port_socket.getsockname()[1]
        override = secret_dir / "compose.override.yaml"
        override.write_text(
            "services:\n  omniroute:\n    ports:\n      - \"127.0.0.1:{}:20128\"\n".format(host_port),
            encoding="utf-8",
        )
        compose = ["docker", "compose", "-p", project, "-f", str(ROOT / "compose.gateway.yaml"), "-f", str(override)]
        env = dict(os.environ, COMPOSE_DISABLE_ENV_FILE="1")
        password = secrets.token_urlsafe(32)
        for filename, variable in (
            ("initial_password", "OMNIROUTE_INITIAL_PASSWORD_PATH"),
            ("jwt_secret", "OMNIROUTE_JWT_SECRET_PATH"),
            ("api_key_secret", "OMNIROUTE_API_KEY_SECRET_PATH"),
        ):
            secret_file = secret_dir / filename
            secret_file.write_text(password if filename == "initial_password" else secrets.token_urlsafe(48), encoding="utf-8")
            env[variable] = str(secret_file)

        container = f"{project}-omniroute-1"
        try:
            run(compose + ["up", "-d", "omniroute"], env)
            if not ollama_reachable_from_gateway(env, MODEL, compose_command=compose):
                raise RuntimeError("Docker cannot reach the configured host Ollama model.")
            for _ in range(60):
                health = run(compose + ["exec", "-T", "omniroute", "node", "-e",
                    "fetch('http://127.0.0.1:20128/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],
                    env, check=False)
                if health.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("The isolated OmniRoute service did not become healthy.")

            run(["docker", "cp", str(ROOT / "scripts" / "bootstrap-gateway.cjs"), f"{container}:/tmp/bootstrap-gateway.cjs"], env)
            bootstrap = run(compose + ["exec", "-T", "omniroute", "node", "/tmp/bootstrap-gateway.cjs"], env,
                input_text=json.dumps({"password": password, "providers": provider_keys(ROOT / ".env"), "ollama": {"model": MODEL, "available": True}}), check=False)
            if bootstrap.returncode:
                raise RuntimeError(bootstrap.stderr.strip() or "OmniRoute bootstrap failed.")
            result = json.loads(bootstrap.stdout)
            local_route = f"superharry-local/{MODEL}"
            if result.get("localRoute") != local_route or result.get("provisioning", {}).get("ollama") != "ready":
                raise RuntimeError("OmniRoute did not publish the configured Ollama model route.")
            if result.get("autoRoute") != "superharry-auto":
                raise RuntimeError("OmniRoute did not publish the Free-first Auto route.")
            if result.get("autoTargets", [])[-1:] != [local_route]:
                raise RuntimeError("Auto does not end with the configured local route.")
            if result.get("verified") and result.get("autoTargets", [])[0] != result.get("route"):
                raise RuntimeError("Auto does not prefer the verified Free route.")

            inference = run(compose + ["exec", "-T", "omniroute", "node", "-e", REQUEST_SCRIPT], env,
                input_text=json.dumps({"key": result["inference"], "model": local_route}), check=False)
            response = json.loads(inference.stdout)
            if response.get("status") != 200 or not response.get("reply") or not response.get("done"):
                raise RuntimeError(f"The real Ollama route returned HTTP {response.get('status')} ({response.get('errorCode', '')}); markers {response.get('dataLines')}, delta {response.get('firstChoiceKeys')}, finish {response.get('finishReason')}, reply chars {len(response.get('reply', ''))}.")

            run(compose + ["restart", "omniroute"], env)
            probe_script = r"""
let body = "";
process.stdin.on("data", chunk => body += chunk).on("end", async () => {
  const input = JSON.parse(body);
  try {
    const response = await fetch("http://127.0.0.1:20128/v1/models", {
      headers: {Authorization: `Bearer ${input.key}`}, signal: AbortSignal.timeout(5000),
    });
    const payload = await response.json();
    process.exit(response.ok && payload.data.some(model => model.id === input.route) ? 0 : 1);
  } catch { process.exit(1); }
});
"""
            for _ in range(60):
                probe = run(compose + ["exec", "-T", "omniroute", "node", "-e", probe_script],
                    env, input_text=json.dumps({"key": result["inference"], "route": local_route}), check=False)
                if probe.returncode == 0:
                    break
                time.sleep(2)
            else:
                raise RuntimeError("The Ollama route was not restored after gateway restart.")
            catalog_request = urllib.request.Request(
                f"http://127.0.0.1:{host_port}/v1/models",
                headers={"Authorization": f"Bearer {result['inference']}"},
            )
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(catalog_request, timeout=10) as catalog_response:
                host_catalog = json.load(catalog_response)
            if not any(item.get("id") == local_route for item in host_catalog.get("data", [])):
                raise RuntimeError("The host-mapped OmniRoute catalog omitted the local model.")
            inference_key_file = secret_dir / "inference_api_key"
            inference_key_file.write_text(result["inference"], encoding="utf-8")
            app_env = dict(os.environ,
                DJANGO_SETTINGS_MODULE="superharry.settings",
                DJANGO_SECRET_KEY=secrets.token_urlsafe(48),
                DJANGO_DATABASE_PATH=str(secret_dir / "django.sqlite3"),
                OMNIROUTE_URL=f"http://127.0.0.1:{host_port}",
                OMNIROUTE_API_KEY_FILE=str(inference_key_file),
                CHAT_MODEL=result["route"],
                AUTO_CHAT_MODEL=result["autoRoute"],
                SMOKE_OLLAMA_ROUTE=local_route,
                SMOKE_AUTO_ROUTE=result["autoRoute"],
            )
            django_smoke = run([str(ROOT / ".venv" / "Scripts" / "python.exe"), str(ROOT / "tests" / "local_route_django_smoke.py")], app_env, check=False)
            if django_smoke.returncode:
                raise RuntimeError(django_smoke.stderr.strip() or "The Django local-route smoke test failed.")
            gpu = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                check=True, capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            placement = subprocess.run(["ollama", "ps"], check=True, capture_output=True, text=True, timeout=10).stdout
            processor = "unknown"
            for line in placement.splitlines()[1:]:
                fields = line.split()
                if MODEL in line:
                    processor = next((" ".join(fields[index:index + 2]) for index, field in enumerate(fields) if field.endswith("%") and index + 1 < len(fields)), processor)
                    break
            auto_history = django_smoke.stdout.strip()
            print(f"PASS: {MODEL} streamed through Django and isolated OmniRoute; {auto_history}; Auto targets={','.join(result['autoTargets'])}; route survived a gateway restart; GPU={gpu}; Ollama processor={processor}.")
        finally:
            run(compose + ["down", "--volumes", "--remove-orphans"], env, check=False)


if __name__ == "__main__":
    main()
