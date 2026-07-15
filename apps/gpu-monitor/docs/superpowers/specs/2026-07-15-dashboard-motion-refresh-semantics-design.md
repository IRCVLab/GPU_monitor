# Dashboard Motion and Refresh Semantics Design

> **Current authority:** The refresh cadence, collapsed-indicator placement, and Compact occupied-surface decisions in this document are superseded by `2026-07-15-continuous-refresh-satellite-design.md`. The FLIP layout decisions remain applicable.

## Goal

Make layout changes, scroll-state feedback, refresh timing, and GPU color meaning read as one coherent monitoring system rather than unrelated effects.

## Decisions

### 1. One GPU color language

- **Available:** a dark surface with the single occupancy accent used for its label and border.
- **Occupied:** Full may use an inverse accent treatment, while Compact uses a restrained accent tint so repeated slots do not become a solid color field.
- **Unknown/stale:** neutral muted treatment. It must never look available or occupied.
- Full and Compact consume the same `getCompactGpuState(...)` result and expose the same `data-state` values without requiring identical surface fills.
- Utilization and memory graphs keep their independent chart colors; they describe measurements, not occupancy.

### 2. Refresh cadence is a circular state indicator

- Remove the ambiguous horizontal marquee from the expanded header.
- Use one shared visual in both expanded and collapsed states: a breathing center status dot, a faint track, a fixed top marker, and a small orbiting satellite.
- The satellite travels linearly and continuously once every ten seconds.
- Network response timing never parks, restarts, or delays the visual cycle.
- The periodic request cadence is scheduled independently, beginning one second before the first visual boundary and continuing every ten seconds.
- Normal uses green; delayed/disconnected uses amber. Exact relative times remain available to assistive text and the detail popover, not as constantly changing header copy.
- The animation is CSS-driven so ordinary server-state updates do not restart it.

### 3. Collapsed scroll indicator lives on the left

- Scrolling down past the existing direction threshold collapses the header and reveals the ring indicator.
- The indicator is fixed near the upper-left page gutter, not the right edge.
- Its visual footprint is only the dot and ring; the larger invisible hit target remains for accessibility.
- Hover, focus, or click opens the existing status/network detail panel to the right of the indicator.
- The hidden indicator remains mounted and visually hidden with opacity/visibility rather than `display: none`, so its ring stays synchronized with the expanded header.

### 4. Gapless layout changes use FLIP motion

- The Masonry action stores each card’s last document-space rectangle.
- Before applying a new placement it cancels stale layout animations; after placement it compares old and new rectangles.
- Cards with changed positions animate from the inverse offset to their new position using the Web Animations API.
- This applies to Grid ↔ Gapless switching and later Masonry reflows caused by card size changes.
- Motion lasts about 360ms with a restrained native-feeling easing curve.
- `prefers-reduced-motion: reduce` disables the movement without changing layout behavior.

## Refresh Data Flow

1. The satellite begins an independent ten-second CSS orbit.
2. The first periodic refresh request is issued after nine seconds.
3. The next ten-second request tick is scheduled before the current request starts.
4. Response data is merged whenever it arrives without recreating or restarting the ring.
5. If a refresh is still active at a cadence tick, that duplicate request is skipped while the next fixed tick remains scheduled.
6. Svelte 5 effect cleanup removes timers and listeners when the page is left.

## Accessibility and Motion

- The ring is decorative; the surrounding button and status text carry the accessible label.
- Status is never communicated by color alone: `정상`, `지연`, `오프라인`, or `확인중` remains available in text.
- Reduced-motion users receive a static ring state and immediate layout placement.
- Focus targets remain at least 24px for the compact indicator and retain visible focus styling.

## Verification

- Contract tests prove there is no old horizontal cadence bar, both header states use the shared ring, and the floating indicator is left-aligned.
- Scheduling tests prove request timing is fixed and independent of refresh completion.
- GPU component tests prove Full and Compact consume the same availability state and semantic color selectors.
- Layout-motion tests prove document-space FLIP deltas and reduced-motion bypass.
- Browser QA verifies Grid ↔ Gapless movement, header collapse/indicator visibility, continuous satellite motion, lifecycle cleanup, no content overlap, and no horizontal overflow.
