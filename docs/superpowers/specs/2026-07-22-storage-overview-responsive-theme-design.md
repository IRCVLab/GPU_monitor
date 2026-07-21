# Storage Overview Responsive and Theme Transition Design

Date: 2026-07-22
Status: Implemented and verified
Scope: Storage Monitor overview layout, card polish, and light/dark transition

## Goal

Make the Storage Monitor overview feel like the same Clean product family as GPU Monitor while preserving dense mount-first information. The overview must stop stretching cards across the entire viewport, resize every card consistently, keep server order predictable, and switch themes with a circular reveal whose origin is the exact visible theme icon center and which has no secondary flash.

## Selected Direction: A — Centered Constrained Overview

The overview uses a centered content shell with a maximum width of 1440px. The header inner content uses the same alignment shell so its left and right edges line up with the cards. Detail views remain wide and are not constrained by the overview-specific maximum width.

### Width contract

- Overview content maximum width: 1440px.
- Desktop outer gutter: 24px.
- Compact outer gutter: 20px, then 12px on narrow mobile.
- Card gap: 14px.
- Three columns on wide overview containers.
- Two columns when a card would become narrower than the supported dense-card minimum.
- One column on narrow tablet/mobile widths.
- Breakpoints are derived from overview container width, not unrelated detail-page width.

At viewports wider than 1488px, the overview remains centered and cards no longer grow. At narrower widths, all cards share the same column width and shrink uniformly until the next column-count breakpoint.

## Alternatives Considered

### Equal-height row grid

Rejected because servers have different mount counts. Equal rows leave large empty areas and reduce the mount-first density that is central to this product.

### Fixed-width auto-fill cards

Rejected because the right edge becomes ragged and column-count changes feel arbitrary. It also weakens alignment with GPU Monitor's deliberate dashboard grid.

### Unbounded shortest-column masonry

Rejected because it maximizes packing at the cost of server order, keyboard reading order, and predictable movement when a card changes height.

## Stable Ordered Masonry

The current layout seeds the first N cards into columns and applies shortest-column selection only to later cards. A global `lastStartRow` clamp then changes later positions disproportionately during resize. This is why the final cards appear to be the only cards reacting.

Replace that behavior with stable ordered column placement:

- Preserve server DOM order exactly.
- For N columns, assign each server to `index % N`.
- Stack cards vertically within their assigned column.
- Expanding or resizing a card moves only later cards in that column.
- A column-count change recalculates every card, not only cards after the first row.
- Clear all stale inline grid placement before every calculation.
- Trigger layout from both `ResizeObserver` and an explicit throttled window-resize fallback.
- Never create implicit columns or horizontal overflow.

This preserves the user's configured server order, keeps earlier servers at the highest possible row for their sequence, and avoids height-first reshuffling.

## Responsive Motion

For ordinary continuous browser resizing, do not animate width on every frame. That would feel elastic and lag behind the pointer. When the column count changes, use a restrained FLIP-style transform animation so cards travel from their previous visual position to the new one while their final layout is already correct.

- Animate transform and opacity only.
- Duration: approximately 280ms.
- Curve: `cubic-bezier(0.22, 1, 0.36, 1)`.
- No animation for data refreshes that do not change layout.
- Disable layout motion under `prefers-reduced-motion: reduce`.

## Card Visual Refinement

The information architecture remains mount-first. This pass does not add new controls or summaries.

- Align card surface, border, radius, and shadow tokens with GPU Monitor Clean.
- Reduce the diffuse floating shadow in light mode.
- Keep the server title strongest, mount path second, percentage/free space numeric and aligned.
- Reserve red/orange for pressure states; normal structure stays neutral.
- Keep media labels quiet and compact.
- Use one language within a metric line; do not mix Korean labels with `free`.
- Keep hover lift to 1px and avoid glow.

## Theme Reveal Origin

The reveal origin is the exact center of the currently visible sun or moon SVG, not an assumed button coordinate.

Sequence:

1. Resolve the currently visible icon inside the activated theme button.
2. Read that icon's `getBoundingClientRect()`.
3. Compute `x = left + width / 2` and `y = top + height / 2` in viewport coordinates.
4. Fall back to the button center only if the icon cannot be measured.
5. Compute the radius to the farthest viewport corner plus a small safety margin.
6. Lock repeated activation until the transition finishes.

The icon CSS itself is centered with `left: 50%`, `top: 50%`, and `translate(-50%, -50%)`; it does not use the current asymmetric `inset + fixed width` combination.

## Flash-Free Theme Transition

The current circular View Transition is followed by normal component color transitions. The new snapshot can therefore capture intermediate colors, and the live page continues transitioning after the circular reveal, which creates the visible flash.

The corrected sequence is:

1. Add a scoped `theme-transitioning` class that disables ordinary color, border, shadow, and icon transitions without changing current visual values.
2. Set reveal coordinates and radius before starting the View Transition.
3. Start a theme-scoped View Transition.
4. Apply the destination `html.light` or `html.dark` class synchronously inside the update callback.
5. Capture only fully resolved destination-theme styles.
6. Keep the old snapshot static and opaque.
7. Reveal the new snapshot only through the circular clip path; do not crossfade it.
8. Wait for the transition to finish.
9. Remove the transition-lock class after the live DOM is already in its final state, preventing a second tween.
10. Refresh theme-dependent charts from the `html.light/html.dark` source of truth.

View Transition pseudo-element rules are active only while the theme-transition class is present. Browsers without the View Transition API switch themes immediately without a broken overlay. Reduced-motion users receive an immediate switch with no circle, icon rotation, or layout movement.

## Theme Persistence and Semantics

- Preserve the existing per-browser `themeMode` cookie behavior.
- Guard `matchMedia` in the early bootstrap script.
- Synchronize `aria-pressed` before the first interactive frame.
- Keep focus on the theme button after keyboard activation without scrolling.
- Theme-dependent chart colors read the selected root theme, not only the OS preference.

## Scope Boundaries

- The 1440px constraint applies to the overview and its aligned header inner shell.
- Detail treemap and table screens keep their current wide working area.
- No server data, collection logic, scan scheduling, API contract, or GPU Monitor production code changes.
- No new dependency.

## Acceptance Criteria

### Layout

- At 1920px and 1600px, the overview is centered, no wider than 1440px, with matching header alignment.
- At 1280px, 1100px, 980px, 760px, and mobile width, every card receives the same available column width.
- All cards are recalculated on resize; movement is not concentrated only in cards after the first row.
- Server DOM order and configured order are unchanged.
- Expanding a card affects only later cards in its assigned column.
- No stale inline column placement, implicit columns, horizontal scroll, or card clipping occurs.
- Detail views remain usable and are not unintentionally constrained to 1440px.

### Theme

- The reveal begins at the visible sun/moon SVG center to within one CSS pixel.
- The destination circle covers the farthest viewport corner.
- There is no white/black flash, second global color tween, or icon pop after the circle finishes.
- Rapid repeated clicks cannot overlap transitions.
- The selected theme, controls, charts, cookie, `aria-pressed`, and root classes agree after the transition.
- Firefox/Safari or any browser without View Transition support changes theme cleanly through the fallback.
- Reduced-motion mode has no circular reveal, icon rotation, layout animation, focus jump, or forced smooth scrolling.

### Verification

- Existing viewer regression tests pass.
- Add regression coverage for stable ordered column assignment and complete relayout after column-count changes.
- Add unit coverage for icon-center coordinate selection and farthest-corner radius.
- Use Playwright screenshots at the acceptance widths in both light and dark modes.
- Use Playwright video or sequential screenshots to inspect the start, midpoint, and end of the theme reveal for flashing.


## Implementation and Verification Record

Implemented on `feature/multiserver-storage-dashboard` in commits `7cb25d9` through `23d1505`.

Automated verification completed on 2026-07-22:

- `node --check viewer/app.js viewer/overview.js viewer/treemap.js viewer/viewer.test.js viewer/viewer_regression_test.js`
- `node viewer/viewer_regression_test.js`
- `node viewer/viewer.test.js`
- `python3 -m pytest -q` — 222 passed
- `git diff --check`

Playwright verification covered 1920×1080, 1600×900, 1280×900, 1100×900, 980×900, 760×900, and 390×844 without horizontal overflow. The overview caps at 1440px, header/card content edges align, all cards share the active column width, and deployed server order uses deterministic columns `1,2,3,1,2,3,1`. Detail mode retained a 1920px main workspace with a 1872px treemap at a 1920px viewport.

Chromium native View Transition evidence confirmed the reveal variables exactly matched the visible icon center, the circular transition had no secondary color flash, rapid activation produced one transition, reduced motion switched immediately, and transition state/CSS variables were cleaned afterward. Firefox fallback and the 390px media-label layout were also verified; `Mixed` and `Unknown` rendered without clipping or overlap.

The Storage viewer static files were deployed to `127.0.0.1:8088`. Storage, GPU live (`15173`), and GPU development (`15174`) all returned HTTP 200 after deployment.
