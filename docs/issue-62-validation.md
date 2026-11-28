# Issue #62 validation: account deletion

## Tested worktree

- Base commit: `e0ae6fe5decc8cf15e916f5c1ce0c94f14016c7c`.
- The issue #62 implementation and related account-management changes were uncommitted in the shared worktree during validation.
- Runtime: Docker Desktop on Windows. No deployment or production validation was performed.

## Acceptance evidence

| Acceptance criterion | Evidence |
| --- | --- |
| Explain deletion consequences, including shared local skills, and require exact username confirmation. | Deletion request tests verify shared-skill consequences, reject a mismatched username, and preserve the account. The Playwright account-management smoke completes the confirmation flow. |
| Stop in-flight work and remove the account's data and stored files while preserving other users' and repository skills. | Deletion tests cover pending chat, skill preparation, owned runs, and another user's run of an owned shared skill. A race regression verifies a new shared run is rejected after deletion begins. Shared user input, result, and artifact remain; private skill files and wheels are cleared. |
| Retain audit identity/classification without passwords or private content, and do not claim completion after a cleanup failure. | Tests verify account and file audit rows survive with identity snapshots, role flags stay unchanged, file names are scrubbed, and a partial spool unlink failure leaves the account inactive and pending. Retrying completes deletion. Migration `0041_account_deletion_and_audit_snapshots.py` backfills identity snapshots. |
| Verify authorization, confirmation, active work, storage cleanup, shared skills, and retained audit behavior. | Focused account deletion/admin tests passed **18/18**. Account, lifecycle, management, password, and admin tests passed **40/40**. Standalone `SkillCatalogTests` passed **48/48**. The updated file-history pagination test passed **1/1**. Playwright account-management/deletion smoke passed. |

Focused Docker command:

```powershell
docker compose run --build --no-deps --rm -e DJANGO_DATABASE_PATH=/tmp/superharry-62-deletion-race.sqlite3 django sh -ec 'python -m django check && python -m django test tests.test_account_deletion tests.test_account_admin'
```

Playwright smoke command:

```powershell
$env:UI_BROWSER_PATH='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
.venv\Scripts\python.exe -m tests.ui_account_management_smoke
```

## Checks

- Django system check: passed.
- `makemigrations --check --dry-run`: passed, no model changes detected.
- Ruff on the changed account/deletion/admin/file-history/skill files and relevant tests: passed.
- `git diff --check`: passed; only CRLF conversion notices were reported.

Ruff command:

```powershell
.venv\Scripts\ruff.exe check superharry\account_deletion.py superharry\account_lifecycle.py superharry\accounts.py superharry\admin.py superharry\chat.py superharry\file_history.py superharry\models.py superharry\skill_views.py superharry\urls.py superharry\migrations\0041_account_deletion_and_audit_snapshots.py tests\test_account_deletion.py tests\test_account_admin.py tests\test_file_history.py
```

The combined Docker run discovered 185 tests and ran 137 before `SkillCatalogTests.setUpClass` failed while restoring Django's serialized rollback fixture. The error was `UNIQUE constraint failed: django_content_type.app_label, django_content_type.model` during fixture deserialization. There were no other failures reported. The same 48 skill catalog tests pass in isolation. This combined-suite setup error remains recorded separately from the passing acceptance suites.

## Migration 0041 startup regression follow-up

On 2026-11-27, an existing `FileAccessAudit` row exposed that migration `0041_account_deletion_and_audit_snapshots` called `get_username()` on historical user models. Django's historical model contains the stored `username` field but not that model method. Migration 0041 now reads the historical `username` field directly for account-audit actors and targets and file-audit actors and owners.

The new regression test builds migration state `0041`, supplies pre-existing `AccountAudit` and `FileAccessAudit` rows with actor/target/owner users, calls the migration backfill, and verifies all identity and privilege snapshots. It reproduced the exact `AttributeError` before the fix and passes after it:

```powershell
$env:PYTHONPATH='.'
$env:DJANGO_SETTINGS_MODULE='superharry.settings'
$env:DJANGO_SECRET_KEY='migration-regression-tests'
.venv\Scripts\python.exe -m django test tests.test_account_audit.FileAccessAuditIdentityBackfillMigrationTests -v 1
```

Result: **1 test passed**. The related account-audit, deletion, and lifecycle suites passed **25/25** tests; Ruff passed for migration 0041 and `tests/test_account_audit.py`.

An isolated Docker Desktop startup probe seeded a temporary database at migration 0040 with an existing file-access audit record, ran the Django migration CLI through 0041 and 0042, ran `bootstrap_recovery` and `recover_chat_attempts`, started Gunicorn, and received HTTP 200 from `/login/`. The container used `--network=none`, a scratch database under `/tmp`, and no mounted application data. This reproduces the migration and readiness path without running `scripts/deploy.py` or modifying the configured application database.
