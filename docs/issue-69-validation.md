# Issue #69 validation

## Result and boundaries

The owner can opt into a separate manual sign-in handoff for a single browser
task. The browser pauses its page-reading work while the owner uses the
owner-only control panel, and resumes only after Return. Initial handoff pages,
all handoff page/resource requests, and every redirect must use HTTPS. The
mediator repeats the HTTPS check. Only a bounded, recognizable native
username/password form POST following an owner click or Enter is accepted.

Typed values, handoff commands, live JPEGs, and cookies remain in process RAM
and the temporary BrowserContext; they are not sent to the model or written to
the spool, database, logs, or artifacts. Without separate continuation consent,
authenticated page text is not sent to the model; with consent, only the sanitized
visible page excerpt and original request are sent for one response. Screenshot and artifact endpoints
reject every task with handoff enabled, including direct requests. Return,
cancel, failure, timeout, and expiry clear the handoff state and close the
browser context/container.

This does not support JavaScript-based sign-in, SPA flows, MFA/one-time codes,
arbitrary forms, payment/card activity, or redirected subresources during
control. Model-prepared native form actions are a separate bounded #70 flow;
the owner must explicitly request/approve the task, enable HTTPS handoff, and
approve the exact proposal. The browser returns sanitized page text and citations after Return. An
additional unchecked consent allows that visible page excerpt and the original
request to reach the selected route only after the owner returns control and the
worker confirms cleanup, for one continuation response. Without it,
page text stays out of model input. The continuation excludes credentials,
cookies, screenshots, and values typed during sign-in, and does not resume
browser tools. The bridge rejects synthesis before the return and cleanup flags
are recorded. Only synthetic local fixtures were used.

## Evidence

| Acceptance area | Evidence |
| --- | --- |
| HTTPS login, native POST, redirect, host-only and Domain cookies, Return, cleanup, useful result and redaction | PowerShell: `$env:PYTHONPATH='.'; $env:SKILL_BROWSER_TEST_IMAGE='superharry-skill-browser-worker:local'; .\.venv\Scripts\python.exe tests/playwright_browser_session_smoke.py` — passed on the rebuilt Docker Desktop image. The fixture verifies three cookies in the authenticated GET, including `Domain=fixture.example; Path=/private`, and checks that typed/cookie sentinels do not reach the published reply. |
| HTTP initial URL rejection and HTTPS-to-HTTP redirect rejection | Same Docker session smoke — HTTP initial URL made no mediator request; redirect fixture recorded the HTTPS login POST and cleanup, with no request to the HTTP destination. |
| Owner isolation, CSRF, in-memory loopback bridge, bounded headers/events, expiry, cancel, cleanup, direct screenshot/artifact restrictions | PowerShell: `$env:PYTHONPATH='.'; $env:DJANGO_SETTINGS_MODULE='superharry.settings'; $env:DJANGO_SECRET_KEY='synthetic-browser-handoff-tests'; .\.venv\Scripts\python.exe -m django test tests.test_browser_handoff_bridge tests.test_browser_session_panel tests.test_skill_sandbox_cleanup tests.test_browser_handoff tests.test_browser_policy tests.test_skill_network -v 1` — 40 tests passed. The bridge test uses a random loopback HTTP port and synthetic values. |
| Desktop/mobile browser panel and preserved session | PowerShell: `$env:UI_BROWSER_PATH='C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'; .\.venv\Scripts\python.exe -m tests.ui_browser_session_smoke` — passed; checked chat beside panel, hide/reopen, 390x844 full-screen panel, return to conversation, and task still active. |
| #66 GET-only compatibility in the same browser image | `$env:PYTHONPATH='.'; $env:SKILL_BROWSER_TEST_IMAGE='superharry-skill-browser-worker:local'; .\.venv\Scripts\python.exe tests/playwright_browser_smoke.py` — passed, including direct-egress denial, mediated resources, redirects, citations, private-address blocking, timeout, and cancellation. |
| Static checks | Ruff on the changed Python files, `node --check` on `superharry/skills/playwright/session.mjs`, and `git diff --check` passed. Git reported only existing LF-to-CRLF working-copy warnings. |

Build command:

```powershell
docker build --target skill-browser-worker -t superharry-skill-browser-worker:local .
```

These are Docker Desktop and local synthetic-fixture results. They do not
establish deployment or production acceptance and do not prove behavior for
unsupported site-specific authentication flows.
