# Clean Density and Cross-Tool Navigation Design

## Status

- Superseded in part by the monitor-card correction below: 2026-07-21
- Primary surfaces: Storage Viz overview/detail, GPU Monitor header navigation
- Reference implementation: GPU Monitor `Clean` material in `/home/ircv/workspace/monitoring_v2_dev/frontend`

## Product intent

Storage Viz is a separate tool from GPU Monitor, but both belong to the same internal infrastructure suite. Storage Viz must use GPU Monitor's Clean visual language and provide direct navigation in both directions without coupling either service's data model or deployment lifecycle.

The overview remains fully expanded: every actionable mount is visible without another click. Density must come from stronger grouping and removal of redundant surfaces, not from hiding data.

## Monitor-card correction (approved by direct implementation feedback)

The earlier continuous full-width row treatment copied Clean color tokens but failed to copy GPU Monitor's component language. It made Storage Viz read as a separate light administrative table and reduced graph legibility. The following rules supersede the earlier `Server storage row` and `Continuous mount strip` sections.

1. At 1280px the overview uses the GPU Monitor dev geometry: 24px page insets, three approximately 401px columns, and a 14px gutter.
2. Each server is a variable-height monitor card with the same 24px radius, Clean surface, subtle border, shadow, header rule, compact metric body, and quiet footer anatomy as GPU Monitor.
3. Server order and mount order remain unchanged. Masonry packing may remove vertical gaps but must not sort by capacity, pressure, or name.
4. Each mount is a compact vertical metric row: media identity chip, path and numeric labels, then one long 4px capacity graph. The graph receives the flexible width instead of being squeezed between table cells.
5. The accent blue is the normal capacity graph color. Orange and red are restricted to warning/critical percentages and fills. Large server-level warning pills are removed.
6. The footer summarizes per-server storage utilization, free capacity, mount count, and exceptional pressure counts in one low-contrast line.
7. The shell title becomes `Storage Monitor`; header height, title scale, control radius, border, and backdrop treatment follow GPU Monitor dev. Cross-tool navigation stays visible.
8. Light and dark modes use the exact existing Clean tokens and the same component geometry. Theme persistence and service isolation remain unchanged.

## Goals

1. Make Storage Viz visually recognizable as the storage surface of GPU Monitor.
2. Fit the ordered seven-server overview into substantially less vertical space while keeping every actionable mount visible.
3. Make server, mount, pressure, and free-space hierarchy understandable in one scan.
4. Add same-tab navigation between GPU Monitor and Storage Viz.
5. Preserve server order, mount order, routes, scanner behavior, and service isolation.

## Non-goals

- Do not merge Storage Viz into the GPU Monitor application.
- Do not change the collection schema, scan cadence, mount policy, or GPU Monitor data flow.
- Do not add another material picker to Storage Viz; Storage Viz uses Clean only.
- Do not hide mounts behind accordions in the overview.
- Do not add a component framework or animation dependency.

## Source design language

Storage Viz copies the semantic values and behavior of GPU Monitor's Clean material rather than importing runtime code from the GPU Monitor repository.

### Core light tokens

- background `#f4f5f7`
- foreground `#0c121a`
- card `#ffffff`
- muted `#eceff1`
- muted foreground `#565e69`
- border `#dbdee2`
- primary `#297cef`
- destructive `#ee343b`
- success `#00a381`
- warning `#f3680f`

### Core dark tokens

- background `#090b0f`
- foreground `#f0f2f4`
- card `#13161b`
- muted `#181b1f`
- muted foreground `#8f9aa4`
- border `#26292e`
- primary `#3a8cff`
- destructive `#ff515a`
- success `#00b793`
- warning `#ff7527`

### Clean material

- surface mix: 94%
- blur: 6px
- saturation: 105%
- card mix: 96%
- control mix: 94%
- major radius: 0.8rem
- control radius: 0.7rem
- shadow: subtle outer depth plus a one-pixel inner highlight

## Information architecture

### Shared tool navigation

Both tools expose a visible utility link in the primary header:

- Storage Viz: `GPU Monitor`
- GPU Monitor: `Storage`

Navigation opens in the same tab. The default local targets are `http://127.0.0.1:15173/` and `http://127.0.0.1:8088/`, matching the SSH-forwarded operator workflow. The GPU Monitor link uses dedicated local tunnel port 15173 so it does not collide with unrelated local services already bound to 127.0.0.1:5173. Storage Viz keeps its current loopback-only service binding.

### Storage overview

The page contains only:

1. a compact suite header;
2. one ordered server row per configured server;
3. one continuous mount strip inside each server row.

Remove the overview subtitle, server-count lead, repeated healthy labels, and individual mount-card depth.

## Component design

### Compact suite header

- Height target: 48-52px.
- Left: compact Storage identity and a quiet `Storage` context label.
- Right: `GPU Monitor` link and circular light/dark control.
- Use the Clean surface, border, blur, and shadow tokens.
- Theme mode reads and writes the same `themeMode` cookie used by GPU Monitor so both tools follow the same browser preference when served on the same hostname.

### Server storage row

- Desktop grid: fixed 132-148px server column plus flexible mount strip.
- Row padding target: 6-8px vertical.
- Server column contains server name, mount count, and only the highest actionable warning.
- Do not show a healthy badge.
- Preserve configured server order.

### Continuous mount strip

- All actionable mounts remain visible.
- Mounts use a shared row surface with separators, not nested cards.
- Each mount cell contains path, media type, percent, pressure bar, and free space.
- Path and percent are primary; media and free space are secondary.
- Healthy values remain mostly monochrome. Warning and critical colors appear only in the bar, percent, and exceptional copy.
- Preserve snapshot mount order.

### Detail shell

- Combine back navigation, identity, scan metadata, cross-tool link, and rescan into a compact header region.
- Capacity rows use the same continuous-strip language as the overview.
- Treemap, Users, Top files, and Stale use the GPU Monitor segmented-control treatment.
- Existing detail routes and table/treemap behavior remain unchanged.

## Responsive behavior

- At desktop widths, mount cells fill two or three columns according to available width.
- At tablet widths, the server column remains compact and mount cells use two columns.
- At mobile widths, the server identity becomes a short first row and mounts use a dense two-column grid where labels fit; otherwise one column.
- No horizontal scrolling.

## Motion

- Use 160-220ms Clean transitions for hover, focus, theme color, and surface changes.
- Do not animate layout during periodic data refresh.
- Respect `prefers-reduced-motion`.

## Accessibility

- Preserve button semantics for server navigation and tabs.
- Cross-tool links have explicit accessible names.
- Theme control exposes pressed/current state and a visible focus ring.
- Warning meaning is available in text and not encoded only by color.
- Maintain current keyboard and history navigation behavior.

## Service and deployment boundaries

- Storage Viz remains `/opt/storage-viz-dashboard`, `storage-viz-dashboard.service`, port 8088 loopback.
- GPU Monitor remains `/home/ircv/workspace/monitoring_v2*`; only header navigation is changed.
- Storage deployment must restart only `storage-viz-dashboard.service`.
- GPU Monitor deployment must not modify backend services or Storage Viz.
- Existing GPU Monitor health PIDs and endpoint hashes are checked before and after Storage deployment.

## Acceptance criteria

1. All seven servers and all actionable mounts remain visible and ordered on the overview.
2. The overview has no nested mount-card appearance or redundant healthy copy.
3. Storage Viz light/dark colors, radii, borders, and surfaces match GPU Monitor Clean tokens.
4. `GPU Monitor` and `Storage` links navigate in both directions in the SSH-forwarded local workflow.
5. Theme mode persists and is shared through `themeMode` without a flash of the wrong mode.
6. Desktop and mobile have no horizontal overflow.
7. Existing scanner, collector, viewer, and GPU Monitor tests remain green.
8. Playwright visual QA shows a denser, coherent overview and no console errors.
