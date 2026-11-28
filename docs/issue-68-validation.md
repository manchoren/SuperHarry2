# Issue #68 browser session validation

## Implemented scope

The approved browser run now keeps one Chromium browser, context, and page alive for a bounded session. The page answer and citations are published as soon as navigation and extraction finish; the session then remains available for the owner to inspect or request one screenshot. Reaching the session TTL or ending it after publication does not replace the successful page result with a timeout or cancellation result.

The task page places the owner's conversation beside the browser panel on desktop. The conversation frame uses a task-scoped route that checks the run owner and serves only that owner's conversation with a same-origin frame policy. The browser panel receives a JPEG from an owner-only, no-store endpoint; it never receives remote page HTML. The labeled hide/reopen button includes accessible eye and eye-off icons; toggling it changes only the panel's display state and keeps the same run and image source. On mobile, the panel fills the viewport and provides a return-to-conversation button. Returning to chat does not cancel the run.

Screenshot requests are POST-only and use Django CSRF protection. They are scoped to the run owner, job ID, and a deterministic command ID. A bounded command expiry and a completion marker make retries idempotent. Captured JPEG bytes use the existing bounded artifact spool and `ArtifactDelivery` import/download path. The artifact status endpoint imports a complete atomic spool while the session is active, so the owner can access the screenshot without waiting for the browser TTL.

The live view and command files, consent, worker container, and running marker are cleaned up when the session exits, reaches its bounded TTL, is cancelled, or is recovered after interruption. Early page text is marked untrusted in the assistant response. Screenshot bytes and DOM snapshots are not passed to the model.

## Validation evidence

The following checks ran against temporary SQLite databases and temporary spool directories; no project database or user account was used.

| Check | Result |
| --- | --- |
| `tests.test_browser_session_panel` | 3 tests passed: owner-only JPEG, screenshot POST/CSRF/idempotency, active artifact import, cancel after early result, and same-origin owner-scoped conversation frame. |
| `tests.ui_browser_session_smoke.py` | Passed in Chromium: desktop conversation plus image panel, accessible eye/eye-off controls, hide/reopen preserves image URL and running job, mobile panel fills 390 x 844 viewport, return button opens the conversation and leaves the job active. |
| `tests.playwright_browser_session_smoke` | Passed against the Docker browser image: page result is published before the requested capture, and JPEG bytes return through the mediated command path. The #66 chat-to-Docker test separately proves citations are available with no screenshot request. |
| `tests.test_browser_session_docker` | 1 Docker integration test passed: `process_queue` starts the persistent Chromium session, owner requests a screenshot, an owner-only `ArtifactDelivery` becomes downloadable while the session is active, owner cancellation cleans up, and the successful early answer remains complete. |

Commands used:

```powershell
$env:DJANGO_SECRET_KEY = 'local-test-only'
$env:DJANGO_SETTINGS_MODULE = 'superharry.settings'
.venv/Scripts/python.exe -m django test tests.test_browser_session_panel -v 1
.venv/Scripts/python.exe tests/ui_browser_session_smoke.py

docker build --target skill-browser-worker -t superharry-skill-browser-worker:issue-68-local .
$env:SKILL_BROWSER_TEST_IMAGE = 'superharry-skill-browser-worker:issue-68-local'
.venv/Scripts/python.exe -m tests.playwright_browser_session_smoke
.venv/Scripts/python.exe -m django test tests.test_browser_session_docker -v 2
```

Issue #68 is now unblocked by the closure of issue #66. These checks establish the local Docker implementation and owner-facing UI behavior; they do not establish deployment or production acceptance.
