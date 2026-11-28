# Issue #65 parent acceptance audit

## Overall status

Owner decision (2026-11-27): the payment exclusion is a bounded control over
identifiable payment activity, not a guarantee against effects concealed by a
remote site. Purchases, payments and transfers remain blocked even with owner
approval; enabling confirmed purchases is deferred. This decision supersedes
the earlier claim below that proving a universal exclusion is a closure gate.
For the parent acceptance review, the supported form slice is one native POST
with supported visible fields; unsupported operations stop. Broader form and
multi-step support is outside this closure, not claimed as implemented.

All child issues #66 through #70 are closed. The acceptance matrix below records
the local evidence and supported limits for each story. With separate opt-in,
story 15 resumes read-only browsing in the same temporary authenticated session:
up to five relevant links can be read by mediated GET for one follow-up answer.
Normal browsing excludes page clicks, form submissions and page JavaScript.

The earlier `needs-info` payment blocker was resolved by the owner refinement
and closure of #70. Unknown server-side effects remain an explicit limitation.

All evidence below is local and synthetic unless explicitly stated. It does not establish deployment or production acceptance.

## User story status

| # | Status | Evidence / remaining work |
| --- | --- | --- |
| 1 | Pass | Chat skill selection and launch covered by `docs/issue-66-validation.md` and `docs/issue-67-mwmbl-provider.md`. |
| 2 | Pass | Public URL and question flows have chat-to-worker coverage in the #66/#67 Docker evidence. |
| 3 | Pass within bounded GET-only scope | Main-frame redirects are mediated. Direct URL and search tasks navigate to up to five relevance-ranked public links with GET. The Docker session fixture follows a relevant privacy page, excludes irrelevant and sensitive links, and cites only successful pages; the chat-to-worker integration confirms direct URL tasks receive the bounded-link setting. No page clicks, forms, or JavaScript run. |
| 4 | Pass | Search answer synthesis returns verified source URLs; controlled Docker evidence is in `docs/issue-67-mwmbl-provider.md`. |
| 5 | Pass | Owner-requested screenshot artifact path and panel controls are covered in `docs/issue-68-validation.md`. |
| 6 | Pass within local scope | Mwmbl uses a shared local SQLite limiter; see `docs/issue-67-mwmbl-provider.md`. A point-in-time anonymous live endpoint probe succeeded, but live end-to-end operation, reliability and deployment-wide limiter coordination are unverified. |
| 7 | Pass | Owner approval explains public browsing and the task scope; search approval separately discloses Mwmbl, license, and the model route receiving excerpts. |
| 8 | Pass within accepted boundary | Consent is task-scoped and covers mediated public browsing/redirects. The known possibility of GET side effects is documented in `docs/issue-66-validation.md`. |
| 9 | Pass | Task authorization expiry, cancellation, and cleanup are covered in #66/#69 validation evidence. |
| 10 | Pass | Desktop chat/browser panel hide and reopen preserve the active session; see `docs/issue-68-validation.md`. |
| 11 | Pass | Mobile full-screen panel and return-to-chat were checked at 390x844; see `docs/issue-68-validation.md`. |
| 12 | Pass | Hiding the panel leaves the job and page active; see `docs/issue-68-validation.md`. |
| 13 | Pass within supported sign-in slice | Owner-controlled HTTPS native username/password sign-in is covered by `docs/issue-69-validation.md`; JavaScript sign-in and MFA are unsupported. |
| 14 | Pass within supported sign-in slice | Typed credentials, cookies, and screenshots are withheld from the model; redaction and cleanup evidence is in `docs/issue-69-validation.md`. |
| 15 | Pass within opt-in read-only scope | An unchecked continuation option keeps the same isolated BrowserContext after the owner returns, reads up to five relevant authenticated links with mediated GET, then sends sanitized excerpts and the original request to the selected route for one follow-up answer. Credentials, cookies, screenshots, and typed sign-in values remain excluded. No post-login form or external action is resumed. |
| 16 | Pass within bounded form scope | #70 prepares one native POST from supported visible fields. Other methods, controls, and multi-step actions fail closed; see `docs/issue-70-validation.md`. |
| 17 | Pass for supported POST | Exact destination and proposed values are shown and bound to a one-use digest approval before dispatch; see #70 evidence. |
| 18 | Pass for supported POST | Owner can inspect the page and approve or reject the exact proposal; Docker fixture confirms one dispatch only after approval. |
| 19 | Pass with limits | Uncertain/unsupported forms stop and report. Unknown website semantics cannot be inferred from labels alone; unsupported forms are not converted into action proposals. |
| 20 | Pass within owner-refined policy | Identifiable payment, card, transfer, and stored-method markers are rejected; concealed website semantics cannot be universally detected. See #70. |
| 21 | Pass | Task completion, cancellation, failure, and timeout clear temporary browser session state; see #66/#69 validation evidence. |
| 22 | Pass | Run views, browser control, screenshots, and artifacts are owner-scoped; see #68/#69 evidence. |
| 23 | Pass | Provider errors and unavailable sites fail without fabricated results; see #67 provider failure fixtures and #66 mediator tests. |
| 24 | Pass | Upstream `lackeyjb/playwright-skill` version, commit, license, and adaptations are recorded in `docs/playwright-browser.md`. |

## Verification run

Acceptance recheck on 2026-11-27: base revision
`334e9e3d9081fef87cb286d8309b13a66a3fb75c` plus the uncommitted fraud
instruction and test assertion identified by SHA-256 in
`docs/issue-70-validation.md`, and an uncommitted search integration test
wait fix (`tests/test_browser_search_docker.py` SHA-256
`48edda18dc9276caded142ccd74ed6888319f51bbf16a88a30810319370a81e9`).
On Docker Desktop Engine 29.9.3, the following synthetic integration command
passed **3 tests**. Its first run failed because the search test stopped waiting
as soon as the synthesis request started, before result publication. An isolated
rerun passed; the test now waits for the published result marker, and the
combined command passed. Ruff and `git diff --check` passed; the latter reported
an unrelated existing line-ending notice.

```powershell
$env:PYTHONPATH='.'
$env:DJANGO_SETTINGS_MODULE='superharry.settings'
$env:DJANGO_SECRET_KEY='issue65-regression'
$env:SKILL_BROWSER_TEST_IMAGE='superharry-skill-browser-worker:local'
$env:SKILL_BROWSER_DOCKER_TEST='1'
.venv\Scripts\python.exe -m django test tests.test_browser_search_docker tests.test_browser_session_docker tests.test_chat.PlaywrightDockerIntegrationTests -v 1
```

The #70 broad regression separately passed 148 tests with one skip, and its
Docker action integration passed 2 tests. These checks do not establish
deployment or production acceptance.

On 2026-11-27, `tests/playwright_browser_session_smoke.py` passed on the rebuilt browser worker. In addition to direct-URL relevance-ranked traversal, its handoff fixture signed in, returned control, followed an authenticated relevant link with GET using cookies retained only inside the BrowserContext, cited that page, and verified credentials/cookies were absent from the published excerpt. The bridge, policy, action-plan, and consent regression passed **30 tests**. The chat-to-worker Docker integration previously passed **1/1** and cited an additional direct-URL page. The owner-action Docker integrations passed **2/2** after the latest URL-policy changes. The account-audit migration regression passed **1/1**, confirming migration 0041 reads historical usernames from model fields. The earlier focused combined Django regression passed **146 tests, with 1 skipped**. Ruff and `node --check` passed for the current changes.

## Boundaries retained after acceptance

- The post-login continuation is one opt-in GET traversal batch and one model response; iterative model-selected browser tools and post-login external actions remain unsupported.
- The owner-refined payment policy is marker-based; no universal payment or fraud detection is claimed.
- The accepted GET-only side-effect limitation remains disclosed; a site may assign effects to an ordinary link or main-page redirect.
