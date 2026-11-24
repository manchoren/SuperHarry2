# SuperHarry2

SuperHarry2 is a planned Django chat application with a conversation sidebar, a message composer, and streaming replies. OmniRoute manages language models and providers. Later milestones add user-controlled memory, repository-managed skills, and isolated execution of a bundled CSV summarizer.

- [Architecture and agreed decisions](docs/architecture.md)
- [Milestones and acceptance criteria](docs/milestones.md)
- [VM and Docker runbook](docs/vm-runbook.md)

The Django recovery service can be started with Docker Compose using the [VM and Docker runbook](docs/vm-runbook.md#start-the-django-recovery-service). The milestone document tracks the remaining product acceptance checks.

In Django admin, the recovery superuser can create users and delegate regular account management. To delegate, enable `Staff status` on the user and add them to the `Administrators` group. Group membership and privilege grants are managed by the superuser.
