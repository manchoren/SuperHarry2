# UI contribution guide

## Reuse

User-facing Django pages extend `ui/base.html` and use the first-party component styles in `superharry/static/superharry/components.css`. Compose screens from the shared heading, action, field, feedback, status, table, empty-state, and confirmation patterns. Keep data access and domain actions in their existing views and forms.

Use the outline icon symbols from the shared icon sprite and give icon-only controls an accessible name, visible focus, and a visible label on hover or keyboard focus. Keep the active destination marked with `aria-current="page"`. Preserve each feature's server-side permission checks and existing behavior.

## Extending

When a screen needs a reusable pattern, add it to the shared stylesheet and show it in `docs/ui-components/index.html` in the same change. Include a synthetic example and copyable markup. The application catalog uses the same first-party stylesheet and is disabled unless `UI_COMPONENT_CATALOG_ENABLED=1` is explicitly configured for development or tests.

Feature-specific styles may support interactions unique to that screen. Keep them scoped to the feature and use the shared color, type, spacing, and focus tokens. Check keyboard focus, reduced motion, themes, narrow layouts, and representative accessibility on the browser-facing interface.
