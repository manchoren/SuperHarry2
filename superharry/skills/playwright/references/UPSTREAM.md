# Upstream provenance and adaptations

- Repository: <https://github.com/lackeyjb/playwright-skill>
- Upstream release: `5.0.0`
- Pinned source revision: `dd47a6a023e249eb1b36e9e943eab89d0900865d`
- License: MIT, preserved in `../LICENSE`.
- Verified revision: the commit object for the repository's `main` branch at implementation time; the upstream package metadata at that revision reports version `5.0.0`.

`UPSTREAM-SKILL.md`, `UPSTREAM-API_REFERENCE.md`, and `../vendor/` preserve upstream skill instructions, API reference, executor, helper, and npm manifests. These are provenance/reference copies. SuperHarry uses its own `../SKILL.md` and `../main.mjs`; it does not run the upstream arbitrary-script executor.

## Runtime adaptations

- Discovery continues through the repository skill catalog. The agent can invoke only the fixed `playwright` skill with a URL supplied by the user.
- The browser runs in the disposable `skill-browser-worker` image, under the existing unprivileged, read-only, no-capabilities Docker sandbox with no Docker socket or application credentials.
- Chromium routes every request through Playwright's context route handler. Service workers and WebSockets are disabled. Only HTTP(S) GET requests are admitted; all other schemes and methods fail closed.
- The worker container has `--network=none`. Browser requests cross the existing skill-worker channel to a host mediator that resolves and checks every destination and connects to a validated public IP. Redirect responses are returned to Chromium; each redirected URL is intercepted and checked again.
- The owner approves public browsing for one owner-owned job before dispatch. Approval is revoked at task termination, cancellation, failure, or timeout. It does not apply to other skills.
- The initial slice reads one page and returns page text and verified main-document source URLs. It does not search, click, fill forms, take over a session, submit forms, or perform external actions.

The upstream's broader automation instructions, local-server discovery, persistent scripts, login flows, arbitrary scripts, and unrestricted browser controls are not enabled in this adaptation.
