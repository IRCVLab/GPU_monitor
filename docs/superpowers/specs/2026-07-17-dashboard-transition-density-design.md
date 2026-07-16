# Dashboard Transition Density Design

## Goal

Make the dashboard feel denser without making it harder to scan:
compact usernames should be slightly easier to read, theme changes should reveal the destination DOM snapshot from the toggle button center, and dashboard preferences must remain browser-local one-year cookies.

## Scope and guardrails

- Work only in the DEV repo at `/home/ircv/workspace/monitoring_v2_dev`.
- Do not touch LIVE.
- Current DEV smoke target: `http://localhost:5174`.
- Implementation may change frontend source and tests, but this documentation task only updates this spec and the matching plan.
- Every frontend command in the plan must load NVM and use Node 24 first:

```bash
source ~/.nvm/nvm.sh
nvm use 24
```

## Current surface

- `frontend/src/routes/+page.svelte`: owns theme-toggle flow, reveal lock state, cached toggle-button center, `activeTab`, `dashboardView`, and the current overlay-based reveal.
- `frontend/src/app.css`: owns the current `.theme-mode-reveal` overlay rules; native View Transition pseudo-element rules belong here.
- `frontend/src/lib/styles/monitor-compact.css`: `.compact-slot__username` is still `0.58rem` and ellipsized.
- `frontend/src/lib/components/CompactServerRow.svelte`: already renders one username row per user, so the density fix should be CSS-first.
- `frontend/src/lib/stores/theme.ts`, `dashboardPrefs.ts`, `order.ts`, `frontend/src/routes/+page.svelte`, and `frontend/src/lib/utils/cookies.ts`: define the browser-local preference boundary.

## Theme reveal design

### Supported path

Use native View Transitions only when both conditions are true:

- `document.startViewTransition` exists.
- `prefers-reduced-motion: reduce` is not active.

The supported flow is:

1. Ignore the request if the theme reveal is already locked.
2. Read the visible theme button center; fall back to the cached/default center only when the button is not visible.
3. Compute the farthest-corner radius.
4. Set `--theme-reveal-x`, `--theme-reveal-y`, and `--theme-reveal-radius` on `document.documentElement` before starting the transition.
5. Call `document.startViewTransition(() => setThemeMode(nextMode))` so the destination DOM snapshot is captured after the mode changes.
6. `await transition.finished`.
7. In `finally`, restore focus when requested, clear the lock, and remove the CSS variables from the root element.

This replaces the old overlay timing. There must be no delayed `setThemeMode(nextMode)` after an overlay covers the viewport.

### Reduced-motion and unsupported path

Reduced-motion users and browsers without `document.startViewTransition` get an immediate swap:

1. Set the lock.
2. Call `setThemeMode(nextMode)` synchronously.
3. Restore focus when requested.
4. Clear the lock and any root CSS variables in `finally`.

Do not provide a second animation fallback.

### Removed legacy path

Remove the old flat overlay implementation and its tests:

- `.theme-mode-reveal`
- `.theme-mode-reveal__edge`
- `theme-mode-toggle-proxy`
- `overlay.animate(...)`
- `edge.animate(...)`
- `covered` state
- overlay/proxy cleanup branches
- delayed `setThemeMode(nextMode)` after the overlay animation

## View Transition CSS design

Add root View Transition pseudo-element rules in `frontend/src/app.css`:

- `::view-transition-old(root)` and `::view-transition-new(root)` must suppress the browser's default root crossfade and blending behavior.
- Use `mix-blend-mode: normal` for both snapshots.
- The old root snapshot should not animate by default.
- The new root snapshot is the destination snapshot and should reveal with a 520ms circular `clip-path` animation from `circle(0 at var(--theme-reveal-x) var(--theme-reveal-y))` to `circle(var(--theme-reveal-radius) at var(--theme-reveal-x) var(--theme-reveal-y))`.
- Do not keep the old overlay color-token matrix; the destination snapshot provides the revealed visuals.

## Preference persistence design

All dashboard preferences remain browser-local cookies. Do not use `localStorage`, server persistence, or cross-device sync.

| Preference | Current owner | Required read/write behavior |
| --- | --- | --- |
| `themeMode` | `frontend/src/lib/stores/theme.ts` | Read `themeMode`, write `themeMode` |
| `materialTheme` | `frontend/src/lib/stores/theme.ts` | Read `materialTheme`, write `materialTheme`; legacy reads may remain only for migration |
| `dashboardView` | `frontend/src/lib/stores/dashboardPrefs.ts` | Read `dashboardView`, write `dashboardView` |
| `dashboardLayout` | `frontend/src/lib/stores/dashboardPrefs.ts` | Read `dashboardLayout`, write `dashboardLayout` |
| `activeTab` | `frontend/src/routes/+page.svelte` | Read `activeTab`, write `activeTab` |
| `serverOrder` | `frontend/src/lib/stores/order.ts` | Read `serverOrder`, write `serverOrder` |

All writes must share the `writeCookie` helper defaults: one-year `max-age=31536000`, `path=/`, and `SameSite=Lax`.

## Compact username density design

- Keep `CompactServerRow.svelte`'s per-user row structure.
- Keep `.compact-slot__user-list` as a grid so each user remains on its own row.
- Set `.compact-slot__username` to `font-size: 0.65rem` and `line-height: 1.15`.
- Keep the username text overflow-safe with `min-width: 0`, `white-space: nowrap`, `overflow: hidden`, and `text-overflow: ellipsis`.
- Do not introduce horizontal overflow to compact cells.

## Files and tests

Production files expected in the later implementation:

- `frontend/src/routes/+page.svelte`
- `frontend/src/app.css`
- `frontend/src/lib/styles/monitor-compact.css`
- `frontend/src/lib/components/CompactServerRow.svelte` only if CSS cannot preserve the existing per-user row contract

Contract tests expected in the later implementation:

- `frontend/src/routes/page-view.contract.test.ts`
- `frontend/src/lib/components/compact-dashboard-task4.contract.test.ts`
- `frontend/src/app-css-token.contract.test.ts`
- `frontend/src/lib/stores/theme.contract.test.ts`
- `frontend/src/lib/stores/dashboardPrefs.contract.test.ts`
- `frontend/src/lib/stores/order.contract.test.ts`
- `frontend/src/lib/utils/cookies.contract.test.ts`

The test suite must also cover `activeTab` cookie read/write behavior. This can live in `page-view.contract.test.ts` if the page remains the owner.

## Acceptance

- Supported browsers use `document.startViewTransition` and reveal the destination root snapshot from the theme toggle center.
- `::view-transition-old(root)` and `::view-transition-new(root)` do not crossfade or blend by default.
- Reduced-motion and unsupported browsers swap immediately while preserving focus and lock cleanup semantics.
- Old overlay/proxy/edge animation code and overlay token CSS are gone.
- Tests no longer assert or require the removed hard-cut/overlay path.
- Compact usernames are readable at `0.65rem`, use `line-height: 1.15`, stay one row per user, and remain overflow-safe.
- `themeMode`, `materialTheme`, `dashboardView`, `dashboardLayout`, `activeTab`, and `serverOrder` each read from and write to cookies with shared one-year `path=/` `SameSite=Lax` behavior.
- No preference path uses `localStorage`, backend persistence, or cross-device sync.
