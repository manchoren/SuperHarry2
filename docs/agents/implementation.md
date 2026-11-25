# Implementation and validation

## Establish the target

- Read the current ticket and its blockers using the issue tracker guide. Keep every acceptance criterion visible through implementation and review.
- Use the active workspace and honor the user's current choice of environment. For local Docker work, inspect Docker Desktop and its running engine before choosing a test host. Treat prior VM and QEMU records as historical; if an acceptance criterion conflicts with the user's chosen host, surface the conflict before changing hosts.
- Before implementing or running a host worker, inspect its entry point and check that its operating-system APIs, Docker CLI, and runtime version work on the chosen host. Report any unsupported configuration before attempting a consequential action.

## Complete and verify

- Continue through the remaining authorized criteria after each passing slice. Stop at an explicit user request or a genuine blocker; report the exact unfinished criteria and the evidence needed to resolve them.
- Test user-visible behavior at the agreed boundaries and run the checks required by the ticket. For sandbox work, verify the real configured runtime and its limits using synthetic inputs and isolated state. A bypassed gate or simulated worker is diagnostic evidence, not isolation acceptance.
- Before claiming completion or closing a ticket, account for each criterion and record the tested revision, commands, and results. Distinguish local tests, Docker integration, deployment, and production acceptance; identify anything still pending.
