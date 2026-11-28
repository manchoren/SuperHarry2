# Issue #70: website action review boundary

## Current status

Owner decision (2026-11-27): retain the existing block on identifiable
purchases, payments, transfers and payment-card handling, even with approval.
The owner accepts that a malicious site can conceal an effect, so this is a
bounded, marker-based control rather than a universal guarantee. The agent must
not solicit payment information or prepare fraudulent actions; fraud detection
also has semantic limits. The explicit instruction against fraud is model
guidance, not a deterministic fraud detector. Each supported external action
still requires exact, one-use owner approval and uncertainty stops dispatch.
Purchases with approval are deferred to separate work.

The optional HTTPS owner handoff supports one model-prepared, native
URL-encoded POST for an explicit action request attached to a public URL task.
The owner must approve the browser task with HTTPS handoff enabled and then take
control. For action preparation, the selected chat model receives only bounded visible
form metadata and the requested action, marked as untrusted input, and may
prepare values for visible text fields. A separate unchecked continuation option
can send the returned sanitized page text to the selected route once after
login; credentials, cookies, screenshots, and sign-in values are excluded. The browser fills those fields with page JavaScript still
disabled and pauses at the mediated POST. An immutable description appears in
the owner-only panel; the network request is sent only after the owner approves
that exact one-use proposal. Denial, expiry, uncertainty, payment markers, and
loss of task authorization stop the request. Model output cannot approve it.

The supported slice is limited to one form and one POST per handoff, visible
text/email/URL/search/telephone/textarea fields, and a single clear submit
control. It does not handle credentials, MFA, checkboxes, selects, multipart or
JavaScript-dependent forms, arbitrary HTTP methods, or unknown payment
semantics. The action classifier cannot determine every site's behavior, so
the owner must inspect both the live page and exact values. Unsupported or
uncertain requests fail closed. No action result claims rollback. Payment,
purchase, transfer, banking, wallet, and card-related activity stays blocked,
including stored-payment markers.

Historical GitHub tracking update (superseded by the owner decision above): #70 was `needs-info` pending an owner
decision on the conflict between its absolute prohibition and generic website
actions. A remote site controls the server-side meaning of its form fields: a
neutral destination and visible field such as `continue` or `x12=default` may
trigger a stored-method charge without exposing payment semantics in the page.
The host can reject known payment markers and uncertain plans, but cannot prove
that every arbitrary site action is not a payment. More marker tests improve
known-case coverage; they cannot prove the universal criterion. Until the owner
chooses a trusted-site policy, authorizes a bounded marker-based criterion, or
asks for a redesign that disables generic external-action dispatch, criterion
3 remains partial. This leaves #70 open and blocks closure of parent #65,
including its action-dependent stories 16–20; it does not invalidate the
completed issues #66–#69.

Normal browsing and search retain the accepted GET-only contract for #67:
they do not click links or submit forms. The explicit manual owner handoff is a
separate workflow. A website could still attach side effects to a GET request;
that limitation remains recorded under #66.

## Acceptance status

| Criterion | Status | Evidence / remaining work |
| --- | --- | --- |
| Present each external action and destination for owner review and approval or rejection | Implemented for the supported form slice | The agent prepares visible text fields from the explicit request. Method, destination, page labels, and exact values appear in the owner-only panel before dispatch. Unsupported forms fail closed. |
| Stop on uncertain effects; enforce decisions outside model instructions | Implemented with documented semantic limits | The model can return uncertain; malformed plans, unsupported fields, and unsafe destinations fail closed. The host binds the decision to the immutable request digest and dispatches only after owner approval. Page instructions are treated as untrusted. Site-specific semantics still require owner inspection. |
| Block identifiable purchases, payments, transfers, and card data regardless of approval | Passed within the owner-accepted bounded policy | URL, labels, field names, values and the requested action are checked; card-like numbers and stored-payment markers are blocked. This marker-based control cannot establish the effects of every website. The agent is instructed not to prepare fraudulent actions; this is model guidance, not guaranteed detection. |
| Controlled approval, denial, uncertainty, adversarial content, bypass, and payment tests | Passed for the supported path | Tests cover no request before exact approval, denial with no network call, wrong digest/replay, uncertain plans, adversarial page instructions, camelCase and lowercase stored-payment identifiers, numeric card-like values using spaces, periods, slashes, or colons, hidden opaque fields and login redirects, unsupported formats, payment-labelled GET links, and GET form/button submissions. A neutral `/api/submit` carrying `paymentmethodid=stored-card` is blocked before the network adapter and before an approval proposal. |
| Complete Docker chat-to-action-review flow | Passed on Docker Desktop with a synthetic site | The worker reads a form, the model prepares a value, the adversarial page instruction is shown to the owner, and no POST reaches the fixture before exact approval. Approval sends one POST. A subsequent payment-labelled GET form is stopped before dispatch by both click and Enter, no second fixture request occurs, and the authenticated result remains available. HTTPS main-page redirects are followed as accepted in #66; a remote site may assign effects to a GET, including a redirect after a form POST. The run reports its result and cleans up. No production behavior or rollback is claimed. |

## Evidence

Owner-refinement verification, 2026-11-27: checkout `334e9e3d9081fef87cb286d8309b13a66a3fb75c`
plus the uncommitted fraud instruction and its assertion. SHA-256 of
`superharry/browser_action_plans.py` is
`a219d09df003a0494c309de6894870e879265e334e64c12060a0104495c848d0`;
SHA-256 of `tests/test_browser_action_plans.py` is
`9ad23eb18be47595680df8853d123faf30294c77aa197302a8b026347989bec1`.
The broad command below passed **148 tests with one skip**. The opt-in Docker
action command below passed **2 tests** on Docker Desktop Engine 29.9.3 with
the existing `superharry-skill-browser-worker:local` image. Ruff and
`git diff --check` passed (the latter reported an existing line-ending notice).
The fraud assertion confirms the instruction is sent to the model; it does not
establish reliable detection of fraud.

Commands run from PowerShell on 2026-11-27:

The broad chat/browser regression command, run before the additional field and GET-action checks, was:

```powershell
.venv\Scripts\python.exe -m django test tests.test_browser_action_plans tests.test_browser_handoff tests.test_browser_policy tests.test_browser_session_panel tests.test_browser_handoff_bridge tests.test_browser_search tests.test_chat -v 1
```

Result: **138 chat/browser/policy tests passed; 1 Docker-opt-in test was
skipped**. Ruff and JavaScript syntax checks passed.

```powershell
$env:PYTHONPATH='.'
$env:DJANGO_SETTINGS_MODULE='superharry.settings'
$env:DJANGO_SECRET_KEY='browser-action-docker-tests'
$env:SKILL_BROWSER_TEST_IMAGE='superharry-skill-browser-worker:local'
.venv\Scripts\python.exe -m django test tests.test_browser_action_docker -v 1
```

Result: **1 Docker Desktop action test passed**. It uses only a synthetic
fixture and confirms the model-prepared value is shown before approval, exactly
one POST occurs after exact approval, the adversarial page text does not
authorize itself, and a payment-labelled GET form stopped by both click and Enter
causes no second fixture request while leaving the authenticated result available.
The run reports its result and cleanup completes. HTTPS main-page redirects are
followed as accepted in #66; a remote site may assign effects to a GET, including
a redirect after a form POST. The existing Docker
search and owner-session regressions passed **2/2**. The separate chat browser
Docker integration passed **1/1** when run with its explicit opt-in flag. The
`skill-browser-worker` Docker target was rebuilt locally.

The search/session and ordinary chat Docker regression command was:

```powershell
$env:PYTHONPATH='.'
$env:DJANGO_SETTINGS_MODULE='superharry.settings'
$env:DJANGO_SECRET_KEY='browser-regression-docker-tests'
$env:SKILL_BROWSER_TEST_IMAGE='superharry-skill-browser-worker:local'
.venv\Scripts\python.exe -m django test tests.test_browser_search_docker tests.test_browser_session_docker tests.test_chat.PlaywrightDockerIntegrationTests -v 1
```

It passed **2 tests**; the chat browser test requires the explicit opt-in:

```powershell
$env:SKILL_BROWSER_DOCKER_TEST='1'
.venv\Scripts\python.exe -m django test tests.test_chat.PlaywrightDockerIntegrationTests -v 2
```

That additional Docker Desktop test passed **1/1**.

After an independent security review found camelCase payment identifiers,
card-like numbers, opaque hidden fields, and owner-submitted GET actions that
needed stronger checks, the following focused regression command passed on
2026-11-27:

```powershell
$env:PYTHONPATH='.'
$env:DJANGO_SETTINGS_MODULE='superharry.settings'
$env:DJANGO_SECRET_KEY='browser-safety-regression'
.venv\Scripts\python.exe -m django test tests.test_browser_policy tests.test_browser_action_plans -v 1
```

Result: **25 tests passed**. This includes camelCase and lowercase stored-payment
markers; neutral-endpoint rejection of concatenated financial fields such as
`totalamount`, `amountdue`, `fee`, `priceValue`, `invoiceId`, `quantity`,
`defaultMethod`, `chargeTotal`, and `loanamount`;
numeric card-like values separated by whitespace, commas, punctuation, zero-width
and other Unicode characters; login redirect-field rejection; opaque `/api/commit`
and financial/card-bearing GET destinations blocked before network access; fields
outside the visible-control allowlist; owner GET form/button refusal;
payment-labelled link refusal; and ordinary GET navigation.
The browser action Docker integrations passed **2/2** on the local
`superharry-skill-browser-worker:local` image. One controlled website returns a
303 from an exactly approved profile POST to the opaque `/api/commit` route; the
fixture records one POST and no follow-up GET, and the owner sees that the result
is uncertain. The other fixture verifies separate owner click and Enter gestures
on a GET form both stop before network access and each produces its own owner notice.
The full Docker pair was rerun after the latest URL-policy changes and passed **2/2**.
`tests/playwright_browser_session_smoke.py`
passed the HTTPS login, redirect, redaction, cookie isolation and cleanup fixture,
and `tests/playwright_browser_smoke.py` passed the GET-only mediation and redirect
fixture.

The owner-controlled GET URL policy is also exercised independently of the
browser fixture: a login redirect to the marked `/api/commit` action route, a
concatenated financial route such as `/totalamount`, and a destination carrying
a card-like value are rejected before the network adapter. Redirects to sites
whose semantics are not signaled by the visible URL remain an unresolvable
classification limit, so the issue's absolute payment criterion remains partial.

Ruff passed on the changed Python implementation and tests. `node --check`
passed for `session.mjs`; `git diff --check` passed apart from the existing
working-copy line-ending notices.

Checks: Ruff passed on the implementation and targeted tests. `node --check`
passed on the browser adapter and panel JavaScript. The 0041 migration startup
regression evidence is recorded in `docs/issue-62-validation.md`.

## Remaining release boundary

This is local Docker Desktop evidence with synthetic data. It does not
establish deployment or production acceptance, guarantee classification of
every website's effect, or roll back an external change. Redirects after a
sign-in POST remain followed under the accepted GET-only browsing behavior; a
site could assign an effect to such a GET. The bounded payment policy cannot
cover website semantics that cannot be identified from available markers.
The owner must inspect the page and exact request before
approving a supported action.
