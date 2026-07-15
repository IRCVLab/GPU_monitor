# Compact Inspector and Mobile Utility Density Design

**Date:** 2026-07-15  
**Branch:** `feature/compact-gpu-dashboard`  
**Scope:** Development repository only. The live repository and service on port 5173 remain untouched.

## Goal

Increase useful information density without changing server order or adding new monitoring concepts. Full mobile cards should stop spending two lines on collapsed System/Memo summaries, and Compact desktop details should never cover the availability overview or header controls.

## Playwright baseline

- At `390x740`, each collapsed Full utility button is approximately `38px` high because the `max-width: 640px` rule changes the toggle to `flex-direction: column`.
- At `1440x900`, the selected Compact detail is a fixed `480px` overlay. It overlaps the server list by approximately `57,661px²` and also occupies the header-control region.
- Both viewports currently have no horizontal overflow. That must remain true.

## Constraints retained from prior decisions

1. Server sequence is user-owned and must remain identical between Full and Compact.
2. Compact remains availability-only: no IP, freshness, network metadata, utilization chart, or free-count badge is added.
3. Every Compact server remains exactly one row through tablet and desktop widths.
4. Available GPU slots remain dark/outlined; occupied slots retain the restrained theme accent tint and user initials.
5. Mobile keeps a modal bottom sheet for touch access to full usernames.
6. No new dependency, remote asset, or live-service modification.

## Design alternatives

### A. Keep the fixed Compact overlay

Rejected. It preserves maximum list width but obscures exactly the overview users opened Compact to scan, and its stacking order competes with header controls.

### B. Expand the selected Compact server inside the list

Rejected. It makes one server consume multiple rows and breaks the one-server/one-row scan rhythm the user explicitly requested.

### C. Contextual desktop inspector rail

Selected. When a server is selected at desktop width, the list and a narrow inspector share the content row. The rail is part of layout flow, so overlap is structurally impossible. When nothing is selected, the list uses the full width. Tablet/mobile behavior remains the existing bottom sheet.

## Design decisions

### Full mobile System/Memo controls

- Keep the current marker, label, preview, and CSS disclosure angle.
- Remove the mobile column override; use one horizontal row at every width.
- Keep the label as a non-shrinking anchor.
- Allow the preview side to shrink to zero, ellipsize, and remain right-aligned.
- Target a collapsed control height around `25–28px`, reducing two controls by roughly one text line per card.
- Preserve full summary content through the existing `title`/accessible button name.

### Expanded Full system density

- Keep all CPU, RAM, GPU hardware, storage summary, and mount data.
- On mobile, each storage mount remains one line (`path · used/total · percent`) with path ellipsis instead of becoming a three-line mini-card.
- Preserve four GPU hardware cells per row at 390px by reducing only internal padding, gap, and secondary type size enough to prevent clipping.
- Do not increase card width or create horizontal page overflow.

### Compact desktop inspector

- Replace `.compact-detail-overlay` with an in-flow `.compact-dashboard__inspector`.
- At `min-width: 1200px`, selected state uses `minmax(0, 1fr) minmax(16rem, 18rem)` with a restrained gap.
- The server list remains the first grid column and keeps original order and row structure.
- The inspector uses the existing detail content, but the mode name changes from presentation-specific `overlay` to semantic `inspector`.
- The inspector enters with a short opacity/translate animation only; no glow, scale, or layout-blocking motion.
- The inspector is an in-flow grid item with `position: sticky`, a `4.25rem` top offset, and viewport-bounded internal overflow so details remain reachable for long server lists without covering the header or indicator.
- At widths below `1200px`, the existing bottom sheet remains unchanged.

## Accessibility

- Server row selection and Escape-to-close remain keyboard accessible.
- Focus returns to the triggering row after closing either detail surface.
- The desktop inspector is a labelled complementary detail region, not an `aria-modal` dialog.
- The mobile sheet remains an `aria-modal` dialog.
- Truncated Full summaries keep their full accessible text.
- Reduced-motion users receive no inspector entrance animation.

## Acceptance criteria

1. At `390px` and `360px`, collapsed Full System/Memo controls are one horizontal line, previews truncate, and the page has no horizontal overflow.
2. At `390px`, expanded System mount telemetry stays one line, four GPU hardware cells do not clip, and no system-panel child creates horizontal page overflow.
3. At `1440x900` and `1200x800`, opening Compact detail produces zero geometric overlap between the list and inspector.
4. In a long Compact list, the inspector remains sticky inside the viewport below the header and uses internal scrolling only when its own content exceeds the viewport bound.
5. At `1024px` and below, selection still opens the existing bottom sheet.
6. Compact server order and one-row-per-server layout are unchanged.
7. GPU availability/occupied color semantics are unchanged.
8. Existing frontend tests, `npm run check`, `npm run build`, and backend tests pass.
