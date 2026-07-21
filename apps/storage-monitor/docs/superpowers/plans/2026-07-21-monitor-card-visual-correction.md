# Storage Monitor Card Visual Correction

## Goal

Replace the rejected flat overview rows with a storage-specific version of GPU Monitor dev's card grid, while preserving data semantics, server order, mount order, routes, collection behavior, and browser-persisted theme mode.

## Reference evidence

- GPU Monitor dev: `http://127.0.0.1:15174/`, 1280x720
- Storage baseline: `http://127.0.0.1:8088/`, 1280x720
- GPU source: `/home/ircv/workspace/monitoring_v2_dev/frontend/src/lib/styles/monitor-cards.css`
- Exact reference geometry: 24px page inset, 14.4px three-column gutter, ~401px cards, 24px radius, 57px header.

## Execution

1. Replace the old connected-strip regression contract with monitor-card DOM and responsive CSS contracts.
2. Rebuild overview markup as semantic server articles containing a compact header, mount metric list, and footer.
3. Rebuild overview CSS from the existing shared Clean tokens using GPU Monitor's spacing, radius, surface, and transition values.
4. Add resize-safe masonry row-span measurement so variable-height cards pack without changing input order.
5. Align the suite header title and controls with GPU Monitor dev without adding unrelated filters or controls.
6. Run JS/Python tests, then compare dark/light desktop and mobile screenshots with Playwright.
7. Iterate on graph width, truncation, density, and card packing until the Storage surface is recognizably the same product family.
8. Deploy only the Storage viewer, verify both Storage and GPU services remain reachable, then commit.

## Acceptance criteria

- Storage no longer uses full-width pale table rows.
- At 1280px, cards align to a three-column GPU Monitor-like grid.
- Every actionable mount remains visible and ordered.
- Capacity bars are consistently visible and consume the flexible horizontal space.
- No page-level total-storage block or redundant server-count lead returns.
- Both themes, 390px mobile, routes, and cross-tool navigation work without horizontal overflow.
- Viewer and service tests pass; both local tunnels return HTTP 200.
