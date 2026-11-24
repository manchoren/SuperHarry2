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

The following sections specify the full deployment workflow, including the OmniRoute installer and later milestone checks.

## Provision

1. Run the same Compose application and test setup used on the Windows development machine in a dedicated Linux guest. Use the pinned Debian Sid base for Django and a tested OmniRoute version. Attach persistent volumes for Django's SQLite database and OmniRoute state.
2. Have the VM creation script install and start OmniRoute on a private service network. Bind the Django app to localhost for owner access. Keep the management interface private.
3. Supply the Nous Portal API key as an installer secret. Have the installer configure the upstream connection directly in OmniRoute and seed the `upstage/solar-mini4:free` route as the initial default. Verify the canonical route ID against the installed gateway.
4. Have the installer provision separate inference and management credentials and deliver them to Django through deployment secrets. Do not print credentials or include them in source control, browser responses, or runner jobs.
5. Apply Django migrations, bootstrap the recovery superuser, and start Django. On reruns, create or repair missing configuration, preserve existing routes and administrator choices, and report the active default. A live provider outage does not prevent Django and OmniRoute from starting.

## Verify

1. Run the Docker test suite with its controlled gateway fixture.
2. In the guest, sign in as a regular user and an administrator. Verify conversation persistence after restarting Django and change the default through Django admin. Confirm that the inference credential cannot call management endpoints.
3. Run a separate live streaming chat against the seeded free route through OmniRoute. Record the configured route, result, and any sanitized error. Verify that an unavailable free route surfaces a user-visible error without switching to a paid route.
4. Rerun provisioning after an administrator changes the default and verify that the configured route and persistent state survive. Before releasing bundled execution, run the sandbox checks in the dedicated Linux guest.

See [milestones and acceptance](milestones.md) for the complete checks and [architecture](architecture.md) for role and credential boundaries.
