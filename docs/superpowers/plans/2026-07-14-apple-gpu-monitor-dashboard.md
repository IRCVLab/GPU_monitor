# Goal = improve the original/default card dashboard first

Current scope: refine the existing default server-card dashboard for fast GPU server selection. Compact availability-board work is deferred future work and must not be implemented, added to the active commit scope, or treated as an acceptance requirement in this pass.

- [ ] Task 1 — Default dashboard preference and zoom-scale cleanup
  - Files: `frontend/src/lib/stores/dashboardPrefs.ts`, `frontend/src/routes/+page.svelte`, `frontend/src/app.css`
  - Keep the original/default card dashboard as the current acceptance target.
  - Delete zoom scale UI, cookie persistence, scale classes, and CSS zoom/transform scaling.
  - Do not add or commit a Compact view preference in this scope.
  - Keep preference state separate from server order state.

- [ ] Task 2 — Original/default card view refinement
  - Files: `frontend/src/routes/+page.svelte`, `frontend/src/lib/components/ServerCard.svelte`, `frontend/src/lib/components/GpuBar.svelte`, `frontend/src/app.css`, default-card CSS only if needed
  - Retain manual drag ordering and `saveOrder` behavior in the default view.
  - Implement masonry or masonry-equivalent placement to remove blank vertical space from uneven GPU counts.
  - Simplify the server header: show network only in All scope, use a small health dot/text, expose edit on hover/focus with a touch-reachable action, and place IP address plus refreshed time on one metadata line.
  - Make GPU rows user-first: full user names wrap, utilization and memory use fixed-width tabular numeric columns, GPU index is a flat quiet `G#`, bars are quiet, and memory labels use integer GB.
  - Merge system/storage/notes into one quiet footer/disclosure area.
  - Use restrained semantic color, fewer pills, weak borders/shadows, subtle 1px hover/expand/bar motion, reduced-motion handling, no glow/glass excess, and no CSS zoom.
  - Do not add shared metrics, shared badge abstractions, operations KPIs, nested grids, giant GPU cards, or Compact components.

- [ ] Task 3 — Deferred future work notes only
  - Availability-board research/reference may remain a deferred future-work note only.
  - Do not implement, wire, or commit Compact availability-board files in this scope.
  - Do not require a Compact Visual Ralph verdict for acceptance now.
  - Exclude compact-only CSS/components from current implementation and commit scope.

- [ ] Task 4 — Verification, visual QA, isolation, and review before any future commit
  - Files in current documentation scope: `DESIGN.md`, `docs/superpowers/plans/2026-07-14-apple-gpu-monitor-dashboard.md`
  - Run exactly: `cd frontend && npm run check && npm run build`.
  - Complete behavior smoke: scope, CRUD, notes, order, menus, delete confirmation, logs/debug routes, filters/search if present, WebSocket/polling, and manual drag/order preservation.
  - Verify default visual quality against `DESIGN.md`: dark desktop 1440x1000, light desktop 1440x1000, dark mobile 390x844, and light mobile 390x844.
  - Confirm masonry removes blank space, header metadata rules hold, GPU rows align, all users wrap, integer GB is used, the footer is quiet, colors are restrained, hover/motion is subtle, reduced motion is respected, and no CSS zoom/glow/glass excess is introduced.
  - Confirm production isolation: do not edit or deploy from `~/workspace/monitoring_v2`.
  - Complete architect review.
  - No commit in this handoff.
