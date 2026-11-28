# Issues #73–#79 validation

## Tested snapshot

Working tree based on `cc6f9011e4740dd23e07dae9e191c7fdd89046fb`, with the implementation and test changes present in the local worktree. The changes have not been committed or deployed.

## Evidence

- `DJANGO_SETTINGS_MODULE=superharry.settings`, synthetic `DJANGO_SECRET_KEY`; `python -m django test tests.test_chat.ChatTests tests.test_route_admin tests.test_gateway tests.test_provision -v 1`: 118 tests passed; Django system checks passed. This covers per-conversation route selection, confirmation, ownership, explicit-route failures, Auto combo selection and model-event uncertainty, reasoning capability validation and streaming, compaction/no-op/error/ownership/invalidation/growth, quota, route administration, and provisioning.
- `python -m tests.ui_chat_skill_mentions_smoke` with `UI_BROWSER_PATH` set to Microsoft Edge: passed. Browser flow verified selection and confirmation, persisted reasoning preference, transient non-persisted reasoning, context-capacity display, manual-compaction no-op, unavailable models, and mobile layout.
- `python tests/ollama_gateway_smoke.py`: passed on Docker Desktop with an isolated OmniRoute project, volume, credentials, and Django database. The host Ollama model `qwen3.5:4b` streamed a response through Django and OmniRoute; the Auto combo also streamed a response; the local route remained available after restarting OmniRoute. Auto's effective model metadata was unavailable, and the application recorded that identity as unknown. Auto targets were `openrouter/google/gemma-4-26b-a4b-it:free` followed by `superharry-local/qwen3.5:4b`. This run did not inject a Free-provider failure, so it does not establish which target served Auto or directly demonstrate failover. Host reported NVIDIA GeForce RTX 2060, 6144 MiB; Ollama reported 100% GPU processing.
- `python -m django makemigrations --check --dry-run`: no changes detected.
- `git diff --check`: passed (Git reported only configured LF-to-CRLF conversion notices).

## Ticket disposition

- #74: configured, named Ollama model provisioning and readiness, isolated persistence/restart, real local route through Django, and GPU result verified.
- #75: route selection, confirmation, per-conversation persistence, unavailable-model state, ownership, and independent scheduled model preference covered by HTTP and browser tests.
- #76: provisioning creates a priority Auto combo containing only the verified Free route and configured local route, in that order. Isolated Docker exercised the real local route and a successful Auto request. OmniRoute documents priority combos as trying the first model and falling back on error ([User Guide](https://github.com/cheaper-inference/OmniRoute/wiki/User-Guide)); the live run did not inject a Free-provider failure, and Auto's effective model metadata was not reported, so which target served the request and failover itself were not directly observed. The app recorded the model identity as unknown. No production deployment was performed.
- #77: only advertised reasoning levels are selectable; preference persists per conversation, temporary streamed reasoning is not saved, and Auto records disabled reasoning when a fallback capability is known to be unsupported.
- #78: manual compaction uses the conversation's selected route (Auto resolves through the configured Auto route); visible history remains intact. Tests cover no-op, ownership denial, summary reuse and growth, invalidation on edits/deletions, configured preservation, quota independence, unknown capacity, and chunked summarization implementation.
- #79: Auto context is checked with response reservation and a conservative configured capacity; older turns compact before dispatch. Tests cover success, history event, no-summarizable-history failure, intact original rows, and a second compaction as the history grows.
- #73: parent acceptance is covered by the evidence above. Deployment and production acceptance remain outside this local Docker Desktop validation and are not claimed.
