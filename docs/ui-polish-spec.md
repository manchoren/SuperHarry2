# Composer and table visual polish

Tracking issue: [#90](https://github.com/manchoren/SuperHarry2/issues/90).

## Problem

The chat composer shows reasoning and memory controls outside the model selector, the selected model competes for space with other controls, and the Model and Auto controls do not align. Navigation icons look too heavy. Several tables give Actions as much space as their primary content, and the Inputs upload form sits too close to its table.

## User outcome

Users can reach model, reasoning, and memory controls from one compact disclosure, identify the active model, and scan tables without the action controls taking over the layout.

## Requirements

1. Put the model selector, supported reasoning control, and per-conversation memory toggle inside one keyboard-operable Model disclosure.
2. Keep reasoning hidden while the disclosure is closed. Show it only when the selected model supports reasoning.
3. Show the active model at the upper right of the composer. Truncate its visible name with an ellipsis when needed while retaining the full accessible name.
4. Align Model and its selected value as one control.
5. Replace the visible “Use saved memories in this conversation” sentence with the brain icon inside the Model disclosure. Give it an accessible name, a visible tooltip on hover and keyboard focus, and an accessible on/off state.
6. New conversations default to memory enabled. Users can turn it off before sending the first message. Existing conversations keep their stored setting; after creation, display that setting read-only. Retrieval remains bounded to the conversation owner's memories.
7. Keep the existing outline sidebar icons and reduce their heavy visual appearance without changing their destinations or active state.
8. In every user-facing table with an Actions column, let content columns take the remaining width and size Actions to its controls. Preserve responsive behavior and demonstrate the shared table layout in the development catalog.
9. Add vertical separation between the Inputs upload form and its table.
10. Do not redesign Django Admin or change authorization, memory ownership, or model routing behavior.
11. Apply consistent dismissal behavior to the Attach files, Skills, and Model composer disclosures. Keep at most one open: opening another closes the current one, and activating the open disclosure's button closes it. A click outside the open panel or pressing Escape closes it. Interactions inside a panel keep it open while the user works. Choosing files, a skill, or a model closes the corresponding panel. Close Model as soon as a model is chosen, even if the subsequent confirmation is declined or saving fails.

## Acceptance criteria

- Closing the Model disclosure hides reasoning and memory selection; opening it makes model, supported reasoning, and the brain toggle available by keyboard and pointer.
- Selecting a model keeps Model and its selected value aligned. Long active model names visibly end in an ellipsis while the full model name remains available to assistive technology.
- The brain control has a meaningful accessible name, visible tooltip, and exposed current state. A new conversation starts enabled; opting out before the first send is persisted. Existing conversations retain their stored setting.
- A controlled gateway receives only relevant memories owned by the conversation owner when memory is enabled; disabled conversations omit them.
- User-facing tables with Actions give the content columns the available width and reserve only the needed width for actions. Django Admin is excluded.
- Navigation rail icons use the existing outline artwork with a lighter visual weight and retain active indication and keyboard focus.
- Inputs has a visible gap between upload and table sections.
- The Attach files, Skills, and Model disclosures allow at most one open panel. Activating another disclosure closes the previous one and opens the new one; activating the open disclosure toggles it closed. An outside click or Escape closes the open panel, while interaction inside it keeps it open. Choosing files, a skill, or a model closes its panel; Model closes immediately even if the confirmation is declined or saving fails.
- Desktop and narrow layouts remain usable in light and dark themes, with keyboard operation and visible focus.

## Test seam

- Django behavior: isolated chat tests with a controlled gateway verify default-on for new conversations, opt-out before first send, persistence, unchanged existing settings, memory ownership, and outbound request contents.
- Browser behavior: extend `tests/ui_browser_smoke.py` using synthetic data and the controlled gateway. Verify all three composer disclosures, their exclusive-open and dismissal behavior, closing after selections, the accessible memory toggle and status, reasoning visibility, model truncation, table action sizing, and Inputs spacing at desktop and narrow widths.
- Inspect screenshots of the changed surfaces in light and dark themes. These are local checks; deployment and live-provider acceptance are outside this spec.
