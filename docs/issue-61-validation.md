# Issue #61 validation boundary

Account deactivation marks the account inactive immediately and records cancellation requests for current work. Each chat provider stream is read by a spawned child process; the Django parent polls the submission state every 50 ms and terminates and joins the reader when suspension is committed. Tool execution and all database writes stay in the parent. The controlled Windows regression holds the provider response open, verifies the reader waits during normal idle time, deactivates the account, and confirms the reader exits and the response is marked interrupted before the fixture releases the provider.

The gateway still uses `opener.open(..., timeout=30)` for normal provider connect/read inactivity. There is no total deadline for active streams, matching existing behavior. During suspension the parent process terminates the SSE reader rather than waiting for that timeout.

## Checks

- `tests.test_account_lifecycle.AccountLifecycleTests.test_inflight_chat_response_is_interrupted_and_confirmation_is_persisted`: passed on Windows with a real local HTTP provider and spawned stream reader.
- Focused lifecycle, admin, chat, and Docker checks are recorded in the implementation handoff.
