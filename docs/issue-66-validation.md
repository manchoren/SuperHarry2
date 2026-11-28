# Issue #66 validation and remaining scope

## Accepted request boundary

The owner approved the initial browser contract as one owner-approved public URL read. The adapter issues only GET requests, disables page JavaScript, does not click or submit forms, blocks service workers and WebSockets, and closes the browser when the task ends. Public servers can still assign side effects to GET requests. The path/query classifier rejects obvious payment, card, account-action, and mutating route names, but it cannot prove that every arbitrary remote GET is side-effect-free. This is an owner-approved scope limitation, not proof that arbitrary server-side effects are prevented.

## Redirect behavior

Main-frame redirects are handled one hop at a time. The adapter validates each `Location`, fulfills the current navigation with an inert page instead of giving Chromium a 3xx response, and starts the next URL through the same route and host mediator. This keeps the final browser URL and relative resource base aligned while rechecking consent, DNS, and the destination for every hop. Loops and chains over ten redirects fail closed.

Redirected subresources remain blocked. A Docker Desktop prototype with stacked Playwright routes and `route.fallback({url})` showed that each changed URL can reach another route handler, but CSS relative assets were still resolved against the original stylesheet URL after a redirect. Serving those bodies would therefore change web URL semantics. The owner explicitly accepted this boundary for #66: main-frame redirects are followed, redirected subresources fail closed. This records the agreed slice; it does not claim subresource redirects are supported.

## Runtime boundary

Chromium's nested sandbox cannot start on the current Docker Desktop Linux host because unprivileged user namespaces are disabled under AppArmor. The browser container is the explicit isolation boundary; it retains network and IPC isolation, 512 MiB memory with no swap, one CPU, read-only root, UID 10001, no capabilities, no-new-privileges, and bounded temporary storage. Chromium needs a 128-process cap: page creation failed at 64 and succeeded at 128. No host or Docker socket, credentials, or session cookies enter the container.

## Controlled validation

Build the dedicated image with Docker Desktop:

```powershell
docker build --target skill-browser-worker -t superharry-skill-browser-worker:local .
```

Run the Docker smoke from the repository root:

```powershell
.venv\Scripts\python.exe -m tests.playwright_browser_smoke
```

The controlled smoke covers direct-egress denial, percent-encoded action URL rejection, two-hop absolute/relative main-frame redirects, final URL citations, relative CSS/image resolution, private subresource and private/file/action redirect denial, redirect loops/limit, request timeout, cancellation, and container cleanup. It uses a synthetic mediator fixture; it does not contact a live provider.

The worker and fixed `main.mjs` adapter now use the same strict request schema, including a finite `execution_timeout` in `(0, 150]` seconds. The smoke passes that field in URL-policy probes and verifies accepted and rejected timeout values. Final integrated Docker run on 2026-11-27 passed without a wrapper or bind mount.

Run policy and timeout-header/body checks:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_browser_policy -v
```

Run chat approval, consent, denial, citations, and the opt-in real Docker worker integration with an in-memory database:

```powershell
$env:DJANGO_SETTINGS_MODULE='superharry.settings'
$env:DJANGO_DATABASE_PATH=':memory:'
$env:DJANGO_SECRET_KEY='browser-test-key-not-for-use'
$env:SKILL_BROWSER_DOCKER_TEST='1'
.venv\Scripts\python.exe -m django test tests.test_chat.ChatTests.test_playwright_run_waits_for_owner_approval_and_revokes_consent_on_cancel tests.test_chat.ChatTests.test_playwright_denial_does_not_dispatch_or_retain_consent tests.test_chat.ChatTests.test_playwright_result_page_renders_only_the_worker_citations tests.test_chat.PlaywrightDockerIntegrationTests.test_approved_chat_task_runs_in_docker_and_publishes_citations --parallel 1 --verbosity 1
```

Run lint and whitespace checks:

```powershell
.venv\Scripts\ruff.exe check superharry\browser_tasks.py scripts\skill_sandbox.py skill_worker.py scripts\skill_network.py scripts\run_skill_jobs.py superharry\chat.py superharry\skill_jobs.py superharry\skill_views.py tests\test_browser_policy.py tests\test_chat.py tests\playwright_browser_smoke.py
git diff --check
```

These checks establish local controlled behavior only. Redirected subresources remain fail-closed under the owner-approved scope, arbitrary GET side effects are outside the provable browser boundary, and deployment/live-provider acceptance is not established here.

## Prepared issue resolution clarification

> Implemented the owner-approved read-only Playwright slice. The owner approves one public page task; the browser sends GET requests only, has JavaScript disabled, and does not click or submit forms. Main-frame redirects are followed through the mediated route with per-hop validation and DNS checks. Redirected subresources are blocked because the tested Playwright fallback path changes relative CSS URL resolution; this fail-closed behavior is accepted for this issue. Obvious payment/card and action URL patterns (including bounded percent-encoded forms) are rejected, but arbitrary remote GET semantics cannot be proven side-effect-free. Docker Desktop smoke, the opt-in Django chat-to-approval-to-worker-to-citation flow, policy tests, Ruff, and diff checks provide controlled evidence. Deployment/live-provider validation is not claimed.
