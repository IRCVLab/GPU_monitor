# Dashboard Transition Density Implementation Plan

> **For agentic workers:** implement task-by-task and verify after each task. Do not commit as part of this plan unless a separate user instruction explicitly requests commits.

**Goal:** Ship a button-centered native View Transition theme reveal and denser compact usernames while preserving browser-local cookie preferences, reduced-motion behavior, and existing dashboard geometry.

**Architecture:** Keep `frontend/src/routes/+page.svelte` as the orchestration point. Put native View Transition styling in `frontend/src/app.css`, compact username density in `frontend/src/lib/styles/monitor-compact.css`, and keep preference persistence behind `readCookie`/`writeCookie`.

**Tech Stack:** Svelte 5, CSS View Transitions, TypeScript, Node test runner, NVM Node 24.

**Environment guardrails:**

- DEV repo only: `/home/ircv/workspace/monitoring_v2_dev`.
- LIVE is out of scope.
- Current DEV smoke target: `http://localhost:5174`.
- Every frontend command must start with NVM Node 24:

```bash
source ~/.nvm/nvm.sh
nvm use 24
```

---

### Task 1: Lock the desired contracts with tests

**Files:**

- Create: `frontend/src/lib/utils/cookies.contract.test.ts`
- Create: `frontend/src/lib/stores/order.contract.test.ts`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- Modify: `frontend/src/app-css-token.contract.test.ts`
- Modify: `frontend/src/lib/stores/theme.contract.test.ts`
- Modify: `frontend/src/lib/stores/dashboardPrefs.contract.test.ts`

- [ ] Add red assertions for `document.startViewTransition` feature detection and the supported call order: set root CSS vars, call `document.startViewTransition(() => setThemeMode(nextMode))`, await `transition.finished`, then clean up focus, lock, and CSS vars in `finally`.
- [ ] Add red assertions that reduced-motion and unsupported browsers use immediate `setThemeMode(nextMode)` with no fallback animation.
- [ ] Add red assertions that the old overlay path is gone: no `.theme-mode-reveal`, `.theme-mode-reveal__edge`, `theme-mode-toggle-proxy`, `overlay.animate`, `edge.animate`, `covered`, or delayed post-overlay `setThemeMode(nextMode)`.
- [ ] Replace old hard-cut/overlay tests. Tests must assert the immediate reduced/unsupported swap and native supported path, not a removed hard-cut or overlay behavior.
- [ ] Add CSS-token assertions for `::view-transition-old(root)`, `::view-transition-new(root)`, no default root crossfade/blending, and a 520ms circular reveal on the new destination snapshot.
- [ ] Add compact username assertions for `font-size: 0.65rem`, `line-height: 1.15`, per-user grid rows, `min-width: 0`, `white-space: nowrap`, `overflow: hidden`, and `text-overflow: ellipsis`.
- [ ] Add cookie assertions for each preference: `themeMode`, `materialTheme`, `dashboardView`, `dashboardLayout`, `activeTab`, and `serverOrder` must each read from and write to cookies.
- [ ] Add shared cookie helper assertions for one-year `max-age=31536000`, `path=/`, and `SameSite=Lax`.
- [ ] Add negative assertions that preference persistence does not use `localStorage`, backend persistence, or cross-device sync.
- [ ] Run the focused contract suite:

```bash
source ~/.nvm/nvm.sh
nvm use 24
cd frontend
node --test --experimental-strip-types \
  src/lib/utils/cookies.contract.test.ts \
  src/lib/stores/theme.contract.test.ts \
  src/lib/stores/dashboardPrefs.contract.test.ts \
  src/lib/stores/order.contract.test.ts \
  src/routes/page-view.contract.test.ts \
  src/lib/components/compact-dashboard-task4.contract.test.ts \
  src/app-css-token.contract.test.ts
```

Expected before implementation: failures point to the old overlay path, old compact username sizing, and missing preference tests.

### Task 2: Fix compact username density

**Files:**

- Modify: `frontend/src/lib/styles/monitor-compact.css`
- Modify only if required: `frontend/src/lib/components/CompactServerRow.svelte`
- Modify: `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`

- [ ] Keep the current per-user row structure in `CompactServerRow.svelte` unless CSS alone cannot preserve it.
- [ ] Keep `.compact-slot__user-list` as a grid so each user stays on its own row.
- [ ] Update `.compact-slot__username` to `font-size: 0.65rem` and `line-height: 1.15`.
- [ ] Keep overflow safe with `min-width: 0`, `white-space: nowrap`, `overflow: hidden`, and `text-overflow: ellipsis`.
- [ ] Re-run the compact contract test:

```bash
source ~/.nvm/nvm.sh
nvm use 24
cd frontend
node --test --experimental-strip-types src/lib/components/compact-dashboard-task4.contract.test.ts
```

Expected after the density fix: pass.

### Task 3: Replace the overlay reveal with native View Transitions

**Files:**

- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/app.css`
- Modify: `frontend/src/routes/page-view.contract.test.ts`
- Modify: `frontend/src/app-css-token.contract.test.ts`

- [ ] Feature-detect native View Transitions with `typeof document.startViewTransition === 'function'`.
- [ ] In the supported path, set `--theme-reveal-x`, `--theme-reveal-y`, and `--theme-reveal-radius` on `document.documentElement` before starting the transition.
- [ ] Call `document.startViewTransition(() => setThemeMode(nextMode))` so the new root snapshot is the destination theme.
- [ ] Await `transition.finished`.
- [ ] Use `finally` to restore focus when requested, clear the rapid-click lock, and remove the root CSS variables.
- [ ] In reduced-motion or unsupported browsers, perform an immediate `setThemeMode(nextMode)` and run the same focus/lock/CSS-var cleanup without animation.
- [ ] Remove the old flat overlay/proxy implementation from `+page.svelte`: overlay element creation, edge element creation, toggle proxy, `overlay.animate`, `edge.animate`, `covered`, overlay cleanup branches, and delayed post-overlay `setThemeMode(nextMode)`.
- [ ] Replace the old `.theme-mode-reveal` CSS with root View Transition CSS:
  - `::view-transition-old(root)` and `::view-transition-new(root)` suppress default crossfade/blending.
  - Both snapshots use `mix-blend-mode: normal`.
  - The old root snapshot has no default animation.
  - The new destination snapshot uses a 520ms circular `clip-path` reveal from the button-centered CSS vars.
- [ ] Re-run the transition and CSS contract tests:

```bash
source ~/.nvm/nvm.sh
nvm use 24
cd frontend
node --test --experimental-strip-types \
  src/routes/page-view.contract.test.ts \
  src/app-css-token.contract.test.ts
```

Expected after the transition fix: pass.

### Task 4: Preserve cookie-only preferences

**Files:**

- Modify: `frontend/src/lib/stores/theme.ts`
- Modify: `frontend/src/lib/stores/dashboardPrefs.ts`
- Modify: `frontend/src/lib/stores/order.ts`
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/lib/utils/cookies.ts` only if the shared attributes are not already centralized
- Modify: related contract tests from Task 1

- [ ] Confirm `themeMode` reads and writes the `themeMode` cookie.
- [ ] Confirm `materialTheme` reads and writes the `materialTheme` cookie; legacy reads may remain only for migration.
- [ ] Confirm `dashboardView` reads and writes the `dashboardView` cookie.
- [ ] Confirm `dashboardLayout` reads and writes the `dashboardLayout` cookie.
- [ ] Confirm `activeTab` reads and writes the `activeTab` cookie.
- [ ] Confirm `serverOrder` reads and writes the `serverOrder` cookie in `order.ts`.
- [ ] Confirm all writes use the shared one-year `path=/` `SameSite=Lax` `writeCookie` behavior.
- [ ] Confirm none of these preferences use `localStorage`, backend persistence, or cross-device sync.
- [ ] Re-run the preference contract tests:

```bash
source ~/.nvm/nvm.sh
nvm use 24
cd frontend
node --test --experimental-strip-types \
  src/lib/utils/cookies.contract.test.ts \
  src/lib/stores/theme.contract.test.ts \
  src/lib/stores/dashboardPrefs.contract.test.ts \
  src/lib/stores/order.contract.test.ts \
  src/routes/page-view.contract.test.ts
```

Expected after the preference pass: pass.

### Task 5: Validate and smoke test

**Files:** none expected.

- [ ] Run the focused contract suite:

```bash
source ~/.nvm/nvm.sh
nvm use 24
cd frontend
node --test --experimental-strip-types \
  src/lib/utils/cookies.contract.test.ts \
  src/lib/stores/theme.contract.test.ts \
  src/lib/stores/dashboardPrefs.contract.test.ts \
  src/lib/stores/order.contract.test.ts \
  src/routes/page-view.contract.test.ts \
  src/lib/components/compact-dashboard-task4.contract.test.ts \
  src/app-css-token.contract.test.ts
```

- [ ] Run the frontend checks:

```bash
source ~/.nvm/nvm.sh
nvm use 24
cd frontend
npm run check
npm run build
```

- [ ] Smoke the current DEV app at `http://localhost:5174` only; do not touch LIVE.
- [ ] Verify three theme cases in the browser: supported View Transition with normal motion, reduced motion, and unsupported View Transition.
- [ ] Verify compact dashboard usernames stay one row per user and do not overflow horizontally.
- [ ] Verify `themeMode`, `materialTheme`, `dashboardView`, `dashboardLayout`, `activeTab`, and `serverOrder` cookies are written with one-year `path=/` `SameSite=Lax` behavior.
- [ ] Run whitespace validation before reporting completion:

```bash
git diff --check -- docs/superpowers/specs/2026-07-17-dashboard-transition-density-design.md docs/superpowers/plans/2026-07-17-dashboard-transition-density.md
```
