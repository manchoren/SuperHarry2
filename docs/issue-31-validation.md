# Issue 31: local validation

Validation on 2026-11-25 used the working tree based on `f1b3faf0e28b171fb36303397c114064c722d6b0`; these workflow and development dependency changes were not yet committed. Docker Desktop reported Engine 29.9.3, runc 1.5.3 and containerd 2.4.2. The network tests used controlled local destinations and disposable skill containers, temporary spools and Django test databases. No runtime gate was bypassed.

- Windows: `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt` and `.venv/Scripts/python.exe -m ruff check --select F821,F822,F823 .` both passed with Ruff 0.14.7.
- Linux container: installed `requirements-dev.txt` in a temporary venv and ran the documented Ruff check successfully. Since the source mount was read-only, `RUFF_CACHE_DIR=/tmp/ruff-cache` was set. The first attempt without this override failed because Ruff could not write its cache; this was a test-mount restriction, not a lint failure.
- `python -c "from scripts.run_skill_jobs import validate_runtime; validate_runtime()"` passed. Built `superharry-skill-worker:local` and tagged it `superharry-skill-network-test:local` as in CI.
- `.venv/Scripts/python.exe -m tests.skill_network_sandbox_smoke` passed. With `DJANGO_SETTINGS_MODULE=superharry.settings`, a synthetic signing key and `SKILL_NETWORK_DOCKER_SMOKE=1`, `.venv/Scripts/python.exe -m django test tests.test_skill_network_docker --verbosity 1` ran 13 tests with no skips and passed.
- A controlled failure was injected into the network smoke by invoking `main()` with `unittest.mock.patch('scripts.skill_network.NetworkMediator.dispatch', side_effect=ValueError('deliberate failure'))`. The assertion failed and the process returned exit 1. This verifies local failure propagation; an actual GitHub Actions run remains pending and has not been observed.

The CI workflow retains its CSV, sandbox and preparation checks and the runtime gate, and now invokes both network checks. No live provider or deployed service was tested. The workflow changes themselves still need a GitHub-hosted execution; local validation is not evidence that the remote runner passed.
