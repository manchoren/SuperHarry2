# Architecture

## Chat and deployment

Use Docker Desktop and Docker Compose on Windows for the current owner-operated deployment, with Django bound to localhost. Use Django templates and client-side streaming for the chat interface. Run isolated, synthetic-data checks against the current Docker runtime; record those separately from checks against the running deployment and the additional gate in [milestones](milestones.md#3-skills-and-bundled-execution). Build the Django image from the official rolling [Debian Sid slim image](https://hub.docker.com/_/debian/tags?name=sid-slim). Pin the tested digest and refresh it regularly.

Django owns accounts, conversations, messages, turn status, limits, and the active chat route selection. Persist chat history and memory in separate Django models in one SQLite database on a Docker volume. Keep write transactions short and revisit PostgreSQL before concurrent use or multiple database-writing workers.

## Users and permissions

Bootstrap one Django superuser for installation and recovery. Use built-in Django user administration and an administrator group with explicit permissions to manage regular accounts, routes, and limits. Reserve privilege grants and administrator membership changes for the superuser. Keep public registration closed.

Regular users can sign in, see the active model, and use chat. Check ownership server-side for conversations, memories, uploaded files, and sandbox results, including history and download endpoints. Keep private chat content out of routine admin views. As an explicit owner-approved exception, administrators can view, download, and delete uploaded and generated files belonging to any user through file history; record the administrator, owner, file, action, and time without recording file contents. Period-based administrative deletion requires an explicitly selected user and a confirmation of the affected files.

Provide global daily message and maximum response length limits with optional per-user overrides. Allow one active response per user initially. Count a turn when Django submits it to OmniRoute and count the same submission only once. Local validation rejections are free. Reset the counter at midnight in the configured deployment timezone, initially `America/Guayaquil`. Persist partial replies and mark interrupted turns incomplete. Offer an explicit retry.

## Model gateway

Django sends streaming inference requests to OmniRoute's OpenAI-compatible chat endpoint. OmniRoute owns provider connections, upstream credentials, the model catalog, routing, and upstream quotas. The installer seeds the OpenRouter route for `google/gemma-4-26b-a4b-it:free` and verifies its canonical ID against OmniRoute. Groq and Gemini can be provisioned as additional connections. Free mode uses the configured free route and surfaces unavailability or quota exhaustion. Paid fallback is disabled.

Django admin lists available routes, adds or updates model entries on existing provider connections, tests a route, and changes the default. Use the OmniRoute dashboard for provider account creation, upstream credentials, and OAuth. Keep separate server-side inference and management credentials. Use the smallest management scope supported by the selected OmniRoute version and limit the Django actions that can use it. [OmniRoute API reference](https://github.com/cheaper-inference/OmniRoute/wiki/API-Reference) · [Management authentication](https://github.com/cheaper-inference/OmniRoute/wiki/Management-Auth)

## Memory and skills

Sending a chat message authorizes the selected provider to receive that message and the conversation context needed for the reply. Users explicitly save durable memories and can inspect, edit, or delete them. Include relevant saved memories in hosted requests only when the user enables memory for that conversation. Default to off and show the setting near the composer. Start with bounded retrieval from SQLite.

Discover repository-managed skills and locally stored skill drafts dynamically. The current interface calls the latter “VM skills”; this denotes their application-managed origin, not a requirement to start a separate VM. Repository definitions are read-only in the application; local skills are managed by their owners or administrators and shared for use after preparation. Reject conflicting names. The agent-driven selection and lifecycle are tracked separately from the initial skill runtime.

Prepare dependencies and execute skill scripts in disposable environments with no application credentials or Docker socket, and with explicit CPU, memory, time, and output limits. Pass only the inputs selected for each job and scope results to its owner. The sandbox has no direct network access. A host mediator handles explicit HTTP requests: only exact operator-registered public resources can be retrieved without approval; other requests require approval by the run owner for the displayed method, destination, headers, and data. Requests to private and special network addresses and redirects are blocked. Preparation may download wheels from PyPI through the mediator, then retains the selected wheels for offline execution. The CSV workflow is to move into an agent-selected skill; an explicit request to summarize the user's file will authorize that isolated processing.

## Debian package findings

Keep CVEs in Debian repository packages visible in scan reports and non-blocking by default. Explicitly review findings that could break sandbox isolation or expose credentials before release. Check the installed package version against the [Debian Security Tracker](https://security-tracker.debian.org/tracker/).
