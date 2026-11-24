# Milestones and acceptance

All milestones are pending. Record Docker suite, VM smoke test, and live provider results separately.

## 1. Chat and administration

- An installer-bootstrapped superuser can sign in and create administrator and regular accounts using Django admin. A regular user cannot manage accounts, models, or limits or access another user's data.
- A signed-in user can start a conversation, watch a live response stream through OmniRoute, restart Django, and reopen the saved conversation.
- Django preserves partial replies on interruption, marks them incomplete, and allows an explicit retry without automatically resubmitting.
- An administrator can list routes, add or update a model on an existing provider connection, test it, and change the default without editing code. A regular user sees the active model.
- Daily limits, response caps, per-user overrides, and the one-active-response limit are enforced. Local rejections leave quota intact and repeat delivery of the same submission consumes one quota unit.
- Docker tests run with a controlled gateway fixture. The VM smoke test covers login, persistence, and a route change. A separate live check verifies that the seeded OpenRouter Gemma free route returns a streamed reply. Record an unavailable free route as pending live verification.

## 2. Saved memory

- A user can save, inspect, edit, and delete their own memories.
- Hosted requests omit saved memory by default. Opting in for one conversation includes relevant memories belonging to its owner and leaves other conversations' settings independent.

## 3. Skills and bundled execution

- A repository-managed text skill runs through the normal chat interface.
- A regular user can approve a proposed CSV summary for their own file and read the resulting output. Another user's files and unapproved requests are rejected.
- Dedicated Linux VM checks verify job isolation, network denial, limits, and cleanup after success, failure, and timeout.
- Model-generated execution has its own future milestone.

## Validation evidence

### Docker and gateway

- The 33-test Django suite and Compose smoke test passed on Windows and on Debian 13 ARM64. The controlled gateway covers streamed chat, route administration, credential separation, and saved-memory opt-in.
- The private OmniRoute 3.8.53 deployment verified the exact canonical OpenRouter Gemma free route on Windows and Debian 13 ARM64. The VM passed administrator and regular sign-in, a Django admin route change, and a real gateway denial of management access with the inference credential. A controlled streamed reply, the user account, and the admin-selected route survived a Django restart and provisioning rerun.
- Live OpenRouter validation remains pending. OmniRoute returned a rate-limit or cooldown error for both tested Gemma free variants. The application surfaced the sanitized error and made no paid fallback request.

### CSV runner

- The Debian 13 ARM64 VM verified the unprivileged worker, read-only filesystem, network denial, CPU and memory limits, process limit, output limit, and removal of input files and containers after success, failure, and timeout.
- On the provisioned ARM64 deployment, a regular user uploaded and approved a CSV, the host runner processed it in a disposable container, and the owner downloaded the expected summary through Django.
- The worker contains Debian Sid Python `3.14.10-1`, which the [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-18503) lists as fixed for the CSV dialect detection issue. The worker uses a fixed CSV dialect.
- The validation VM uses Debian trixie's `runc 1.1.15+ds1-2+b4` and `containerd 1.7.24~ds1-6+deb13u1`. The Debian tracker reports open runtime issues affecting those release lines. Keep execution on test inputs until the deployment runtime is patched or the relevant isolation findings are resolved. [runc](https://security-tracker.debian.org/tracker/source-package/runc) · [containerd](https://security-tracker.debian.org/tracker/source-package/containerd)
