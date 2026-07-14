# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-14
- Primary product surfaces: default server-card GPU selection dashboard, server details, notes, event logs, development diagnostics, server administration
- Product purpose: help researchers choose which independent GPU server to enter for training. Servers are not high-speed-networked, so the decisive questions are which exact server has immediately available GPU capacity, what GPU model/VRAM it offers, and who is already using each GPU.
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
- Supersedes: docs/superpowers/specs/2026-07-14-apple-dashboard-refinement-design.md and its narrow implementation plan where they conflict with this product clarification

## Brand
- Personality: calm, precise, trustworthy, technical, native-feeling, quietly premium, research-lab pragmatic
- Trust signals: available capacity is prominent, server identity is unambiguous, data freshness is explicit, user names are complete, semantic colors are consistent, numbers align, destructive actions are separated
- Avoid: fleet-operations/incident-dashboard framing, decorative gradients, neon glow, purple-first branding, oversized KPI hero cards, shared metric/badge, repeated labels, hidden critical state, arbitrary CSS zoom, dense rows that truncate users, heavy nested panels, giant/nested GPU cards

## Product goals
- Goals:
  - Improve the existing original/default server-card dashboard first; do not replace it with a compact availability table in this scope.
  - Let researchers answer “Which server can I use right now?” without scanning every GPU row.
  - Preserve complete per-GPU ownership: every user name remains visible and wraps rather than truncating into misleading ownership.
  - Surface GPU model, integer VRAM capacity, visual per-GPU availability, utilization, memory use, scope, and freshness at the level where they aid server choice; do not use visible textual availability counts such as `3 available`.
  - Keep internal/external/all scope, manual ordering, server CRUD, notes, logs, debug, filters, and live refresh behavior intact.
- Non-goals:
  - Turning this into a fleet operations center, incident command dashboard, or general analytics product.
  - Replacing backend collector, authentication, WebSocket/polling, notes, logs, debug, or server administration behavior.
  - Introducing operations-first KPIs, decorative charts, summary badges that compete with server choice, or Compact availability-board implementation.
  - Changing the live production deployment or touching `~/workspace/monitoring_v2` while the redesign is developed in `~/workspace/monitoring_v2_dev`.
- Success signals:
  - Default view: researchers can identify usable servers from refined server cards, with no masonry blank-space gaps caused by unequal GPU counts.
  - Free servers visually rise above full servers without turning full servers into alarms.
  - Multi-user GPUs wrap cleanly and expose every name in the default card rows.
  - Memory capacities and usage labels are integer GB.
  - Dark and light surfaces use the same token system and restrained semantic color.

## Personas and jobs
- Primary personas: researchers choosing compute for training jobs; administrators maintaining server metadata and availability context
- User jobs:
  - Choose an independent server to enter based on immediate available GPU capacity.
  - Compare per-GPU availability cues, GPU model, VRAM, network scope, users, and freshness before starting work without relying on visible count text.
  - Identify single-user and shared-GPU occupancy without hidden names.
  - Check per-GPU utilization and memory when a server is a candidate.
  - Read notes that affect server choice, such as reservation, maintenance, or lab usage expectations.
  - Register, edit, reorder, or delete a server safely when administering the inventory.
  - Reach server-filtered logs and development diagnostics when needed, without those tools dominating the dashboard.
- Key contexts of use: frequent desktop lab use, dark rooms, phone-width checks, mixed Korean labels and technical metrics, server selection before launching or moving training work

## Information architecture
- Primary navigation:
  - Header left: product identity and concise live/freshness state.
  - Header center: integrated Internal / External / All scope.
  - Header right: search when present, Manage/settings, far-right light/dark toggle.
  - Do not add a Compact View-menu mode in this scope.
  - Mobile: freshness first, scope second, one overflow control for Manage actions, theme toggle.
- Core routes/screens:
  - Dashboard default is the refined server-card dashboard.
  - Compact availability-board research/reference is deferred future work only, not current acceptance scope.
  - Event logs are a diagnostic drill-down and accept server context.
  - Development diagnostics remain clearly development-only.
  - Server administration opens from Manage/settings or a contextual card action.
- Content hierarchy:
  1. Server choice and immediately available GPU capacity.
  2. Scope and server identity.
  3. GPU model, VRAM, and unboxed per-GPU availability dots.
  4. Per-GPU users, utilization, and memory.
  5. System/storage and notes.
  6. Administration and diagnostics.

## Design principles
- Server selection before operations: every layout answers where to train before it answers how to operate a fleet.
- Availability before telemetry: free/occupied/shared state is more important than decorative charts or operational aggregates.
- Default remains card-based: server cards are refined, not replaced, and continue to carry notes/system context.
- Compact availability-board work is deferred: keep references as future research only and do not implement or commit Compact now.
- Users are first-class data: every GPU reserves space for complete wrapping user names; truncation must not hide ownership.
- One surface, clear layers: cards read as single objects; dividers, borders, bars, and fills are quiet.
- Progressive disclosure: system, storage, notes, edit, delete, logs, and diagnostics stay reachable but secondary to selection.
- Stable order: visual order, DOM order, and drag/save order must not diverge in the default view.
- Theme as a system: the TweakCN reference applies to every route and component, not only the header.
- Motion explains change: transitions signal filtering, expansion, hover/focus, refresh, and state; they are never ornamental.
- Tradeoffs: preserve readable cards/rows, user visibility, and server identity before maximizing density.

## Default view: refined server-card dashboard
- Role: the default dashboard experience. It must remain the existing server-card dashboard refined for GPU selection, not an availability table replacement.
- Layout:
  - Use masonry or masonry-equivalent placement so cards with unequal GPU counts do not leave large blank vertical gaps.
  - Desktop keeps readable card widths before maximizing column count; tablet and mobile preserve full GPU row readability.
  - No CSS zoom. Density preferences adjust card width, spacing, and disclosure—not browser-scale typography.
- Server header:
  - Simplify to server name, small health dot/text, promoted GPU model/VRAM, and an unboxed per-GPU circular availability cue.
  - Show network only in All scope; omit it in Internal/External scopes because the scope already provides context.
  - Put IP address and refreshed time on one secondary line.
  - Edit/admin affordance appears on hover and focus-within, not as persistent header chrome; it must remain reachable on touch through an explicit menu/action.
- GPU row hierarchy:
  - Users are emphasized as the key occupancy signal and remain visible/wrapping.
  - Utilization and memory align in fixed-width numeric columns with tabular numerals.
  - Remove boxed `G#` labels; if GPU index remains visible, keep it inline and quieter than users/model/VRAM.
  - Remove boxy/grid-like interiors, excessive dividers, equal-strength dual bars, and boxed footer sections; bars, borders, and row fills are quieter than user names, model/VRAM, and availability dots.
  - One small circular dot represents each GPU near the promoted model/VRAM line: free uses the semantic availability/accent color, occupied/shared uses a quiet neutral; this replaces visible textual availability counts such as `3 available`.
  - Memory capacities and usage are integer GB.
- Footer/system/notes:
  - System telemetry, storage, notes, and related secondary details remain available but read as one quiet continuation of the card, not boxed footer sections.
  - Footer content supports wrapping notes and avoids stacked glass panels, nested boxes, or equal-weight dividers.
- Visual treatment:
  - Semantic restrained color: green for available/healthy, amber for delayed/degraded, red for offline/destructive, muted neutrals for full/occupied.
  - Larger outer radii, weak shadows, fewer pills, lower-contrast nested borders, and no glow/glass excess.
  - Subtle 1px hover/expand/bar motion only; reduce under prefers-reduced-motion.
- Default success criteria:
  - Researchers can choose a candidate server from cards without opening every card.
  - Unequal GPU counts do not create distracting layout holes.
  - Every user name is visible, model/VRAM is prominent, availability is perceived from per-GPU dots, and capacity labels are integer GB.
  - Header chrome is calmer while health, IP, network-in-All, and freshness remain discoverable.

## Deferred future work: availability board
- Compact availability-board research/reference is deferred future work only.
- Do not implement, wire, commit, or require Compact availability-board behavior in the current default-card pass.
- Do not use a Compact Visual Ralph verdict, Compact implementation files, or a compact reference artifact as current acceptance criteria.
- Future work may revisit an availability board after the original/default card dashboard meets this design contract.

## Visual language
- Color:
  - Base theme is TweakCN "Apple Liquid Glass."
  - Light: background oklch(0.9700 0.0029 264.5420), card/popover white, foreground oklch(0.1801 0.0191 255.7673), border oklch(0.8994 0.0064 255.4779).
  - Dark: background oklch(0.1492 0.0093 263.9667), card oklch(0.1993 0.0111 260.6610), popover oklch(0.2298 0.0107 260.6838), foreground oklch(0.9602 0.0034 247.8587), border oklch(0.2800 0.0102 260.7048).
  - Primary blue: light oklch(0.6007 0.1903 257.9419), dark oklch(0.6496 0.1885 257.7207).
  - Availability/healthy: semantic green. Delayed/degraded: amber. Offline/destructive: red. Occupied/full: muted neutral. User identity and memory accent: selected palette primary.
  - Color palette choices may swap primary/chart accents, never base surface contrast or health/availability semantics.
- Typography:
  - UI follows the reference system font stack: Segoe UI, Helvetica Neue, Helvetica, Lucida Grande, Arial, Ubuntu, Cantarell, Fira Sans, sans-serif.
  - Metrics use SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, Courier New, monospace.
  - Tabular numerals are mandatory for aligned telemetry.
  - Server names, GPU model, VRAM, and visual availability cues are strongest; host, timestamps, and labels are quieter.
- Spacing/layout rhythm:
  - 4px base unit.
  - Default main shell supports readable 360–400px cards and masonry flow.
  - Overview/header content is concise and decision-oriented, not marketing or operations KPI tiles.
- Shape/radius/elevation:
  - Default card outer radius 24px (1.5rem) with weak shadows.
  - Controls 12–16px; chips/pills full radius only when semantically appropriate.
  - Light shadow: soft 28px at low opacity. Dark shadow: soft 40px at low opacity.
  - Use translucent/backdrop surfaces selectively for header, popovers, and board/card shells; do not stack glass inside glass.
- Motion:
  - 140–220ms ease-out for controls, bars, rows, and panels.
  - 280–360ms for initial card/filter entry with small stagger when helpful.
  - Card/row hover lift is at most 1px.
  - Status dot breathes slowly with low amplitude only when it improves freshness perception.
  - Disable breathing, stagger, hover lift, width interpolation, and panel animation under prefers-reduced-motion.
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
  - DefaultDashboardMasonry: masonry/equivalent card layout that preserves manual order semantics and avoids blank gaps.
  - ServerCard shell: simplified header, health dot/text, promoted GPU model/VRAM, unboxed per-GPU availability dots instead of visible count text, network only in All scope, contextual actions on hover/focus.
  - GpuRow/GpuBar default rows: user-first rows, no boxed `G#` labels, wrapping users, integer memory labels, and quieter utilization/memory telemetry that does not create equal-strength dual bars.
  - QuietServerFooter: unified system/storage/notes region.
  - MobileOverflowMenu: Manage entry points without crowding the header.
  - Deferred future work only: Compact availability-board component work is not current implementation scope.
- Variants and states:
  - Server: online, degraded, offline, unknown/stale.
  - GPU: available, occupied, shared, high memory, high temperature.
  - View: Default cards only in this scope; Compact availability board is deferred.
  - Refresh: live socket, polling fallback, delayed, failed with last-known data.
  - Notes: none, active, expiring soon, urgent.
- Token/component ownership:
  - Global surfaces, typography, radius, shadow, semantic color, and motion tokens live in app.css.
  - Components consume tokens; route-specific CSS must not redefine the theme.

## Accessibility
- Target standard: WCAG 2.2 AA where practical.
- Keyboard/focus behavior:
  - Scope, Manage, theme, search, card actions, disclosure panels, notes, and server actions are keyboard reachable.
  - Hover-only actions also appear on focus-within.
  - Touch users can reach edit/manage actions without relying on hover.
- Contrast/readability:
  - Muted text must remain readable in light and dark mode.
  - Availability/health is communicated by text/icon plus color.
  - Telemetry uses minimum readable sizes and tabular alignment.
- Screen-reader semantics:
  - Dashboard summary uses live regions only for meaningful availability/freshness transitions.
  - Default cards visually avoid textual availability counts, but accessible names may summarize server name, status, available/total GPU count, model, freshness, and scope for screen-reader users.
  - GPU rows announce index, availability, utilization, memory, and all users.
  - Shared occupancy is explicit in GPU row text, not only badge color.
- Reduced motion and sensory considerations:
  - Respect prefers-reduced-motion for card entry, masonry movement, bar transitions, breathing dots, and hover lift.
  - Avoid flashing or fast pulses.

## Responsive behavior
- Supported breakpoints/devices:
  - Desktop >=1200px, tablet 768–1199px, mobile <768px.
- Default layout adaptations:
  - Desktop: masonry/equivalent multi-column cards when width permits.
  - Tablet: two readable columns or masonry columns based on available width.
  - Mobile: one column, freshness first, scope scrollable, actions in one overflow menu.
  - Model/VRAM, availability dots, and users remain visible/wrapping at every width; telemetry stays secondary.
- Deferred future layout:
  - Compact availability-board responsive behavior is out of current scope and must not drive acceptance now.
- Touch/hover differences:
  - Drag reorder is Default desktop-only unless a touch-safe reorder control is added.
  - Edit and management remain explicitly reachable on touch.

## Interaction states
- Loading: quiet skeletons preserving final card/row geometry; no full-page spinner after first data.
- Empty: distinguish no registered servers, no servers in scope, no free GPUs in current scope, and no search matches.
- Error: retain last-known data, label it stale, show retry, and distinguish socket failure from API failure.
- Success: refresh timestamp updates without celebratory motion.
- Disabled: preserve readable reason and do not rely on opacity alone.
- Offline/slow network:
  - Header identifies polling fallback, delayed refresh, or failed refresh.
  - Server collector status remains distinct from dashboard transport state.
  - Full/offline servers stay quiet unless action is needed; do not turn the screen into an incident wall.
- Expansion:
  - Default card expansion preserves scroll position and avoids layout jumps where practical.

## Content voice
- Tone: terse, research-lab practical, calm.
- Terminology:
  - "사용 가능", "사용 중", "공유", "정상", "지연", "오프라인", "확인 중", "마지막 업데이트".
  - Use "내부망", "외부망", "전체" consistently.
  - GPU memory is formatted as integer "21/24 GB" and capacity as integer "80 GB".
- Microcopy rules:
  - Prefer direct availability/status plus timestamp.
  - Avoid vague "문제 있음" when a reason exists.
  - Destructive actions state the affected server.
  - Development diagnostics are labeled development-only.

## Behavior preservation
- Preserve:
  - SvelteKit app behavior, existing data contracts, live WebSocket/polling behavior, collector behavior, and auth behavior.
  - Internal/External/All scope semantics.
  - Server CRUD, delete confirmation, notes behavior, logs, debug route, filters/search where present, and drag/manual order behavior.
  - Existing production isolation: do not edit `~/workspace/monitoring_v2` for this redesign.
- Default view:
  - Remains default and respects existing manual server order.
  - Masonry layout must not mutate persisted server order.
  - Header simplification must not remove edit/manage access.
- Deferred future work:
  - Compact availability-board research/reference may be retained as future work only.
  - Do not implement, wire, commit, or require Compact behavior in this scope.

## Implementation constraints
- Framework/styling system: SvelteKit 5, Tailwind, existing global app.css tokens.
- Design-token constraints:
  - Map exact Apple Liquid Glass base tokens first.
  - Remove legacy rose/pink surface rules and broad conflicting overrides during implementation.
  - Keep selectable blue/violet/emerald accents independent from light/dark mode.
- Performance constraints:
  - No new frontend dependency for layout or animation unless explicitly approved.
  - Masonry/resize/layout work must be batched with requestAnimationFrame and observed only where needed.
  - WebSocket/polling behavior and live collector must remain unchanged.
- Compatibility constraints:
  - Production project `~/workspace/monitoring_v2` is not edited.
  - Development project and tmux services remain isolated.
  - Do not use CSS zoom.
- Test/screenshot expectations:
  - Svelte diagnostics and production build for implementation work.
  - Default dark desktop at 1440x1000 and light desktop at 1440x1000.
  - Default dark mobile at 390x844 and light mobile at 390x844.
  - Default: masonry gaps, simplified headers, network only in All scope, hover/focus edit access, promoted model/VRAM, unboxed per-GPU availability dots with no visible `3 available`-style text, no boxed `G#` labels, all-user wrapping, quiet telemetry, quiet unboxed footer, integer memory, restrained semantic color, fewer pills, weak borders/shadows, no glow/glass excess, and no CSS zoom.
  - Reduced motion checks for hover, expansion, bar motion, and initial entry.

## Visual QA expectations
- Default view QA:
  - Compare desktop and mobile screenshots against this contract, not the compact reference.
  - Verify masonry/equivalent layout removes large blank space across unequal GPU counts.
  - Verify server header is simpler and calmer while preserving health, IP, refreshed time, and network-in-All behavior.
  - Verify all user names wrap and remain visible; no ellipsis hides ownership, including shared GPUs with multiple users.
  - Verify model/VRAM is promoted, memory capacities are integer GB, and utilization/memory telemetry is secondary rather than equal-strength dual bars.
  - Verify footer keeps system/notes functionality in one quiet area without boxed sections, nested glass clutter, or excessive dividers.
  - Verify semantic colors are restrained and pills/borders are reduced.
- Deferred future QA:
  - Availability-board research/reference may be noted for future work only. Do not require Compact Visual Ralph, Compact screenshots, Compact implementation files, or Compact acceptance verdicts for this current default-card pass.

## Open questions
- [x] Product purpose: researchers choose an independent GPU server for training; server selection and immediate availability dominate.
- [x] Current scope: original/default card dashboard improvement first.
- [x] Compact scope: deferred future work only; no Compact implementation, commit scope, Visual Ralph requirement, implementation-file scope, or acceptance verdict now.
- [x] Manual order scope: preserve current default-card behavior; masonry must not mutate persisted order.
- [x] Threshold policy: preserve existing collector/display thresholds unless a separate change is requested.
- [x] Notes semantics: remain free text with current expiry/auth behavior.
- [x] Debug exposure: remain under Manage/settings and clearly development-only.
