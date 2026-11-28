# Milestones and acceptance

The current working environment is Docker Desktop on Windows. Record controlled Docker checks, checks against the deployed services, and live provider results separately. Earlier Linux VM results below are historical evidence for the snapshots identified there, not proof of current deployment acceptance.

## 1. Chat and administration

- An installer-bootstrapped superuser can sign in and create administrator and regular accounts using Django admin. A regular user cannot manage accounts, models, or limits or access another user's data.
- A signed-in user can start a conversation, watch a live response stream through OmniRoute, restart Django, and reopen the saved conversation.
- Django preserves partial replies on interruption, marks them incomplete, and allows an explicit retry without automatically resubmitting.
- An administrator can list routes, add or update a model on an existing provider connection, test it, and change the default without editing code. A regular user sees the active model.
- Daily limits, response caps, per-user overrides, and the one-active-response limit are enforced. Local rejections leave quota intact and repeat delivery of the same submission consumes one quota unit.
- Docker tests run with a controlled gateway fixture. Deployment smoke tests cover login, persistence, and a route change. A separate live check verifies that the seeded OpenRouter Gemma free route returns a streamed reply. Record an unavailable free route as pending live verification.

## 2. Saved memory

- A user can save, inspect, edit, and delete their own memories.
- New conversations include relevant saved memories belonging to their owner by default. Users can turn memory off before the first message. The choice stays independent per conversation, and existing conversations retain their stored setting.

## 3. Skills and bundled execution

- Repository and locally stored skills are discovered dynamically, and users can manage local skill drafts according to ownership. The current interface calls application-managed local skills “VM skills”; using them does not require starting a separate VM.
- A skill can be prepared and executed in an isolated environment; prepared skills are available to users and results remain private to the submitting user.
- The agent selects an appropriate skill through chat. A regular user can explicitly request a summary of their own CSV and read the result; attaching a file alone does not start processing.
- Isolated Docker Desktop checks verify job isolation, network denial or approved mediation as applicable, limits, and cleanup after success, failure, and timeout. The owner replaced the former dedicated VM requirement for issue #28 with Docker Desktop validation on 2026-11-25.

## Validation evidence

### Docker and gateway

- The 33-test Django suite and Compose smoke test passed on Windows and on Debian 13 ARM64. The controlled gateway covers streamed chat, route administration, credential separation, and saved-memory opt-in.
- The private OmniRoute 3.8.53 deployment verified the exact canonical OpenRouter Gemma free route on Windows and Debian 13 ARM64. The VM passed administrator and regular sign-in, a Django admin route change, and a real gateway denial of management access with the inference credential. A controlled streamed reply, the user account, and the admin-selected route survived a Django restart and provisioning rerun.
- A browser-facing HTTP smoke test through Django and OmniRoute previously received a sanitized rate-limit error from the seeded free route and reopened the saved conversation with an incomplete reply. A direct request to the same model returned HTTP 429. The controlled Django suite verifies that free-route failures do not trigger fallback.
- On 2026-11-24, a separate live HTTP smoke run on the dedicated Debian 13 ARM64 VM streamed one text fragment through OmniRoute on `openrouter/google/gemma-4-26b-a4b-it:free`, received a completion event, and reopened the saved conversation. The run used a temporary Django database and the provisioned gateway. All 33 Django tests and the CSV runner smoke test also passed on ARM64 against `f5e8965`.
- A repeat live run with the strengthened smoke check verified that the streamed text appeared as the assistant reply in the reopened conversation. The 35-test Windows suite passed, including HTTP regressions for a missing saved assistant reply and a correctly escaped saved reply.

### CSV runner

- The Debian 13 ARM64 VM verified the unprivileged worker, read-only filesystem, network denial, CPU and memory limits, process limit, output limit, and removal of input files and containers after success, failure, and timeout.
- On the provisioned ARM64 deployment, a regular user uploaded and approved a CSV, the host runner processed it in a disposable container, and the owner downloaded the expected summary through Django.
- The worker contains Debian Sid Python `3.14.10-1`, which the [Debian tracker](https://security-tracker.debian.org/tracker/CVE-2026-18503) lists as fixed for the CSV dialect detection issue. The worker uses a fixed CSV dialect.
- On 2026-11-24, `python3 -m tests.csv_runner_smoke` passed on the dedicated Debian 13 ARM64 VM after the runtime upgrade. Docker reported Engine CE `29.10.0`, `runc 1.5.3`, and `containerd 2.4.2`. The installed worker's script matched the checkout by SHA-256, and its installed Python package was `3.14.10-1`.
- The deployed runtime supersedes the affected Debian trixie packages. The reviewed runc findings have fixes by `1.4.3` on the 1.4 branch, and `containerd 2.4.2` includes the reviewed fixes. CSV jobs use the prebuilt local worker image with pulls disabled and an explicit UID/GID of `10001:10001`. [Docker release notes](https://docs.docker.com/engine/release-notes/29/) · [Debian runc findings](https://security-tracker.debian.org/tracker/source-package/runc) · [Debian containerd findings](https://security-tracker.debian.org/tracker/source-package/containerd)

### Skill runtime development checks

- On 2026-11-25, the local #24 work passed 166 Django tests with the Docker network integration enabled, plus the sandbox, preparation, network, and Docker Desktop spool smoke checks on Engine `29.9.3`, `runc 1.5.3`, and `containerd 2.4.2`. Controlled destinations covered owner approval, denial, repeated requests, user data in GET requests, redirects, private destinations, direct TCP and UDP attempts, package-channel spoofing, and cleanup after interruption. A separate public PyPI check prepared `requests==2.32.5` and four transitive wheels, then ran from those wheels offline. The Desktop spool check exercised approval from a disposable Django container with a temporary database and synthetic inputs. These checks did not deploy the change. See [issue #28 evidence](issue-28-docker-evidence.md) for the later Docker validation.
- On 2026-11-24, controlled tests on Docker Desktop `4.100.1` with Engine `29.9.3`, `runc 1.5.3`, and `containerd 2.4.2` passed with the runtime gate enabled. The tests exercised the Windows host worker and a disposable Django container with a temporary database and spool, including offline preparation with a third-party `packaging==26.0` wheel, retry, execution, and result ownership.
- The sandbox smoke test checked unprivileged execution, no network or application database, no Docker socket or credentials, read-only root, CPU, memory and process limits, bounded output, timeout, and container cleanup. These development checks did not deploy the feature to the running application and do not replace the separately tracked lifecycle and mediated-network acceptance.
