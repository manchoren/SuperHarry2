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

## Approved follow-up: composer and table polish (2026-11-28)

The following decisions refine the chat composer and shared data tables. They are tracked in [issue #90](https://github.com/manchoren/SuperHarry2/issues/90) and supersede the earlier unchecked-memory default in this document and in the original UI refresh acceptance; they do not change memory ownership or retrieval boundaries.

- Put model selection, supported reasoning controls, and the per-conversation memory toggle in one keyboard-operable Model disclosure. Show reasoning only while the disclosure is open and only when the selected model supports it.
- Show the active model at the composer's upper right. Keep its full accessible name and truncate the visible text with an ellipsis when space is limited. Align the Model label and selected value as one control.
- Replace the visible memory sentence with the shared brain icon. Keep an accessible name and a visible tooltip on hover or keyboard focus, and expose whether memory is on or off. New conversations start with memory enabled; the user can turn it off before the first message. Existing conversations keep their stored value and continue to show it read-only after creation.
- Use the existing outline icon artwork in the navigation rail without a heavy visual weight.
- Across user-facing tables with an Actions column, let data columns use available width and keep Actions only as wide as its controls require. Preserve responsive behavior. Show this shared table behavior in the development catalog.
- Add vertical space between the Inputs upload form and its table.
- Leave Django Admin outside this visual scope.

### Validation seam

- Django chat tests use an isolated database and controlled gateway to verify that new conversations default to memory enabled, can opt out before their first message, preserve existing settings, and send only the owning user's eligible memories when enabled.
- The existing Playwright UI browser smoke is extended to verify the Model disclosure, accessible brain toggle and status, model ellipsis, supported reasoning visibility, shared table action sizing, and Inputs upload spacing at desktop and narrow widths. Use synthetic data and the controlled gateway; do not use the deployed service or live model provider for these checks.
- Review browser screenshots for the composer, navigation rail, memory and Inputs views, and representative tables in light and dark themes. Preserve keyboard access, visible focus, tooltip access, and narrow-screen behavior.

## Validation evidence

- On 2026-11-25, against the uncommitted changes based on `89ba9ec`, 147 existing chat, memory, scheduling, CSV, and skills Django tests passed locally and in a network-isolated Docker container with the checkout mounted read-only and synthetic credentials.
- An isolated local Django server with a temporary database and a synthetic user was exercised in headless Edge using `python -m tests.ui_browser_smoke`. Checks covered sign-in, navigation to inputs, schedules, memories, skills and CSV, desktop rendering, mobile menu and horizontal overflow, theme persistence across reload and sign-in, system color-scheme changes, keyboard focus, and reduced-motion preference detection. The browser also saved a memory, enabled conversation memory, uploaded a synthetic CSV input, canceled a synthetic schedule, and submitted a retry and a new chat against a delayed controlled gateway, observing the waiting state and the saved replies.
- Calculated contrast for the main text, muted text, links and primary buttons was at least 6:1 for the checked light and dark color pairs. This is not a complete accessibility audit.
- Axe-core checks reported no WCAG 2 A/AA or 2.1 A/AA violations on sampled sign-in, chat, inputs, schedules, memories, skill and CSV screens in either theme. Automated checks do not constitute a complete accessibility audit. Deployed-service and live-provider checks were not performed.
- The public ChatGPT page structure and an [official ChatGPT overview illustration](https://chatgpt.com/overview/) were reviewed for sidebar navigation, conversation spacing and the composer. An attempt to inspect the live rendered interface in the automated browser encountered a human-verification screen, so the live visual design could not be directly verified there.

The browser smoke requires Playwright for Python, an installed Chromium-compatible browser, and a local copy of axe-core. Set `UI_BROWSER_PATH` and `UI_AXE_PATH` to their absolute paths before running it. The smoke uses a temporary database and a synthetic local gateway; it does not contact the deployed application or a live provider.
