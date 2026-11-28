# Issue #64 validation: account audit history

## Tested worktree

- Base commit: `e0ae6fe5decc8cf15e916f5c1ce0c94f14016c7c`.
- Issue #64 changes were uncommitted in the shared worktree during validation.
- Runtime: Docker Desktop on Windows. No deployment or production validation was performed.

## Acceptance evidence

| Acceptance criterion | Evidence |
| --- | --- |
| Show actor, target, action, time, and target classification without credentials or private content; retain identity after deletion. | The application page renders only actor/target username snapshots, target role, action label, and timestamp. Tests verify actor and target snapshots remain after deletion and ensure no reset password is rendered. |
| Superusers see all events; delegates see regular-target events only. Promotion hides prior events; deletion retains final classification. | The view filters delegates server-side by `target_is_privileged=False`. Tests verify promotion hides prior events, demotion restores regular visibility, deleted promoted accounts remain classified as administrators, and migration 0042 backfills current/final classification. |
| Verify lifecycle events, authorization, promotion, deletion, and responsive display. | The focused combined Docker suite passed **70/70 tests**, including all six account action types and role/deletion/migration cases. Playwright smoke passed at desktop and 375px; the audit table displayed readable labeled cards with no horizontal overflow. |
| Update architecture documentation. | `docs/architecture.md` describes the integrated account interface and retained Django Admin responsibilities. |

Combined Docker command:

```powershell
docker compose run --build --no-deps --rm -e DJANGO_DATABASE_PATH=/tmp/superharry-64-acceptance.sqlite3 django sh -ec 'python -m django check && python -m django makemigrations --check --dry-run && python -m django test --parallel 1 tests.test_account_audit tests.test_account_admin tests.test_account_management tests.test_account_lifecycle tests.test_account_deletion tests.test_account_passwords tests.test_file_history'
```

Playwright smoke command:

```powershell
$env:UI_BROWSER_PATH='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
.venv\Scripts\python.exe -m tests.ui_account_management_smoke
```

Ruff command:

```powershell
.venv\Scripts\ruff.exe check --output-format concise superharry\accounts.py superharry\models.py superharry\migrations\0042_account_audit_final_classification.py tests\test_account_audit.py tests\ui_account_management_smoke.py
```

- Django system check: passed.
- Migration check: passed, no model changes detected.
- `git diff --check`: passed; only existing LF-to-CRLF working-copy warnings were reported.
