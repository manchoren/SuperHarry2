# VM and Docker runbook

## Start the Django recovery service

Put a random Django signing key and an initial recovery password in separate UTF-8 text files. The default paths are `.secrets/django_secret_key` and `.secrets/recovery_password`. Keep the directory private. On Linux, make the files readable by the container user (UID 10001). Set `DJANGO_SECRET_KEY_PATH` and `RECOVERY_PASSWORD_PATH` if the files are elsewhere.

Run `docker compose up --build -d` and open `http://127.0.0.1:8000/admin/` on the Docker host. The initial username is `recovery`. Set `SUPERHARRY_PORT` to use a different host port. For a remote Linux VM, forward the guest loopback port over SSH.

Django applies migrations and creates the recovery account on first start. Compose stores SQLite in the `django_data` volume. Restarting Django preserves the account and its current password. Keep the signing key stable across restarts.

Run `python -m tests.compose_smoke` on a host with Docker Compose and Python to verify login, static assets, the closed registration URL, and persistence across a restart. The test uses disposable credentials, a random localhost port, and an isolated Compose project.

## Connect chat to an existing OmniRoute gateway

Set `OMNIROUTE_URL` to the gateway base URL reachable from Django's container and `CHAT_MODEL` to the canonical route ID shown by that gateway. Put the inference key in `.secrets/omniroute_api_key` or set `OMNIROUTE_API_KEY_PATH` to its file path. Use an inference credential.

Run `docker compose -f compose.yaml -f compose.omniroute.yaml up --build -d` and visit `http://127.0.0.1:8000/`. Sign in with an account created in Django admin. Run the commands from the same Compose project directory so both startup modes use the same SQLite volume.

To enable model-entry administration, create a separate OmniRoute Access Token with `write` scope and save it in `.secrets/omniroute_management_token`. Set `OMNIROUTE_MANAGEMENT_TOKEN_PATH` if the file is elsewhere. Keep the inference key free of management permissions. Add `-f compose.omniroute-management.yaml` after the other Compose files when starting Django.

Open **Chat routes** in Django admin to list routes visible to the inference key, test a route with a short inference request, and set the saved default used by chat. The saved selection overrides `CHAT_MODEL`. Add or update model entries by selecting an existing provider connection. OmniRoute stores these entries at provider level. Keep provider-account management, upstream credentials, OAuth, and fallback configuration in OmniRoute. Keep paid fallback disabled there.

In Django admin, **Chat limits** controls the global daily submission allowance and maximum number of characters per reply. **User chat limits** overrides either value for an individual account. A blank override inherits the global value. A daily allowance of zero disables chat submissions for the applicable accounts.

Daily limits reset at midnight in `DJANGO_TIME_ZONE`, which defaults to `America/Guayaquil`. A submission counts when Django attempts to send it to OmniRoute. An explicit retry is a new submission. Repeated delivery of the same browser form uses the original submission ID and reopens its conversation.

## Provision

Run `python scripts/provision.py` from the checkout on a Docker Compose host or in the dedicated Linux VM. Use `python3` where appropriate. The script starts the pinned OmniRoute image on the Compose network without a published port, starts Django on host loopback, and stores gateway and SQLite state in separate volumes.

The script accepts `OPENROUTER_API_KEY`, `GROQ_API_KEY`, and `GEMINI_API_KEY` from the process environment or an installer-only `.env` file. Use `--provider-env-file` for another file path or `--provider-env-file -` to read assignments from standard input. Provider keys go directly to OmniRoute. OpenRouter's `google/gemma-4-26b-a4b-it:free` is the initial route candidate. The script reports whether that canonical route appears in the gateway catalog and leaves live verification pending if it cannot resolve it.

The installer generates local deployment secrets, including the recovery password, under `.secrets` by default. Use `--secrets-dir` to choose another private directory. It provisions distinct inference and management credentials, applies migrations, and reports the effective Django route. Reruns keep persistent data, existing provider connections, and the administrator's saved default. A provider outage or missing provider key leaves the services available.

### Isolated CSV summaries

On a Linux Docker host, create a dedicated spool directory writable by Django's UID 10001 with access limited to that UID and the host operator. Set `CSV_SPOOL_DIRECTORY` to its absolute path before running the provisioner, or add `-f compose.csv.yaml` to a manual Django Compose invocation. Build the worker with `docker build --target csv-worker -t superharry-csv-worker:local .`.

Run `python3 scripts/run_csv_jobs.py --spool "$CSV_SPOOL_DIRECTORY"` as a host operator with Docker access. Django queues only owner-approved CSV files. The processor launches a disposable unprivileged container per job with no network, no host mounts, and bounded resources. The owner can reopen the job page to view and download the result.

## Verify

1. Run the Docker test suite with its controlled gateway fixture.
2. In the guest, sign in as a regular user and an administrator. Verify conversation persistence after restarting Django and change the default through Django admin. Confirm that the inference credential cannot call management endpoints.
3. Run a separate live streaming chat against the seeded OpenRouter Gemma free route through OmniRoute. Record the configured route, result, and any sanitized error. Verify that an unavailable free route surfaces a user-visible error without switching to a paid route.
4. Rerun provisioning after an administrator changes the default and verify that the configured route and persistent state survive. Before releasing bundled execution, run the sandbox checks in the dedicated Linux guest.

See [milestones and acceptance](milestones.md) for the complete checks and [architecture](architecture.md) for role and credential boundaries.
