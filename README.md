# SuperHarry2

SuperHarry2 is a Django chat application with a conversation sidebar, a message composer, and streaming replies. OmniRoute manages language models and providers. The current working environment is Docker Desktop on Windows. Users can request CSV summaries in chat through the isolated skill runtime.

- [Architecture and agreed decisions](docs/architecture.md)
- [Milestones and acceptance criteria](docs/milestones.md)
- [Docker and deployment runbook](docs/vm-runbook.md)

The Django recovery service can be started with Docker Compose using the [Docker and deployment runbook](docs/vm-runbook.md#start-the-django-recovery-service). The milestone document distinguishes current Docker checks from historical VM evidence and tracks remaining acceptance checks.

To provision Django and a private OmniRoute gateway, set the provider variables listed in [.env.example](.env.example) in a local `.env` file and run `python scripts/provision.py`. See [Provision](docs/vm-runbook.md#provision) for deployment options.

For an existing provisioned installation, run `python scripts/deploy.py` to deploy the current checkout, or `python scripts/deploy.py --watch` to redeploy after application changes.

In Django admin, the recovery superuser can create users and delegate regular account management. To delegate, enable `Staff status` on the user and add them to the `Administrators` group. Group membership and privilege grants are managed by the superuser.
