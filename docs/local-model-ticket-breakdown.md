# Proposed tickets for issue #73

## 1. Configure and use a named Ollama model

**Blocked by:** None.

**What to build:** An administrator configures and downloads a local Ollama model during provisioning, selects its route as the current default, and a user receives a streamed response through OmniRoute.

**Acceptance criteria**
- [ ] Interactive and unattended provisioning configure a named model, persist it across restarts, and verify readiness; existing secrets and deployment data are preserved.
- [ ] The administrator can see, test and select the local route as the default; a user can send a message through it.
- [ ] The administrator can restore Free when Ollama is down; an unavailable local route yields a clear error.
- [ ] Explicit local selection does not fall back; errors and partial streams are handled clearly.
- [ ] Isolated Docker Desktop and real current-host Ollama/OmniRoute checks record model, GPU availability and results.

## 2. Select a model per conversation

**Blocked by:** None.

**What to build:** Users can choose Free or a named enabled model for a conversation and have that choice retained.

**Acceptance criteria**
- [ ] The selector lists Free and enabled named routes; explicit choices use only the selected route.
- [ ] An unavailable model is visibly disabled with an explanation.
- [ ] Each conversation retains its selection; scheduled tasks keep their separate preferences.
- [ ] Changing selection in an existing conversation requires confirmation and adds a history event.
- [ ] Server-side ownership and route validation protect changes; browser and streaming tests cover the flow.

## 3. Route Auto from Free to local

**Blocked by:** 1, 2.

**What to build:** New conversations default to Auto, with OmniRoute preferring Free and falling back to local inference when necessary.

**Acceptance criteria**
- [ ] Auto uses only configured Free and local routes, falls back on eligible quota or temporary availability failures and prefers Free again on later recovery.
- [ ] The history records effective model changes when reliably identifiable and otherwise reports uncertainty.
- [ ] A response already streaming is preserved as partial on failure and requires an explicit retry; a clear error appears if neither route is available.
- [ ] Provisioning establishes and verifies the routing policy, and tests distinguish synthetic and real gateway behavior.

## 4. Control and temporarily display reasoning

**Blocked by:** 3.

**What to build:** Users select verified reasoning levels per conversation and see provider-delivered reasoning temporarily during a response.

**Acceptance criteria**
- [ ] A compact slider defaults to off and exposes only reliably verified model levels; unknown or unsupported capabilities are hidden or disabled.
- [ ] The chosen setting persists per conversation; when Auto falls back to an unsupported model it continues without reasoning and records that change.
- [ ] Reasoning actually delivered by a provider appears temporarily in a readable compact panel and disappears after completion; it is not persisted.
- [ ] Browser and streaming tests cover capability changes, fallback and reload.

## 5. Compact conversation context manually

**Blocked by:** 3.

**What to build:** A user can compact a conversation and see the estimated context remaining without losing visible history.

**Acceptance criteria**
- [ ] The context doughnut includes response reservation, uses a conservative fallback limit in Auto, and shows unknown when capacity cannot be verified; administrators can configure limits.
- [ ] A manual button summarizes older messages using the selected route; Auto follows its ordinary routing rules, and long input is handled in chunks as needed.
- [ ] System instructions, the first message and the latest three messages remain intact by default; administrators can configure the message counts and tool relationships remain valid.
- [ ] Original history remains visible; summaries persist, update as needed and become invalid when covered messages change or are deleted.
- [ ] Compaction requests do not consume the daily user-message allowance; provider quota still applies. Failures and no-op requests are clear.
- [ ] Browser, ownership, gateway and context-boundary tests exercise the complete flow.

## 6. Compact automatically before context overflow

**Blocked by:** 5.

**What to build:** Long conversations compact automatically when required by the selected route or its local fallback.

**Acceptance criteria**
- [ ] Before sending, context is checked against a safe applicable capacity and automatically compacted when necessary, including in Auto.
- [ ] The history records automatic compaction; if the resulting request still cannot fit or compaction fails, the user sees a clear error and original messages remain intact.
- [ ] Conversation context, reserved response space, unknown limits, chunking, quota, fallback and repeated compaction are covered by observable tests.
- [ ] End-to-end Docker Desktop verification covers the agreed flows and distinguishes controlled gateway tests from live provider and local-model results.
