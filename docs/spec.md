# SuperHarry2 product specification

## Problem Statement

The owner needs a locally operated chat application that saves conversations, streams hosted model replies, and lets administrators manage accounts, routes, and usage limits. Users also need control over what saved information goes to hosted models and which files a skill can process.

## Solution

Deliver chat and administration first, then saved memory, then dynamically discovered repository and VM skills with isolated execution. Move CSV summarization into an agent-selected skill. Django provides the browser interface and owns application data. OmniRoute connects Django to model providers.

## User Stories

1. As the owner, I want an installer-created recovery superuser so I can administer a fresh deployment.
2. As an administrator, I want to manage regular accounts so I can control who has access.
3. As the superuser, I want exclusive control of privilege grants and administrator membership so I can control who administers the deployment.
4. As a regular user, I want a conversation sidebar and message composer so I can resume existing chats or start new ones.
5. As a regular user, I want to see the active model and watch replies stream so I know which model is answering and can read as it responds.
6. As a regular user, I want conversations to survive restarts so I can return to my history.
7. As a regular user, I want interrupted replies to remain visible and explicitly retryable so I can decide when to submit again.
8. As a regular user, I want a clear error when the free route is unavailable or exhausted so I understand why a reply could not be generated.
9. As an administrator, I want to list and test routes, maintain models on existing provider connections, and select the default so I can operate chat through Django admin.
10. As an administrator, I want global and per-user limits so I can manage usage.
11. As a regular user, I want locally rejected requests to be free and repeat delivery of one submission to count once so my quota reflects submissions to the gateway.
12. As the owner, I want provisioning reruns to preserve persistent state and administrator choices so I can repair an installation safely.
13. As a regular user, I want to save, view, edit, and delete memories so I control what persists beyond chat history.
14. As a regular user, I want a memory toggle near the composer that defaults to off per conversation so I control when relevant saved memories enter hosted requests.
15. As a regular user, I want the agent to select an appropriate available skill through chat so I can use its workflow from the normal interface.
16. As a regular user, I want to request a summary of a specific CSV I own and read the result so I control which file is processed.
17. As a regular user, I want server-side ownership checks on my conversations, memories, files, and results so other users cannot access them.
18. As the owner, I want execution jobs isolated, bounded, and cleaned up so each job has access only to its scoped inputs and results.

## Implementation Decisions

- Use Django templates and client-side streaming. Run Compose on Windows with Django bound to localhost, and reuse Compose in a dedicated Linux VM. Build Django on a tested, pinned Debian Sid slim digest. Persist SQLite and OmniRoute state in Docker volumes. Keep SQLite write transactions short and revisit PostgreSQL before concurrent use or multiple database-writing workers.
- Keep registration closed. Use built-in Django admin with explicit administrator permissions and superuser-only privilege grants. Enforce ownership on history, downloads, and other data access. Keep private chats out of routine admin views.
- Allow one active response per user. Enforce daily message and maximum response length limits, with per-user overrides. Reset the daily counter at midnight in the deployment timezone, initially `America/Guayaquil`. Count each submission to OmniRoute once. Persist partial replies and mark interrupted turns incomplete.
- Stream through OmniRoute's OpenAI-compatible chat endpoint. Seed the OpenRouter `google/gemma-4-26b-a4b-it:free` route and verify its canonical ID from the deployed gateway. Provision available OpenRouter, Groq, and Gemini connections from installer secrets. Disable paid fallback. Manage provider accounts, upstream credentials, and OAuth in OmniRoute. Give Django separate server-side inference and management credentials with the smallest supported management scope.
- Provision the gateway on a private network, migrate Django, and bootstrap the recovery account. Repair missing configuration on reruns and preserve administrator choices. A live provider outage must allow Django and OmniRoute to start.
- Store chat history and saved memory in separate models. Sending a message authorizes sending the conversation context needed for the reply. Use bounded SQLite retrieval for relevant memories from the conversation owner only when that conversation opts in.
- Discover repository and VM skills dynamically, and enforce ownership when managing VM skill drafts. Run each job in a disposable environment without application credentials or the Docker socket. Bound CPU, memory, runtime, and output and clean up job state. Until mediated egress is implemented, deny sandbox network access and prepare dependencies from supplied offline wheels. Future network mediation must require specific approval for transmitting user data, external effects, and uncertain requests. A user must explicitly request a CSV summary and own the file.

## Testing Decisions

- Use browser-facing Django views and admin actions as the primary test boundary, with a controlled OmniRoute fixture at the gateway boundary. Cover authentication, authorization, streaming, persistence, interrupted replies, explicit retry, route management, quota accounting, limits, and free route failures.
- In the dedicated Linux VM, smoke-test role-based login, persistence after Django restarts, admin route changes, denial of management calls using the inference credential, and provisioning reruns that preserve the selected route and state.
- Record the Docker suite, VM smoke test, and live provider check separately. Verify a streamed reply through the seeded free route live. If the provider is unavailable, leave live verification pending and record the sanitized error.
- Verify memory isolation and outbound request contents with the controlled gateway. Test skill discovery, authorization, preparation and execution through public interfaces. In the Linux VM, verify job isolation, network denial or approved mediation as applicable, resource limits, and cleanup after success, failure, and timeout. Test CSV ownership and explicit summarization intent through chat when that migration is implemented.

## Out of Scope

Public registration, paid fallback, and provider-account setup through Django are outside these milestones.

## Further Notes

Track implementation and runtime verification separately in the milestones and issue tracker. Keep Debian package findings visible in scan reports and review findings that could compromise sandbox isolation or expose credentials before release.

- [Architecture and agreed decisions](architecture.md)
- [Milestones and acceptance criteria](milestones.md)
- [VM and Docker runbook](vm-runbook.md)
