# Issue #58 validation: integrated account management

## Tested worktree

- Base commit: `e0ae6fe5decc8cf15e916f5c1ce0c94f14016c7c`.
- Issues #59–#64 were implemented and validated in the uncommitted shared worktree, then individually reviewed, evidenced, and closed.
- Runtime: Docker Desktop on Windows. No deployment or production validation was performed.

## Parent acceptance summary

| Scope | Evidence |
| --- | --- |
| Application account creation, search/filter, edit, optional profile fields, and responsive list/detail | Playwright creates and edits an account, searches by email, independently filters Active and Inactive accounts, checks the Administrator role filter with a superuser, uses keyboard activation for Create, and checks no horizontal overflow at 375px on list and detail pages. |
| Password validation, secrecy, required change, reset and session invalidation | Account and admin tests cover existing password validation, default-on/optional must-change, reset, session invalidation, access restriction until password change, and audit without credentials. Playwright completes create/reset/required-change/change flows. |
| Deactivation/reactivation, stop and recovery behavior | #61 evidence in `docs/issue-61-validation.md`; account lifecycle tests cover direct Django Admin bypasses, in-flight work, schedules, pause/resume, and skipped missed occurrences. #63 tests protect the final active admin-capable superuser. |
| Permanent deletion and audit retention | #62 evidence in `docs/issue-62-validation.md`; exact username confirmation, shared-skill cancellation, partial storage cleanup retry, preservation of other users' results, and audit identity/classification are covered. |
| Administrator delegation and audit history | #63 evidence in `docs/issue-63-validation.md`; #64 evidence in `docs/issue-64-validation.md`. Delegated admins are limited to regular accounts, superusers manage delegated administrators, and audit visibility follows the target's final classification. |
| Architecture and UI scope | `docs/architecture.md` describes the integrated Accounts interface and the retained Django Admin responsibilities. The account UI uses individual actions and has no bulk controls. |

## Combined validation

The combined serial Docker account suite passed **70/70** tests:

```powershell
docker compose run --build --no-deps --rm -e DJANGO_DATABASE_PATH=/tmp/superharry-64-acceptance.sqlite3 django sh -ec 'python -m django check && python -m django makemigrations --check --dry-run && python -m django test --parallel 1 tests.test_account_audit tests.test_account_admin tests.test_account_management tests.test_account_lifecycle tests.test_account_deletion tests.test_account_passwords tests.test_file_history'
```

The final Playwright smoke command passed after adding independent status-filter and keyboard checks:

```powershell
$env:UI_BROWSER_PATH='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
.venv\Scripts\python.exe -m tests.ui_account_management_smoke
```

At 375px it verifies account list, account detail, and audit page overflow; audit rows are readable cards. It also verifies the status and role filters change results, email search submits by Enter, and Create opens by keyboard activation.

Ruff passed for affected files. `git diff --check` exited 0 and reported only shared-worktree LF-to-CRLF warnings. The independent review found no remaining #58 acceptance gaps.

The earlier #62 broad 185-test run reached 137 tests before `SkillCatalogTests.setUpClass` failed during Django serialized rollback with a duplicate `django_content_type` key. The standalone skill catalog suite passed 48/48; the final 70-test account acceptance suite passed. This broader test-harness limitation is recorded in `docs/issue-62-validation.md`.
