# Docker and deployment runbook

The current working environment is Docker Desktop on Windows. A separate VM is not needed to start the application or run Docker Desktop checks. Earlier VM results are historical and do not prove the current deployment; see [milestones](milestones.md#3-skills-and-bundled-execution) for the separate acceptance gate.

## Start the Django recovery service

Put a random Django signing key and an initial recovery password in separate UTF-8 text files. The default paths are `.secrets/django_secret_key` and `.secrets/recovery_password`. Keep the directory private. On Linux, make the files readable by the container user (UID 10001). Set `DJANGO_SECRET_KEY_PATH` and `RECOVERY_PASSWORD_PATH` if the files are elsewhere.

Run `docker compose up --build -d` and open `http://127.0.0.1:8000/admin/` on the Docker host. The initial username is `recovery`. Set `SUPERHARRY_PORT` to use a different host port. For a remote Linux VM, forward the guest loopback port over SSH.

Django applies migrations and creates the recovery account on first start. Compose stores SQLite in the `django_data` volume. Restarting Django preserves the account and its current password. Keep the signing key stable across restarts.

Run `python -m tests.compose_smoke` on a host with Docker Compose and Python to verify login, static assets, the closed registration URL, and persistence across a restart. The test uses disposable credentials, a random localhost port, and an isolated Compose project.

The scheduler serializes processing with an operating-system lock file alongside the SQLite database. All scheduler processes for a database must use the same database path and a shared filesystem that supports these locks. After a process stops, the next scheduler invocation recovers unsubmitted occurrences and marks submitted, unfinished replies incomplete without resubmitting them. Django's chat recovery leaves scheduled attempts to the scheduler.

## Connect chat to an existing OmniRoute gateway

Set `OMNIROUTE_URL` to the gateway base URL reachable from Django's container and `CHAT_MODEL` to the canonical route ID shown by that gateway. Put the inference key in `.secrets/omniroute_api_key` or set `OMNIROUTE_API_KEY_PATH` to its file path. Use an inference credential.

Run `docker compose -f compose.yaml -f compose.omniroute.yaml up --build -d` and visit `http://127.0.0.1:8000/`. Sign in with an account created in Django admin. Run the commands from the same Compose project directory so both startup modes use the same SQLite volume.

To enable model-entry administration, create a separate OmniRoute Access Token with `write` scope and save it in `.secrets/omniroute_management_token`. Set `OMNIROUTE_MANAGEMENT_TOKEN_PATH` if the file is elsewhere. Keep the inference key free of management permissions. Add `-f compose.omniroute-management.yaml` after the other Compose files when starting Django.

Open **Chat routes** in Django admin to list routes visible to the inference key, test a route with a short inference request, and set the saved default used by chat. The saved selection overrides `CHAT_MODEL`. Add or update model entries by selecting an existing provider connection. OmniRoute stores these entries at provider level. Keep provider-account management, upstream credentials, OAuth, and fallback configuration in OmniRoute. Keep paid fallback disabled there.

In Django admin, **Chat limits** controls the global daily submission allowance and maximum number of characters per reply. **User chat limits** overrides either value for an individual account. A blank override inherits the global value. A daily allowance of zero disables chat submissions for the applicable accounts.

Daily limits reset at midnight in `DJANGO_TIME_ZONE`, which defaults to `America/Guayaquil`. A submission counts when Django attempts to send it to OmniRoute. An explicit retry is a new submission. Repeated delivery of the same browser form uses the original submission ID and reopens its conversation.

## Provision

Run `python scripts/provision.py` from the checkout on the Docker Compose host. Use `python3` where appropriate. The script starts the pinned OmniRoute image on the Compose network without a published port, starts Django on host loopback, and stores gateway and SQLite state in separate volumes.

The script accepts `OPENROUTER_API_KEY`, `GROQ_API_KEY`, and `GEMINI_API_KEY` from the process environment or an installer-only `.env` file. Use `--provider-env-file` for another file path or `--provider-env-file -` to read assignments from standard input. Provider keys go directly to OmniRoute. OpenRouter's `google/gemma-4-26b-a4b-it:free` is the initial route candidate. The script reports whether that canonical route appears in the gateway catalog and leaves live verification pending if it cannot resolve it.

The installer generates local deployment secrets, including the recovery password, under `.secrets` by default. Use `--secrets-dir` to choose another private directory. It provisions distinct inference and management credentials, applies migrations, and reports the effective Django route. Reruns keep persistent data, existing provider connections, and the administrator's saved default. A provider outage or missing provider key leaves the services available.

For an already provisioned installation, run `python scripts/deploy.py` from the checkout to rebuild and restart Django and the scheduler. The script uses `.secrets/omniroute_default_route` unless `CHAT_MODEL` is set and keeps the existing Compose volumes. It waits until Django responds and both services are running. For a non-default gateway set `OMNIROUTE_URL`; set `CSV_SPOOL_DIRECTORY` before running it if the installation uses the CSV spool overlay.

Run `python scripts/deploy.py --watch` to deploy once and then redeploy whenever the application, tests, Dockerfile, requirements, or Compose files change. Stop the watcher with Ctrl+C. Every deployment restarts Django and can interrupt active replies; use this mode when that behavior is appropriate.

### Isolated CSV summaries

On a Linux Docker host, create a dedicated spool directory writable by Django's UID 10001 with access limited to that UID and the host operator. Set `CSV_SPOOL_DIRECTORY` to its absolute path before running the provisioner, or add `-f compose.csv.yaml` to a manual Django Compose invocation. Build the worker with `docker build --target csv-worker -t superharry-csv-worker:local .`.

Run `python3 scripts/run_csv_jobs.py --spool "$CSV_SPOOL_DIRECTORY"` as a host operator with Docker access. Django queues only owner-approved CSV files. The processor launches a disposable unprivileged container per job with no network, no host mounts, and bounded resources. The owner can reopen the job page to view and download the result.

Run `python -m tests.csv_runner_smoke` with Docker access on the test host. The smoke test applies the [runtime policy](runtime-policy.md), then exercises network denial, read-only execution, resource bounds, and disposable cleanup. The policy uses conservative minimums of runc `1.4.3` and containerd `2.3.6` and explicitly rejects the affected containerd `2.4.0`. Record Docker Desktop results independently from the historical VM results.

### Isolated skill preparation and execution

On a Docker host, use a dedicated skill spool directory accessible only to the application container and the host operator. On Linux, allow Django's UID 10001 to access it; on Windows, restrict the directory using Windows access controls. The spool contains skill source, dependency wheels, inputs, and results; do not share it with other users or services. Set `SKILL_SPOOL_DIRECTORY` to its absolute path; the provisioner and deployment helper include the skill overlay and build the disposable worker image when it is set. For manual Compose commands add `-f compose.skill.yaml` and build the worker with `docker build --target skill-worker -t superharry-skill-worker:local .`. Run `python3 scripts/run_skill_jobs.py --spool "$SKILL_SPOOL_DIRECTORY"` on Linux, or `python scripts/run_skill_jobs.py --spool $env:SKILL_SPOOL_DIRECTORY` in PowerShell, as a host operator with Docker access. Django itself does not receive the Docker socket.

Owners or administrators can select a Python entrypoint and optionally supply offline Python wheel files on an application-managed skill's details page. The current interface labels these local definitions “VM skills”; no separate VM is required. Dependency declarations accept package names and exact `==` versions. When no wheels are supplied, preparation can retrieve binary wheels and transitive dependencies from PyPI through the host mediator without an installation approval. When supplying wheels, include all transitive dependencies; preparation stays offline. It retains the selected wheels in the restricted spool so each subsequent execution uses that prepared set offline. A failed preparation can be retried; if the retained wheels are missing, prepare the skill again. Prepared skills can be run by signed-in users, with output visible only to the user who submitted that run. Changing a skill requires a new preparation.

The sandbox itself remains network-isolated. A skill can call `from skill_network_client import request`, then `request(method, url, body, headers)` for a UTF-8 response; use `request_bytes` for a binary response. The body and headers are optional. Supported methods are GET, POST, PUT, PATCH, and DELETE, the body is UTF-8 text, and header values are printable ASCII. Exact, operator-registered public resources can be fetched with an empty GET without custom headers or user approval; the default is `https://docs.python.org/3/`. The operator may add a separately verified exact HTTPS URL by repeating `--public-resource <URL>` when starting the host worker. Register only fixed public URLs that carry no user data and have no external effect. All other requests, including GET requests with an unknown effect or data in the URL, require the run owner's approval on the run result page. The page shows the method, destination, headers, and data before a single-use approval or denial; it refreshes while waiting for a request. The worker waits up to two minutes for a decision. Rejection, timeout, interruption, invalid traffic, private or special destination addresses, and redirects fail closed; interrupted work is not automatically resubmitted.

For now, the application rejects edits and deletion when it detects an active job. Restarting runs on update and stopping them on deletion are tracked separately; this interim check is not a substitute for that lifecycle work.

The scheduler retries committed skill jobs whose delivery has not started. If publication was interrupted after it was claimed and no queue or result file proves its outcome, the application displays the job UUID and reports delivery as uncertain. It does not automatically publish that job again. Allow an active publisher to finish first. An operator can then verify Docker is reachable, inspect `docker ps -a --format '{{.Names}}'` for `superharry-skill-` followed by the UUID without hyphens, and inspect the dedicated spool for the same UUID. If the container is present, or the outcome cannot be established, leave the job unresolved. Once the operator confirms the container is absent, run the following in the configured Django container, replacing the UUID with the one displayed:

```text
python -m django recover_skill_dispatch --mark-uncertain-failed <UUID> --confirm-no-running-container
```

The command refuses to resolve a queued or running job, coordinates with any publisher through the database, and preserves an existing result. When no result exists it records a failure; it never reexecutes the uncertain job. A user may explicitly submit a new preparation or run after reviewing that outcome. The flag is an operator assertion, not an automatic Docker inspection.

On a Docker test host, build the worker and run `python3 -m tests.skill_sandbox_smoke` and `python3 -m tests.skill_preparation_smoke`. For isolated network tests, build a separate image with `docker build --target skill-worker -t superharry-skill-network-test:local .`, run `python -m tests.skill_network_sandbox_smoke`, and set `SKILL_NETWORK_DOCKER_SMOKE=1` when running `python -m django test tests.test_skill_network_docker` with test Django settings. On Docker Desktop, `python -m tests.skill_desktop_smoke` additionally checks the shared spool between a disposable Django container and the host worker. These use synthetic inputs and a disposable Django database. The preparation, network integration, and Desktop smoke tests enforce the worker's runtime gate by default. For functional diagnosis only, setting `SKILL_SMOKE_UNSUPPORTED_RUNTIME=1` bypasses that gate inside the test process; it does not change the operational worker or establish isolation acceptance. Dedicated Linux VM acceptance remains tracked in #28.

The host skill worker applies the same [runtime policy](runtime-policy.md): conservative minimums of runc `1.4.3` and containerd `2.3.6`, with an explicit rejection of the affected containerd `2.4.0`. A runtime failure leaves queued jobs unprocessed; inspect the installed Docker runtime before restarting the worker.

## Verify

1. Run the Docker test suite with its controlled gateway fixture.
2. Against the selected deployment, sign in as a regular user and an administrator. Verify conversation persistence after restarting Django and change the default through Django admin. Confirm that the inference credential cannot call management endpoints.
3. Run `docker exec <django-container> python -m tests.live_chat_smoke` against the provisioned deployment, substituting the actual Django container name. It signs in over HTTP using the mounted recovery secret and sends a synthetic prompt only when the chat shows the seeded OpenRouter free route. Exit status `0` means the streamed text appeared as the assistant reply in the reopened conversation. Exit status `2` records an unavailable route and a saved incomplete reply. Keep live completion pending in that case.
4. Rerun provisioning after an administrator changes the default and verify that the configured route and persistent state survive. Run sandbox checks with isolated test data on the current Docker host. Record the separate acceptance gate in [milestones](milestones.md#3-skills-and-bundled-execution) as pending unless it is explicitly revised and validated.

See [milestones and acceptance](milestones.md) for the complete checks and [architecture](architecture.md) for role and credential boundaries.
