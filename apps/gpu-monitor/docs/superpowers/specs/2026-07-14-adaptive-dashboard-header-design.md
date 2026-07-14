# Adaptive dashboard header design

## Goal

Replace the separate dashboard header and network-filter row with one compact sticky header. Preserve all existing controls and filtering behavior while minimizing the header on intentional down-scroll and revealing it on up-scroll.

## Layout

- Desktop (`>= 768px`): one 56px expanded / 48px compact header row: title and live state on the left, existing `전체` / `내부망` / `외부망` filter buttons in the center, and controls on the right. Registration and logs stay directly accessible; delete, debug, view settings, and themes are available through an actions menu when space is constrained.
- Mobile (`< 768px`): one header surface, not a separate page-level filter row. Expanded height is 88px: title/live/actions on the first row and inline filters on the second. Compact height is 56px: title/live, current-filter button with a three-item filter menu, and an actions menu.
- The existing filter store, counts, and cookie persistence are reused. Filter buttons use `aria-pressed`.

## State machine

- `expanded`: forced at `scrollY <= 8`; 56px desktop / 88px mobile.
- `compact`: after `scrollY > 24`; 48px desktop / 56px mobile.
- `minimized`: after `scrollY >= 128` and 56px accumulated downward scroll motion. The header is translated out of view except for a visible 6px grip and a 20px reveal hit area.
- An upward accumulator of 12px returns `minimized` to `compact`.
- Scroll deltas smaller than 4px are ignored; changing direction resets the accumulator.

## Lock and motion rules

- Header focus, desktop pointer hover, or an open header menu locks the header in `compact` (or `expanded` at the page top).
- A form/delete modal leaves the header visually compact but the modal backdrop makes it inert.
- In minimized state, ordinary header controls are inert. The reveal strip restores compact mode on hover/click/tap; the first upward mobile scroll also restores compact mode.
- Transitions use `cubic-bezier(.22, 1, .36, 1)` at 180ms expanded→compact, 160ms compact→minimized, and 140ms minimized→compact.
- `prefers-reduced-motion: reduce` makes state changes immediate and disables auto-minimization; header is expanded at the page top and compact otherwise.

## Non-goals

No backend, collector, WebSocket, filter-data, route, card, drag-order, or dialog behavior changes. No new package.

## Acceptance criteria

1. The old standalone network navigation row is gone.
2. Network filtering and counts remain correct and expose `aria-pressed`.
3. Desktop/mobile heights and scroll thresholds match this specification.
4. Focus, menus, hover, and dialogs prevent an inaccessible hidden header.
5. All pre-existing actions remain reachable at desktop and mobile widths.
6. Reduced-motion disables the animated auto-hide.
7. `npm run check`, `npm run build`, and browser smoke tests pass.
