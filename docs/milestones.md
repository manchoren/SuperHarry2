# Milestones and acceptance

All milestones are pending. Record Docker suite, VM smoke test, and live provider results separately.

## 1. Chat and administration

- An installer-bootstrapped superuser can sign in and create administrator and regular accounts using Django admin. A regular user cannot manage accounts, models, or limits or access another user's data.
- A signed-in user can start a conversation, watch a live response stream through OmniRoute, restart Django, and reopen the saved conversation.
- Django preserves partial replies on interruption, marks them incomplete, and allows an explicit retry without automatically resubmitting.
- An administrator can list routes, add or update a model on an existing provider connection, test it, and change the default without editing code. A regular user sees the active model.
- Daily limits, response caps, per-user overrides, and the one-active-response limit are enforced. Local rejections leave quota intact and repeat delivery of the same submission consumes one quota unit.
- Docker tests run with a controlled gateway fixture. The VM smoke test covers login, persistence, and a route change. A separate live check verifies that the seeded Solar Mini 4 free route returns a streamed reply. Record an unavailable free route as pending live verification.

## 2. Saved memory

- A user can save, inspect, edit, and delete their own memories.
- Hosted requests omit saved memory by default. Opting in for one conversation includes relevant memories belonging to its owner and leaves other conversations' settings independent.

## 3. Skills and bundled execution

- A repository-managed text skill runs through the normal chat interface.
- A regular user can approve a proposed CSV summary for their own file and read the resulting output. Another user's files and unapproved requests are rejected.
- Dedicated Linux VM checks verify job isolation, network denial, limits, and cleanup after success, failure, and timeout.
- Model-generated execution has its own future milestone.
