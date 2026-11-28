# Issue #65 parent acceptance audit

## Overall status

Issue #65 remains open. Most browser foundations and task-local security controls have local or Docker evidence. With separate opt-in, story 15 now resumes read-only browsing in the same temporary authenticated session: after the owner returns control, up to five relevant links can be read with mediated GET and their sanitized excerpts sent for one follow-up answer. Stories 16 through 20 depend on #70 and remain bounded; the absolute payment exclusion is not proven for unknown website semantics. Story 3 follows a small, relevance-ranked set of links from direct URLs and Mwmbl results through mediated GET requests. Clicks, form submissions, and page JavaScript remain excluded by the accepted GET-only boundary.

All evidence below is local and synthetic unless explicitly stated. It does not establish deployment or production acceptance.

## User story status

| # | Status | Evidence / remaining work |
| --- | --- | --- |
| 1 | Pass | Chat skill selection and launch covered by `docs/issue-66-validation.md` and `docs/issue-67-mwmbl-provider.md`. |
| 2 | Pass | Public URL and question flows have chat-to-worker coverage in the #66/#67 Docker evidence. |
| 3 | Pass within bounded GET-only scope | Main-frame redirects are mediated. Direct URL and search tasks navigate to up to five relevance-ranked public links with GET. The Docker session fixture follows a relevant privacy page, excludes irrelevant and sensitive links, and cites only successful pages; the chat-to-worker integration confirms direct URL tasks receive the bounded-link setting. No page clicks, forms, or JavaScript run. |
| 4 | Pass | Search answer synthesis returns verified source URLs; controlled Docker evidence is in `docs/issue-67-mwmbl-provider.md`. |
| 5 | Pass | Owner-requested screenshot artifact path and panel controls are covered in `docs/issue-68-validation.md`. |
| 6 | Pass | Mwmbl is configured through environment settings and uses a shared local SQLite limiter; provider evidence is in `docs/issue-67-mwmbl-provider.md`. Live provider behavior is unverified. |
| 7 | Pass | Owner approval explains public browsing and the task scope; search approval separately discloses Mwmbl, license, and the model route receiving excerpts. |
| 8 | Pass within accepted boundary | Consent is task-scoped and covers mediated public browsing/redirects. The known possibility of GET side effects is documented in `docs/issue-66-validation.md`. |
| 9 | Pass | Task authorization expiry, cancellation, and cleanup are covered in #66/#69 validation evidence. |
| 10 | Pass | Desktop chat/browser panel hide and reopen preserve the active session; see `docs/issue-68-validation.md`. |
| 11 | Pass | Mobile full-screen panel and return-to-chat were checked at 390x844; see `docs/issue-68-validation.md`. |
| 12 | Pass | Hiding the panel leaves the job and page active; see `docs/issue-68-validation.md`. |
| 13 | Pass within supported sign-in slice | Owner-controlled HTTPS native username/password sign-in is covered by `docs/issue-69-validation.md`; JavaScript sign-in and MFA are unsupported. |
| 14 | Pass within supported sign-in slice | Typed credentials, cookies, and screenshots are withheld from the model; redaction and cleanup evidence is in `docs/issue-69-validation.md`. |
| 15 | Pass within opt-in read-only scope | An unchecked continuation option keeps the same isolated BrowserContext after the owner returns, reads up to five relevant authenticated links with mediated GET, then sends sanitized excerpts and the original request to the selected route for one follow-up answer. Credentials, cookies, screenshots, and typed sign-in values remain excluded. No post-login form or external action is resumed. |
| 16 | Partial | #70 prepares one bounded native POST from visible text fields. Other methods, controls, and multi-step actions fail closed; see `docs/issue-70-validation.md`. |
| 17 | Pass for supported POST | Exact destination and proposed values are shown and bound to a one-use digest approval before dispatch; see #70 evidence. |
| 18 | Pass for supported POST | Owner can inspect the page and approve or reject the exact proposal; Docker fixture confirms one dispatch only after approval. |
| 19 | Pass with limits | Uncertain/unsupported forms stop and report. Unknown website semantics cannot be inferred from labels alone; unsupported forms are not converted into action proposals. |
| 20 | Partial | Common payment, card, transfer, and stored-method markers are rejected, but universal detection of every site's payment semantics is not established. Criterion 3 of #70 remains partial. |
| 21 | Pass | Task completion, cancellation, failure, and timeout clear temporary browser session state; see #66/#69 validation evidence. |
| 22 | Pass | Run views, browser control, screenshots, and artifacts are owner-scoped; see #68/#69 evidence. |
| 23 | Pass | Provider errors and unavailable sites fail without fabricated results; see #67 provider failure fixtures and #66 mediator tests. |
| 24 | Pass | Upstream `lackeyjb/playwright-skill` version, commit, license, and adaptations are recorded in `docs/playwright-browser.md`. |

## Verification run

On 2026-11-27, `tests/playwright_browser_session_smoke.py` passed on the rebuilt browser worker. In addition to direct-URL relevance-ranked traversal, its handoff fixture signed in, returned control, followed an authenticated relevant link with GET using cookies retained only inside the BrowserContext, cited that page, and verified credentials/cookies were absent from the published excerpt. The bridge, policy, action-plan, and consent regression passed **30 tests**. The chat-to-worker Docker integration previously passed **1/1** and cited an additional direct-URL page. The owner-action Docker integrations passed **2/2** after the latest URL-policy changes. The account-audit migration regression passed **1/1**, confirming migration 0041 reads historical usernames from model fields. The earlier focused combined Django regression passed **146 tests, with 1 skipped**. Ruff and `node --check` passed for the current changes.

## Closure gates

- The post-login continuation is one opt-in GET traversal batch and one model response; iterative model-selected browser tools and post-login external actions remain unsupported.
- Resolution of #70's absolute payment-blocking criterion, or owner-approved refinement to the marker-covered bounded behavior. No claim of universal payment detection is made.
- The accepted GET-only side-effect limitation remains disclosed; a site may assign effects to an ordinary link or main-page redirect.
