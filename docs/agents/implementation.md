# Implementation and validation

## Establish the target

- Read the current ticket and its blockers using the issue tracker guide. Keep every acceptance criterion visible through implementation and review.
- Use the active workspace and honor the user's current choice of environment. For local Docker work, inspect Docker Desktop and its running engine before choosing a test host. Treat prior VM and QEMU records as historical; if an acceptance criterion conflicts with the user's chosen host, surface the conflict before changing hosts.
- Before implementing or running a host worker, inspect its entry point and check that its operating-system APIs, Docker CLI, and runtime version work on the chosen host. Report any unsupported configuration before attempting a consequential action.

## Complete and verify

- Before ending work, account for every acceptance criterion as satisfied with evidence, incomplete, or blocked. Continue all authorized work that can progress independently; a passing slice or an incomplete design is not a stopping condition. Stop only at an explicit user request or a genuine blocker, and report the unfinished criteria and evidence or input needed to resolve them.
- Test user-visible behavior at the agreed boundaries and run the checks required by the ticket. For sandbox work, verify the real configured runtime and its limits using synthetic inputs and isolated state. A bypassed gate or simulated worker is diagnostic evidence, not isolation acceptance.
- Before claiming completion or closing a ticket, record the tested revision, commands, and results for the criteria. Distinguish local tests, Docker integration, deployment, and production acceptance; identify anything still pending.
