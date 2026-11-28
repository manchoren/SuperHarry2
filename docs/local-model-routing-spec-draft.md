# Local model routing and context management

## Problem Statement

When the free hosted model runs out of quota or becomes unavailable, a conversation cannot continue. Users also cannot select a model, control supported reasoning, or manage a conversation that exceeds a model's context window.

## Solution

Provide per-conversation model selection, an OmniRoute-managed free-first route with an Ollama fallback, reasoning controls, and context compaction. Preserve the original conversation and make model changes and automatic compaction visible in its history.

## User Stories

1. As a user, I want new conversations to use Auto, so that routing works without configuration.
2. As a user, I want to choose Auto, Free, or an enabled model by name, so that I can control inference.
3. As a user, I want my choice retained for a conversation, so that subsequent messages use it.
4. As a user, I want explicit selections to use only the chosen route, so that they do not silently fall back.
5. As a user, I want Auto to prefer Free and fall back to local inference on quota exhaustion or temporary unavailability, so that I can continue chatting.
6. As a user, I want Auto to prefer Free again when it recovers, so that the normal route is restored.
7. As a user, I want no paid routes in Auto, so that automatic routing does not incur paid inference charges.
8. As a user, I want unavailable local models visibly disabled with an explanation, so that I understand my choices.
9. As a user, I want Free to remain usable if Ollama is unavailable, so that local failure does not disable chat.
10. As a user, I want a clear error when the required route is unavailable, so that I know why a request failed.
11. As a user, I want a warning and confirmation before changing selection in an existing conversation, so that I understand possible coherence issues.
12. As a user, I want manual and automatic model changes recorded in history, so that I can understand conversation changes.
13. As a user, I want an honest unknown-model warning when the effective model cannot be identified reliably, so that history does not misattribute a response.
14. As a user, I want partial responses retained on interruption with an explicit retry, so that I do not lose received text or mix responses.
15. As a user, I want a compact reasoning level slider based on verified model capabilities, so that I can control supported reasoning.
16. As a user, I want reasoning disabled by default, so that it is opt-in.
17. As a user, I want unsupported or unknown reasoning controls disabled or hidden, so that the interface does not promise unsupported levels.
18. As a user, I want Auto to continue without reasoning when a fallback model does not support it and to record that change, so that I can continue without being misled.
19. As a user, I want provider-delivered reasoning text temporarily visible during streaming, so that I can see progress.
20. As a user, I want temporary reasoning to disappear at completion and not be persisted, so that reopening a conversation shows only the response.
21. As a user, I want a context usage doughnut next to the selector, so that I can estimate remaining space.
22. As a user, I want the context estimate to reserve response space and account for the local fallback in Auto, so that a fallback is less likely to exceed its limit.
23. As a user, I want unknown context capacity clearly indicated, so that an estimate is not presented as fact.
24. As a user, I want a button to compact context immediately, so that I can make room before sending.
25. As a user, I want automatic compaction when needed for the selected route, so that long conversations can continue.
26. As a user, I want original messages retained in the visible history, so that I can review the complete conversation.
27. As a user, I want the system instructions, first message and latest three messages retained by default, so that important context remains intact.
28. As a user, I want the remaining older messages summarized and summaries updated as the conversation grows, so that future requests fit.
29. As a user, I want compacted content invalidated when included messages are edited or deleted, so that stale information is not reused.
30. As a user, I want an explicit error if compaction cannot make the request fit, so that history is not silently lost.
31. As a user, I want internal compaction requests excluded from my daily message limit, so that managing context does not count as another user message.
32. As an administrator, I want to configure how many first and recent messages are preserved, so that compaction meets deployment needs.
33. As an administrator, I want to configure an unknown model context limit, so that the application can estimate capacity safely.
34. As an administrator, I want enabled OmniRoute models listed in the selector, so that models can be managed without changing interface code.
35. As an administrator, I want installation to configure Ollama, download a named model and persist it across deployments, so that local fallback is repeatable.
36. As an administrator, I want unattended configuration and readiness checks, so that provisioning can be automated.
37. As an owner of a scheduled task, I want its preferred model to remain independently configured, so that chat selection does not alter scheduled work.

## Implementation Decisions

- OmniRoute owns the free-first automatic routing policy and upstream credentials; constrain Auto to configured free and local routes, never paid routes. Verify actual deployed version capabilities and effective model reporting instead of assuming current upstream behavior.
- Persist selection and supported reasoning preference per conversation; new conversations default to Auto. Explicit model selection does not trigger fallback. Record manual and automatic changes as conversation history events and warn honestly when model identity cannot be verified.
- Keep partial streaming replies and explicit retry after an interrupted response. Do not splice a local response into a partially streamed hosted response.
- Provision Ollama on the deployment host with configurable model download, persistent model data, readiness checks, and a noninteractive configuration path. A local outage does not prevent the rest of the application from starting.
- Reasoning controls expose only reliably known capabilities, default to off, and use a compact slider inspired by the supplied design. Display only reasoning text actually delivered by the provider temporarily; never persist it. Auto continues without reasoning on unsupported fallback and reports that change.
- Implement compaction in core chat context preparation, not as a skill. Retain original history, system instructions, configurable first and recent message counts (defaults one and three), and valid tool-call/result relationships. Persist summaries per conversation and invalidate stale summaries when covered messages change.
- Use the selected route for compaction, including free-first Auto behavior; chunk summarization if necessary. Communicate failure if the result cannot fit. Compaction can consume provider quota but does not count as a daily user message.
- Estimate context usage including response reservation; Auto uses a conservative limit covering fallback. Show unknown when capacity is not reliably available and allow administrator configuration.
- Keep scheduled task model preferences independent. Preserve existing ownership, memory-consent, provider credential, and quota boundaries.

## Testing Decisions

- Test externally observable behavior at the chat HTTP and streaming boundary using the existing synthetic gateway pattern: route selection and persistence, failover and recovery, partial stream handling, model events, reasoning, quota accounting, context construction, compaction, invalidation, ownership, and unavailable routes.
- Exercise the interface through the existing browser smoke approach: selector, confirmation, disabled models, history events, slider, transient reasoning, context indicator and manual compaction.
- Validate provisioning and persistence through isolated Docker Desktop integration, and separately verify a real Ollama and OmniRoute route on the current 16 GB RAM, RTX 2060 Mobile 6 GB machine. Record GPU availability, model and actual results; do not equate synthetic tests with live integration.
- Prefer tests of behavior and contracts over internal implementation details. Verify administration, configured limits, context edge cases and failures, and scheduled-task independence at their existing public boundaries.

## Out of Scope

- Paid model fallback, changing scheduled-task selection from chat, deleting original history as part of compaction, persisting reasoning text, and adding compaction as a skill.

## Further Notes

Model capabilities, fallback behavior, model identity and context limits must be verified against the deployed OmniRoute and Ollama versions. If reliable metadata is unavailable, show unknown or disable unsupported controls instead of guessing. The local model is selected by the administrator based on the host's resources.
