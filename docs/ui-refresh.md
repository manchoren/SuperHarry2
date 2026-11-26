# SuperHarry interface refresh

## Problem Statement

Users encounter inconsistent, mostly unstyled screens and cannot choose a dark appearance. The chat layout and navigation need clearer hierarchy across desktop and mobile while preserving the application's existing capabilities.

## Solution

Refresh the user-facing interface with a coherent identity, shared navigation, readable chat and forms, and accessible light, dark, and system themes. Use the current ChatGPT web interface as inspiration for conversational clarity while giving SuperHarry its own appearance.

## User Stories

1. As a user, I want a consistent interface across application screens, so that navigation feels familiar.
2. As a user, I want a desktop sidebar with conversations, so that I can start and resume chats easily.
3. As a mobile user, I want collapsible navigation, so that I have room to read and compose messages.
4. As a user, I want a readable conversation and prominent composer, so that I can focus on the chat.
5. As a user, I want the active model and streaming progress visible, so that I understand the response.
6. As a user, I want incomplete replies and retry controls visible, so that I can recover deliberately.
7. As a user, I want memory consent visible near the composer, so that I control its inclusion in requests.
8. As a user, I want attachment instructions and scheduled-task controls preserved, so that I can use existing workflows.
9. As a user, I want navigation to inputs, schedules, memories, skills, and available CSV results, so that I can reach my work.
10. As a user, I want light, dark, and system themes, so that I can choose a comfortable appearance.
11. As a user, I want my theme choice remembered in this browser, including on sign-in, so that it persists between visits.
12. As a keyboard user, I want visible focus and usable controls, so that I can navigate the application.
13. As a user, I want readable contrast, comfortable touch targets, and reduced-motion support, so that I can use the interface comfortably.

## Implementation Decisions

- Cover chat, sign-in, memories, schedules, inputs, CSV, and skills; retain the existing Django administration appearance.
- Use warm neutral surfaces, a muted blue accent, restrained decoration, rounded controls, a system font stack, and monospace for code. Do not load fonts from an external service.
- Use shared navigation on signed-in pages; show conversation history on chat. Provide a collapsible menu on narrow screens.
- Default to system appearance; store an explicit Light, Dark, or System choice locally in the browser. Respect subsequent system changes while System is selected. Different browsers and devices can have different preferences.
- Preserve the existing workflows, authorization, explicit memory opt-in, attachment guidance, statuses, streaming, scheduling, and explicit retry. Template and client-side changes may support the design without changing product semantics.
- Document the agreed visual and accessibility direction in the project documentation.

## Testing Decisions

- Test observable behavior at the browser-facing interface, rather than CSS implementation details. Use the existing Django view tests as prior art for preserving workflows.
- Verify desktop and mobile layouts, navigation, keyboard focus, contrast, reduced motion, and Light, Dark, and System selection in a real browser. Check persistence on reload and sign-in and system changes when System is selected.
- Exercise representative chat, streaming/status, memory opt-in, attachment, schedule and retry flows, plus navigation and forms across the covered pages. Use isolated test data and the existing controlled gateway where needed.
- Record the tested revision, commands and results, separating local and Docker checks from any deployed or live-provider verification.

## Out of Scope

- Redesign of Django administration, changes to model routing, chat semantics, account permissions, or new product features.
- Cross-device theme synchronization and externally hosted fonts.

## Further Notes

Use Docker Desktop for applicable runtime validation. The visual reference informs the design; SuperHarry keeps its own identity.

## Validation evidence

- On 2026-11-25, against the uncommitted changes based on `89ba9ec`, 147 existing chat, memory, scheduling, CSV, and skills Django tests passed locally and in a network-isolated Docker container with the checkout mounted read-only and synthetic credentials.
- An isolated local Django server with a temporary database and a synthetic user was exercised in headless Edge using `python -m tests.ui_browser_smoke`. Checks covered sign-in, navigation to inputs, schedules, memories, skills and CSV, desktop rendering, mobile menu and horizontal overflow, theme persistence across reload and sign-in, system color-scheme changes, keyboard focus, and reduced-motion preference detection. The browser also saved a memory, enabled conversation memory, uploaded a synthetic CSV input, canceled a synthetic schedule, and submitted a retry and a new chat against a delayed controlled gateway, observing the waiting state and the saved replies.
- Calculated contrast for the main text, muted text, links and primary buttons was at least 6:1 for the checked light and dark color pairs. This is not a complete accessibility audit.
- Axe-core checks reported no WCAG 2 A/AA or 2.1 A/AA violations on sampled sign-in, chat, inputs, schedules, memories, skill and CSV screens in either theme. Automated checks do not constitute a complete accessibility audit. Deployed-service and live-provider checks were not performed.
- The public ChatGPT page structure and an [official ChatGPT overview illustration](https://chatgpt.com/overview/) were reviewed for sidebar navigation, conversation spacing and the composer. An attempt to inspect the live rendered interface in the automated browser encountered a human-verification screen, so the live visual design could not be directly verified there.

The browser smoke requires Playwright for Python, an installed Chromium-compatible browser, and a local copy of axe-core. Set `UI_BROWSER_PATH` and `UI_AXE_PATH` to their absolute paths before running it. The smoke uses a temporary database and a synthetic local gateway; it does not contact the deployed application or a live provider.
