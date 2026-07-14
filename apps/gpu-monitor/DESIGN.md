# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-14
- Primary product surfaces: fleet dashboard, server details, notes, event logs, development diagnostics, server administration
- Evidence reviewed:
  - frontend/src/routes/+page.svelte
  - frontend/src/lib/components/ServerCard.svelte
  - frontend/src/lib/components/GpuBar.svelte
  - frontend/src/lib/components/StatusBadge.svelte
  - frontend/src/routes/logs/+page.svelte
  - frontend/src/routes/debug/+page.svelte
  - frontend/src/lib/stores/servers.ts
  - frontend/src/lib/ws.ts
  - backend/collectors/server_collector.py
  - baseline screenshots captured at 1440x1000 in dark and light mode
  - TweakCN reference: https://tweakcn.com/themes/cmr2flrsp000304ih46yj4y1b
  - TweakCN registry source: https://tweakcn.com/r/themes/cmr2flrsp000304ih46yj4y1b
- Supersedes: docs/superpowers/specs/2026-07-14-apple-dashboard-refinement-design.md and its narrow implementation plan

## Brand
- Personality: calm, precise, trustworthy, technical, native-feeling, quietly premium
- Trust signals: health is explicit, data freshness is explicit, semantic colors are consistent, numbers align, destructive actions are separated
- Avoid: decorative gradients, neon glow, purple-first branding, oversized headers, repeated labels, hidden critical state, arbitrary CSS zoom, dense rows that truncate users, heavy nested panels

## Product goals
- Goals:
  - Answer "Is the fleet healthy?" within one second.
  - Answer "Where is an available GPU?" without scanning every row.
  - Show every user sharing a GPU without misleading truncation.
  - Make stale data, transport failure, and collector failure distinguishable.
  - Keep system, storage, notes, logs, and administration reachable without competing with GPU occupancy.
  - Preserve internal/external/all scope and manual ordering.
- Non-goals:
  - Replacing the backend collector or authentication model.
  - Turning the dashboard into a general analytics product.
  - Adding decorative charts with no operational decision value.
  - Changing the live production deployment while the redesign is developed.
- Success signals:
  - Global health, free GPU count, active/shared occupancy, and refresh freshness are visible above the server grid.
  - Abnormal servers interrupt the visual rhythm without opening a card.
  - Multi-user GPUs wrap cleanly and expose every name.
  - Core actions remain usable on phone width without hover or drag.
  - Dark and light surfaces use the same token system.

## Personas and jobs
- Primary personas: researchers choosing compute, operators diagnosing fleet health, administrators managing server inventory
- User jobs:
  - Detect offline, degraded, delayed, or stale servers.
  - Find idle GPUs within internal or external network scope.
  - Identify single-user and shared-GPU occupancy.
  - Check utilization and integer GPU memory usage.
  - Inspect CPU, RAM, temperature, power, and storage pressure.
  - Read or leave time-bounded operational notes.
  - Reach server-filtered logs and development diagnostics.
  - Register, edit, reorder, or delete a server safely.
- Key contexts of use: frequent desktop glance monitoring, incident triage, phone-width checks, dark rooms, mixed Korean labels and technical metrics

## Information architecture
- Primary navigation:
  - Header left: product identity and current fleet/refresh health.
  - Header center: integrated Internal / External / All scope.
  - Header right: View, Manage, and far-right light/dark toggle.
  - Mobile: health first, scope second, one overflow control for View and Manage actions, theme toggle.
- Core routes/screens:
  - Dashboard is the operator home.
  - Event logs are a diagnostic drill-down and accept server context.
  - Development diagnostics remain clearly development-only.
  - Server administration opens from Manage or a contextual card action.
- Content hierarchy:
  1. Fleet health and data freshness.
  2. Scope and fleet overview.
  3. Server health and GPU availability.
  4. Per-GPU metrics and users.
  5. System/storage and notes.
  6. Administration.

## Design principles
- Health before chrome: server/fleet health survives header collapse and never depends on opening a menu.
- Availability before telemetry: free/occupied/shared state is more important than decorative charts.
- Users are first-class data: every GPU reserves a separate users row and supports wrapping chips.
- One surface, clear layers: a server card reads as one object; dividers and fills are quiet.
- Progressive disclosure: system, storage, notes, edit, and delete stay reachable but secondary.
- Stable order: visual order, DOM order, and drag/save order must not diverge.
- Theme as a system: the TweakCN reference applies to every route and component, not only the header.
- Motion explains change: transitions signal filtering, expansion, refresh, and state; they are never ornamental.
- Tradeoffs: preserve readable card width and user visibility before maximizing column count.

## Visual language
- Color:
  - Base theme is TweakCN "Apple Liquid Glass."
  - Light: background oklch(0.9700 0.0029 264.5420), card/popover white, foreground oklch(0.1801 0.0191 255.7673), border oklch(0.8994 0.0064 255.4779).
  - Dark: background oklch(0.1492 0.0093 263.9667), card oklch(0.1993 0.0111 260.6610), popover oklch(0.2298 0.0107 260.6838), foreground oklch(0.9602 0.0034 247.8587), border oklch(0.2800 0.0102 260.7048).
  - Primary blue: light oklch(0.6007 0.1903 257.9419), dark oklch(0.6496 0.1885 257.7207).
  - Utilization/healthy: semantic green. Delayed/degraded: amber. Offline/destructive: red. User identity and memory accent: selected palette primary.
  - Color palette choices may swap primary/chart accents, never base surface contrast or health semantics.
- Typography:
  - UI follows the reference system font stack: Segoe UI, Helvetica Neue, Helvetica, Lucida Grande, Arial, Ubuntu, Cantarell, Fira Sans, sans-serif.
  - Metrics use SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace.
  - Tabular numerals are mandatory for aligned telemetry.
  - Server names and health are strongest; host, timestamps, and labels are quieter.
- Spacing/layout rhythm:
  - 4px base unit.
  - Main shell max width supports three readable 360–400px cards at desktop.
  - Fleet overview is one compact strip, not a row of oversized marketing tiles.
  - Remove CSS zoom. View preferences change card density/minimum width, not browser-scale typography.
- Shape/radius/elevation:
  - Root/card radius 24px (1.5rem).
  - Controls 12–16px; chips/pills full radius only when semantically appropriate.
  - Light shadow: soft 28px at low opacity. Dark shadow: soft 40px at low opacity.
  - Use translucent/backdrop surfaces selectively for header, popovers, and overview; do not stack glass inside glass.
- Motion:
  - 140–220ms ease-out for controls and panels.
  - 280–360ms for initial card/filter entry with small stagger.
  - Card hover lift is at most 1px.
  - Status dot breathes slowly with low amplitude.
  - Compact mode keeps the approved dot-only resting state; hover/focus expands status and scope details.
- Imagery/iconography:
  - No decorative imagery or brand logos.
  - Use simple line icons with consistent stroke; text remains primary for critical state.

## Components
- Existing components to reuse:
  - ServerCard business logic and system/storage/notes content.
  - StatusBadge state semantics.
  - ServerForm, ServerDeleteModal, logs, and debug routes.
  - Theme, dashboard preference, tab, server, and order stores.
- New/changed components:
  - FleetOverviewStrip: server health, free/total GPU, active users, shared GPUs, refresh freshness.
  - DashboardToolbar: result count, optional server/user search, issue shortcut, density preference.
  - ServerCard shell: health edge/state, summary counts, network only in All scope, contextual actions.
  - GpuRow: metric row plus distinct wrapping users row; shared badge when users.length > 1; integer memory labels.
  - MobileOverflowMenu: View and Manage entry points without crowding the header.
  - CompactStatusOrb: existing dot-only default, accessible hover/focus expansion, no content overlap.
- Variants and states:
  - Server: online, degraded, offline, unknown/stale.
  - GPU: idle, occupied, shared, high memory, high temperature.
  - Refresh: live socket, polling fallback, delayed, failed with last-known data.
  - Notes: none, active, expiring soon, urgent.
- Token/component ownership:
  - Global surfaces, typography, radius, shadow, semantic color, and motion tokens live in app.css.
  - Components consume tokens; route-specific CSS must not redefine the theme.

## Accessibility
- Target standard: WCAG 2.2 AA where practical.
- Keyboard/focus behavior:
  - Scope, View, Manage, theme, search, card actions, disclosure panels, and notes are keyboard reachable.
  - Hover-only actions also appear on focus-within.
  - Compact status details expand on focus, not only pointer hover.
- Contrast/readability:
  - Muted text must remain readable in light and dark mode.
  - Health is communicated by text/icon plus color.
  - Telemetry uses minimum readable sizes and tabular alignment.
- Screen-reader semantics:
  - Fleet summary uses live regions only for meaningful state transitions.
  - GPU rows announce index, utilization, memory, and all users.
  - Shared occupancy is explicit.
- Reduced motion and sensory considerations:
  - Disable breathing, stagger, hover lift, width interpolation, and panel animation under prefers-reduced-motion.
  - Avoid flashing or fast pulses.

## Responsive behavior
- Supported breakpoints/devices:
  - Desktop >=1200px, tablet 768–1199px, mobile <768px.
- Layout adaptations:
  - Desktop: three readable columns when width permits, otherwise two.
  - Tablet: two columns and two-row header.
  - Mobile: one column, health first, scope scrollable, actions in one overflow menu.
  - GPU metrics remain row one and users remain row two at every width.
  - Multiple users wrap; they are never reduced to one apparent owner.
- Touch/hover differences:
  - Drag reorder is desktop-only unless a touch-safe reorder control is added.
  - Edit and management remain explicitly reachable on touch.
  - Compact status expansion supports tap/focus.

## Interaction states
- Loading: quiet skeletons preserving final card geometry; no full-page spinner after first data.
- Empty: distinguish no registered servers, no servers in scope, and no search matches.
- Error: retain last-known data, label it stale, show retry, and distinguish socket failure from API failure.
- Success: refresh timestamp updates without celebratory motion.
- Disabled: preserve readable reason and do not rely on opacity alone.
- Offline/slow network:
  - Fleet overview and header identify polling fallback, delayed refresh, or failed refresh.
  - Server collector status remains distinct from dashboard transport state.
  - Compact orb changes semantic color and accessible label.

## Content voice
- Tone: terse, operational, calm.
- Terminology:
  - "정상", "지연", "오프라인", "확인 중", "사용 가능", "사용 중", "공유", "마지막 업데이트".
  - Use "내부망", "외부망", "전체" consistently.
  - GPU memory is formatted as integer "21/24 GB".
- Microcopy rules:
  - Prefer direct status plus timestamp.
  - Avoid vague "문제 있음" when a reason exists.
  - Destructive actions state the affected server.
  - Development diagnostics are labeled development-only.

## Implementation constraints
- Framework/styling system: SvelteKit 5, Tailwind, existing global app.css tokens.
- Design-token constraints:
  - Map exact Apple Liquid Glass base tokens first.
  - Remove legacy rose/pink surface rules and broad conflicting overrides during the redesign.
  - Keep selectable blue/violet/emerald accents independent from light/dark mode.
- Performance constraints:
  - No new frontend dependency for layout or animation.
  - Resize/layout work must be batched with requestAnimationFrame and observed only where needed.
  - WebSocket/polling behavior and live collector must remain unchanged.
- Compatibility constraints:
  - Production project ~/workspace/monitoring_v2 is not edited.
  - Development project and tmux services remain isolated.
  - Preserve server CRUD, notes, logs, debug, filters, and drag order behavior.
- Test/screenshot expectations:
  - Svelte diagnostics and production build.
  - Dark/light desktop at 1440x1000.
  - Mobile at 390x844.
  - Server/GPU abnormal states, multi-user wrapping, integer memory, scope filter, panel expansion, compact orb, and reduced motion.
  - Visual reference approval precedes implementation.

## Open questions
- [x] Manual order scope: preserve current behavior for this redesign; do not expand into backend authorization work.
- [x] Threshold policy: preserve existing collector/display thresholds unless a separate change is requested.
- [x] Notes semantics: remain free text with current expiry/auth behavior.
- [x] Debug exposure: remain under Manage and clearly development-only.
- [x] Compact status: retain the user-approved dot-only resting state with hover/focus expansion.
- [ ] Final visual reference approval by user before implementation.
