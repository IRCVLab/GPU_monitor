# GPU Monitor Semantic Density and Material Themes Design

## Status

- Approved for implementation from the user's 2026-07-15 direction.
- Supersedes conflicting portions of the Quiet Rack design concerning collapsed System/Memo presentation, Compact hover behavior, color-only themes, immediate theme switching, and the lack of I/O telemetry.
- Scope is development only. The live repository and live service remain untouched.
- Implementation audit as of commits `bf28961`, `0c87ccc`, `2823fcb`, and `c245313`: Tasks 2-4 are largely implemented. Continue judging the work by perceptual legibility in the running interface, not by the mere presence of matching code paths.

## Product judgment

This dashboard is a decision surface for researchers, not an operations wallboard. Every visible element must answer at least one of these questions:

1. Which server or exact GPU can I use now?
2. Who is already using an occupied GPU?
3. Is the telemetry fresh and trustworthy?
4. Is a GPU being informally held, by whom, and why?
5. Is a server constrained by compute, memory, storage, or I/O pressure?

Elements that answer none of these questions are removed. Decorative dots, repeated labels, hover-only dead ends, and ambiguous timestamps have no role.

## Information hierarchy

### Server card header

- Healthy cards use one compact baseline: server name, health dot, host/IP, freshness, edit action.
- The network port is shown only when it differs from the product default or is operationally necessary.
- Exception/degraded reason may add one secondary line; healthy metadata must not reserve that height.
- The card header is a navigation and identity surface, not a summary panel.

### System collapsed state

- Remove the circle before `System`.
- Use one line, not a two-row label/value grid.
- Default preview: `CPU 32% · RAM 10% · I/O 0.7% · Disk 62%` using tabular numerals.
- Labels are subdued; values carry contrast. Warning values receive semantic emphasis only when thresholds are crossed.
- I/O is Linux PSI I/O pressure (`some avg10`) because it represents task stall time and is more decision-useful than unreliable `/proc/stat` iowait.
- Expanded detail may show `some`, `full`, blocked tasks, CPU, RAM, disk capacity, GPU power, and explanatory microcopy.

### Memo and hold collapsed state

- Remove the circle before `Memo`.
- A memo is ordered as owner → content → expiry. It must be obvious which text is authored content.
- A hold is ordered as `HOLD G4·G5` → owner → reason/content → expiry.
- Use explicit Korean relative time such as `9분 남음`, `2시간 남음`, or `만료됨`; never cryptic `D/H/M/S` output.
- Long text is one-line clamped in the collapsed state and fully available when opened.

### System and Memo disclosure motion

- System and Memo expansion/collapse must be visually symmetric.
- The current conditional unmount creates a smooth-enough open but an abrupt close; replace it with a mounted disclosure panel.
- Animate a mounted grid-track disclosure wrapper, for example `grid-template-rows: 0fr -> 1fr`, together with opacity and a slight `translateY`. This is the narrow approved exception to the generic no-layout-animation rule because it is intrinsic-height disclosure motion, scoped to System and Memo panels.
- Closing must retain the panel content until the transition ends, then only disable interaction/visibility as needed.
- Reduced motion should make the state change immediate.
- Do not animate `top`, arbitrary height, or hard-coded `max-height`; those approaches either move unrelated layout or fail with variable memo/system content.

### Hold visual meaning

- A hold is advisory metadata, never a replacement for telemetry state.
- In Full, held GPU rows receive a narrow warm collar/notch around the exact `G#` marker plus a compact owner label.
- In Compact, the same collar/notch language appears on the exact GPU cell.
- Occupied/available color semantics remain telemetry truth. Hold styling overlays rather than recolors the base state.

## Compact behavior

- One server remains one row.
- Row activation always opens Full for that server.
- GPU hover/focus provides a small, non-interactive identity hint only: exact GPU, telemetry state, owner(s), and hold annotation when present.
- Remove `Full에서 보기` from the hover panel. It is an unreachable nested action because moving the pointer leaves the GPU cell.
- The hint ignores pointer events and never owns navigation.
- Server order always follows the persisted manual order; no view sorts.

## Header and indicator motion

- The expanded header freshness orb and the collapsed fixed indicator are the same conceptual object.
- On collapse, measure both endpoints and animate a FLIP-style transform from the header orb to the fixed indicator while the rest of the header translates/fades away.
- On reveal, reverse the visual relationship.
- The fixed indicator remains inside the viewport and never covers the content column.
- Its detail panel remains mounted and transitions opacity, translateY, and scale over 180–220ms. Do not toggle `display`.
- The indicator breathes slowly; a separate satellite orbit represents the 10-second request cadence. Data arrival is independent and does not reset or jump the orbit.
- Transient `refreshing` text never changes layout. Only sustained latency/degraded state adds a stable label.

## Theme architecture

The theme picker chooses a complete visual material preset, not an accent color. Light/dark remains a separate sun/moon control.

### Liquid Glass (default)

- Keep the existing cool semantic palette as the base.
- Liquid glass applies only to functional layers: adaptive header, menus, popovers, indicator panel, and theme control.
- Content cards remain calm, mostly opaque surfaces. Glass everywhere destroys hierarchy.
- Functional glass uses semantic color mixing, 24px blur, approximately 145% saturation, a restrained highlight edge, and layered tinted shadow.
- Radius remains approximately 24px for major surfaces, with smaller nested radii.

### Claude+

Use the exact extracted reference tokens from `cmdght103000n04lh3e2ae93r`.

Light: background `#faf9f5`, foreground `#3d3929`, card `#f5f4ef`, card foreground `#141413`, popover `#ffffff`, popover foreground `#28261b`, primary `#c96442`, primary foreground `#ffffff`, secondary `#e9e6dc`, secondary foreground `#535146`, muted `#ede9de`, muted foreground `#6e6d68`, accent `#e9e6dc`, accent foreground `#28261b`, destructive `#141413`, destructive foreground `#ffffff`, border `#dad9d4`, input `#b4b2a7`, ring `#c96442`, charts `#b05730 #9c87f5 #ded8c4 #dbd3f0 #b4552d`, radius `1rem`.

Dark: background `#262624`, foreground `#f1f1ef`, card `#2c2c2b`, card foreground `#faf9f5`, popover `#30302e`, popover foreground `#e5e5e2`, primary `#d97757`, primary foreground `#141413`, secondary `#faf9f5`, secondary foreground `#30302e`, muted `#1b1b19`, muted foreground `#b7b5a9`, accent `#1a1915`, accent foreground `#f5f4ee`, destructive `#ef4444`, destructive foreground `#ffffff`, border `#3e3e38`, input `#52514a`, ring `#d97757`, charts `#b05730 #9c87f5 #1a1915 #2f2b48 #b4552d`, radius `1rem`.

### AstroVista

Use the exact extracted reference tokens from `cmlk6zefr000004lbe9jygsqc`.

Light: background `#e8ebed`, foreground `#333333`, card `#ffffff`, card foreground `#333333`, popover `#ffffff`, popover foreground `#333333`, primary `#df6035`, primary foreground `#ffffff`, secondary `#2f4b79`, secondary foreground `#ffffff`, muted `#f9fafb`, muted foreground `#6b7280`, accent `#d6e4f0`, accent foreground `#1e3a8a`, destructive `#ef4444`, destructive foreground `#ffffff`, border `#cccccc`, input `#f4f5f7`, ring `#e05d38`, charts `#7399bf #e16f41 #d54450 #e2b146 #3c4c76`, radius `.5rem`.

Dark: background `#1a1a1a`, foreground `#e5e5e5`, card `#202020`, card foreground `#e5e5e5`, popover `#202020`, popover foreground `#e5e5e5`, primary `#df6035`, primary foreground `#ffffff`, secondary `#284167`, secondary foreground `#e5e5e5`, muted `#2a2a2a`, muted foreground `#808080`, accent `#2a3656`, accent foreground `#bfdbfe`, destructive `#ef4444`, destructive foreground `#ffffff`, border `#353535`, input `#303030`, ring `#e05d38`, charts `#85a6c7 #e16f41 #d54450 #e2b146 #3c4c76`, radius `.5rem`.

## Light/dark transition

- The sun/moon button is the origin of a circular reveal.
- Measure its viewport center and the radius to the farthest corner.
- Cover the current UI with the destination background using `clip-path` before applying the actual document mode.
- Expand for approximately 480ms, apply the mode after coverage, fade/remove the overlay, and lock repeated activation until complete.
- Reduced-motion users receive an immediate mode update or a very short crossfade.
- The toggle remains above the overlay and focus is preserved.

## Accessibility and motion constraints

- Never communicate status with color alone.
- All controls retain visible focus.
- Hover hints have focus equivalents.
- Respect `prefers-reduced-motion`.
- Animate transform, opacity, clip-path, and CSS variables; avoid layout-triggering height/top animation except the explicitly scoped mounted grid-track disclosure for System and Memo panels.
- No animation may make update timing appear more precise than the telemetry actually is.

## Acceptance criteria

- No decorative marker circle appears before collapsed System or Memo.
- Healthy System preview is one row and includes I/O pressure when supported.
- Memo/hold owner, content, GPUs, and expiry are distinguishable without opening.
- System and Memo disclosure controls own `aria-expanded` and `aria-controls`; their panels remain mounted and open/close with the same mounted-content grid-track transition. Closing is not an abrupt unmount, and reduced motion is immediate.
- Exact held GPUs are visible in both Full and Compact.
- Compact hover contains no nested navigation action; row activation opens Full.
- Header orb visibly hands off to the collapsed indicator; indicator panel animates without `display` toggling.
- Sun/moon activation produces a button-centered circular reveal unless reduced motion is enabled.
- Theme menu offers Liquid Glass, Claude+, and AstroVista as complete style presets.
- Server order remains unchanged.
- No live repository or live service modification occurs.

