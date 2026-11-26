# Implementation and validation

## Establish the target

- Read the current ticket and its blockers using the issue tracker guide. Keep every acceptance criterion visible through implementation and review.
- Use Docker Desktop on Windows for local development and validation; inspect the running engine and use isolated test data. Prior VM and QEMU records are historical. Do not request or start the old VM for this project.
- Before acting on an environment-specific ticket, compare its acceptance criteria with the user's current instructions and the documented environment. If they conflict, keep using the authorized environment and surface the conflict before changing hosts. Revise the ticket only when the user has authorized the change; otherwise record the conflicting criterion as pending and continue independent work. Do not treat evidence from one environment as acceptance for another.
- Before implementing or running a host worker, inspect its entry point and check that its operating-system APIs, Docker CLI, and runtime version work on the chosen host. Report any unsupported configuration before attempting a consequential action.

## Complete and verify

- Before ending work, account for every acceptance criterion as satisfied with evidence, incomplete, or blocked. Continue all authorized work that can progress independently; a passing slice or an incomplete design is not a stopping condition. Stop only at an explicit user request or a genuine blocker, and report the unfinished criteria and evidence or input needed to resolve them.
- Test user-visible behavior at the agreed boundaries and run the checks required by the ticket. For sandbox work, verify the real configured runtime and its limits using synthetic inputs and isolated state. A bypassed gate or simulated worker is diagnostic evidence, not isolation acceptance.
- Before claiming completion or closing a ticket, record the tested revision, commands, and results for the criteria. Distinguish local tests, Docker integration, deployment, and production acceptance; identify anything still pending.
