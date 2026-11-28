# Issues #71 and #72 validation

## Scope

The shared implementation replaces the two main navigation entries with one
**Folders** entry that opens Inputs. Inputs and Files are tabs on each list and
detail view. Existing routes, content, ownership checks, administrative file
history controls, and file actions remain in place; this does not add a storage
folder model.

## Evidence

| Acceptance area | Evidence |
| --- | --- |
| One Folders entry opens Inputs; Inputs/Files switch and show the active section | PowerShell: `$env:UI_BROWSER_PATH='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'; .\.venv\Scripts\python.exe -m tests.ui_folders_navigation_smoke` — passed in Chromium. Covers the single entry, default route, tab switching, active state, keyboard focus, and tab switching from both input and generated-file details. |
| Direct list/detail URLs and narrow viewport | Same browser smoke — passed for `/inputs/`, `/files/`, `/inputs/<id>/`, and `/files/artifact/<id>/` at desktop and 375px viewport widths. |
| Input and file ownership, administrator access, audit, and existing history behavior | PowerShell: `$env:DJANGO_SETTINGS_MODULE='superharry.settings'; $env:DJANGO_SECRET_KEY='synthetic-test-key-do-not-use'; $env:DJANGO_DATABASE_PATH=(Join-Path $env:TEMP 'superharry-issues-71-72.sqlite3'); .\.venv\Scripts\python.exe -m django test tests.test_file_history` — 12 tests passed. |
| Upload, viewing, download, deletion, filtering, pagination/theme layout, and administrative audit browser flow | PowerShell with the same `UI_BROWSER_PATH` as above: `.\.venv\Scripts\python.exe -m tests.ui_file_preview_smoke` — passed in Chromium with synthetic files and users. Existing history actions are buttons/forms. |
| Static checks | Ruff on changed Python files and `git diff --check` passed. |

These are local synthetic browser and Django checks. They do not claim a
deployment or production acceptance result.
