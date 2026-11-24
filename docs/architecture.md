# Architecture

## Chat and deployment

Start with an owner-operated deployment on Windows using Docker Compose and a localhost-bound Django app. Use Django templates and client-side streaming for the chat interface. Reuse the Compose setup in a dedicated Linux VM for deployment verification and sandbox testing. Build the Django image from the official rolling [Debian Sid slim image](https://hub.docker.com/_/debian/tags?name=sid-slim). Pin the tested digest and refresh it regularly.

Django owns accounts, conversations, messages, turn status, limits, and the active chat route selection. Persist chat history and memory in separate Django models in one SQLite database on a Docker volume. Keep write transactions short and revisit PostgreSQL before concurrent use or multiple database-writing workers.

## Users and permissions

Bootstrap one Django superuser for installation and recovery. Use built-in Django user administration and an administrator group with explicit permissions to manage regular accounts, routes, and limits. Reserve privilege grants and administrator membership changes for the superuser. Keep public registration closed.

Regular users can sign in, see the active model, and use chat. Check ownership server-side for conversations, memories, uploaded files, and sandbox results, including history and download endpoints. Keep private chat content out of routine admin views.

Provide global daily message and maximum response length limits with optional per-user overrides. Allow one active response per user initially. Count a turn when Django submits it to OmniRoute and count the same submission only once. Local validation rejections are free. Reset the counter at midnight in the configured deployment timezone, initially `America/Guayaquil`. Persist partial replies and mark interrupted turns incomplete. Offer an explicit retry.

## Model gateway

Django sends streaming inference requests to OmniRoute's OpenAI-compatible chat endpoint. OmniRoute owns provider connections, upstream credentials, the model catalog, routing, and upstream quotas. The VM installer seeds the Nous Portal route for `upstage/solar-mini4:free`. The deployed route ID comes from OmniRoute. Free mode uses the configured free route and surfaces unavailability or quota exhaustion. Paid fallback is disabled.

Django admin lists available routes, adds or updates model entries on existing provider connections, tests a route, and changes the default. Use the OmniRoute dashboard for provider account creation, upstream credentials, and OAuth. Keep separate server-side inference and management credentials. Use the smallest management scope supported by the selected OmniRoute version and limit the Django actions that can use it. [OmniRoute API reference](https://github.com/cheaper-inference/OmniRoute/wiki/API-Reference) · [Management authentication](https://github.com/cheaper-inference/OmniRoute/wiki/Management-Auth)

## Memory and skills

Sending a chat message authorizes the selected provider to receive that message and the conversation context needed for the reply. Users explicitly save durable memories and can inspect, edit, or delete them. Include relevant saved memories in hosted requests only when the user enables memory for that conversation. Default to off and show the setting near the composer. Start with bounded retrieval from SQLite.

Store skills as repository-managed Markdown with a declared purpose and allowed tools. Start with a text-only skill. For the first execution workflow, a skill can propose the bundled CSV summarizer and a user must approve running it on a specific file they own. Django validates the request and submits a job to a separate runner.

Run each job in a disposable environment with network disabled by default, no application credentials or Docker socket, and explicit CPU, memory, time, and output limits. Scope inputs and results to the approved job and clean up after execution. Model-generated programs are a later milestone with their own isolation acceptance.

## Debian package findings

Keep CVEs in Debian repository packages visible in scan reports and non-blocking by default. Explicitly review findings that could break sandbox isolation or expose credentials before release. Check the installed package version against the [Debian Security Tracker](https://security-tracker.debian.org/tracker/).
