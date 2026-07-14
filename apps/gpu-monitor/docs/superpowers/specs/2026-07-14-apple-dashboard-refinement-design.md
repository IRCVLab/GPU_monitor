# Dashboard Refinement Design

## Goal

Make the monitoring dashboard feel like a restrained Apple internal tool: fewer repeated labels, stronger information hierarchy, dense use of space, and motion that communicates state without becoming decorative. Preserve all monitoring, filtering, editing, notes, drag ordering, and theme behavior.

## Layout

Use a measured CSS-grid masonry layout. Each card keeps DOM order and drag behavior, while a `ResizeObserver` assigns a grid-row span from its rendered height. This removes large holes created by servers with different GPU counts without introducing a dependency.

## Server cards

- Keep server name and health as the primary header information.
- Show the network chip only in the `전체` view because it is redundant in filtered views.
- Keep host and snapshot time as quieter secondary metadata.
- Reveal edit affordance on card hover/focus rather than displaying it at full prominence.
- Reduce nested surface fills and divider contrast so each card reads as one object.
- Keep abnormal states semantically prominent; do not hide server health.

## GPU rows

- Replace large circular GPU labels with compact rounded index labels.
- Align utilization and memory figures into stable metric columns.
- Keep username prominent and numeric labels tabular.
- Use semantic green for utilization and the selected color-theme accent for memory.

## Motion

- Card entry: short fade/translate transition.
- Card hover: one-pixel lift with a small shadow change.
- Edit affordance: opacity transition.
- Disclosure panels: short fade/translate animation on open.
- Metric bars: smooth width interpolation.
- Disable nonessential motion under `prefers-reduced-motion`.

## Validation

Run Svelte diagnostics and production build, then inspect dark/light screenshots at desktop and mobile widths. Verify card ordering, drag attributes, filter behavior, panel expansion, and compact status indicator remain intact.
