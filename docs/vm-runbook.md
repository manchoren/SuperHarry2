# VM and Docker runbook

## Start the Django recovery service

Put a random Django signing key and an initial recovery password in separate UTF-8 text files. The default paths are `.secrets/django_secret_key` and `.secrets/recovery_password`. Keep the directory private. On Linux, make the files readable by the container user (UID 10001). Set `DJANGO_SECRET_KEY_PATH` and `RECOVERY_PASSWORD_PATH` if the files are elsewhere.

Run `docker compose up --build -d` and open `http://127.0.0.1:8000/admin/` on the Docker host. The initial username is `recovery`. Set `SUPERHARRY_PORT` to use a different host port. For a remote Linux VM, forward the guest loopback port over SSH.

Django applies migrations and creates the recovery account on first start. Compose stores SQLite in the `django_data` volume. Restarting Django preserves the account and its current password. Keep the signing key stable across restarts.

Run `python -m tests.compose_smoke` on a host with Docker Compose and Python to verify login, static assets, the closed registration URL, and persistence across a restart. The test uses disposable credentials, a random localhost port, and an isolated Compose project.

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
