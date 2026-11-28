# Issue #63 validation: administrator account management

## Tested worktree

- Base commit: `e0ae6fe5decc8cf15e916f5c1ce0c94f14016c7c`.
- Issue #63 changes were uncommitted in the shared worktree during validation.
- Runtime: Docker Desktop on Windows. No deployment or production validation was performed.

## Acceptance evidence

| Acceptance criterion | Evidence |
| --- | --- |
| Superusers manage non-superuser administrator accounts, assign/remove the existing Administrators group, and use lifecycle/password workflows; delegates cannot manage privileged accounts or grant privileges. | Tests cover administrator creation, promotion/demotion, password reset through the integrated flow, deactivate/reactivate, and delete. Delegated direct GET/POST attempts against administrator and superuser targets return 404; privilege-field POSTs return 403. |
| Keep individual permissions and superuser account changes in Django Admin; prevent integrated self-targeting and preserve a last active admin-capable superuser. | Integrated target queries exclude every superuser. Direct Django Admin POST tests reject removing `is_superuser`, `is_active`, or `is_staff` from the last active admin-capable superuser, and permit demotion when another such superuser remains. Native Django Admin superuser password reset preserves the default-on password-change policy, invalidates the target session, and records a password-free audit event. |
| Test promotion, demotion, activation, deletion, direct-request authorization, and recovery protections. | The focused Docker account/admin/lifecycle/deletion/password suite passed **52/52**. Tests also verify audit target classification updates on role changes. |

## Checks

```powershell
docker compose run --build --no-deps --rm -e DJANGO_DATABASE_PATH=/tmp/superharry-63-accounts.sqlite3 django sh -ec 'python -m django check && python -m django makemigrations --check --dry-run && python -m django test --parallel 1 tests.test_account_admin tests.test_account_management tests.test_account_lifecycle tests.test_account_deletion tests.test_account_passwords'
```

- Django system check: passed.
- Migration check: passed, no model changes detected.
- Focused test suite: **52/52 passed**.
- Ruff on the nine affected Python modules and test modules: passed.
- `git diff --check`: passed; only CRLF conversion notices were reported.
