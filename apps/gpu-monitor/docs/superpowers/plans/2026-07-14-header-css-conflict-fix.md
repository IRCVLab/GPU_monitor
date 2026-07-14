# Header CSS Conflict Fix Plan

**Date:** 2026-07-14  
**Scope:** development branch only (`feature/compact-gpu-dashboard`)

## Browser evidence

At 1440x1000 after intentional downward scroll:

- `.ops-header-shell` has `ops-header-compact` but its measured height remains `64px`.
- `.ops-indicator-anchor` computes to `position: relative`, `width: 88px`, and a centered x-position.
- `.ops-indicator` renders near the viewport center instead of the right outer gutter.
- Full and Compact page width otherwise remain overflow-free.

## Root cause


### Second browser verification round

After the first tail-block removal and reload at 1440px with intentional down-scroll:

- `.ops-header-shell` still had `ops-header-compact`.
- Shell measured height remained `52px`.
- `.ops-indicator-anchor` was now absolute and correctly positioned in the gutter.

Additional root cause: an earlier obsolete `@layer` header block in `frontend/src/app.css` still defined component-owned header selectors, especially `@media (min-width: 1200px) { .ops-header-compact { height: 52px; ... } }`, plus duplicate `.ops-header-shell`, `.ops-header`, `.ops-header-inner`, status/actions/menu rules. This block also competes with `frontend/src/lib/styles/monitor-dashboard.css` and must be removed so the component stylesheet is the only active header owner.


The active `frontend/src/lib/styles/monitor-dashboard.css` correctly defines an absolute, zero-layout indicator anchor and grid-row collapse. A historical header/indicator block at the end of `frontend/src/app.css` has equal-or-higher specificity and later effective cascade rules, including:

- fixed `.ops-header-compact` heights,
- hidden compact header via `display: none`,
- `.ops-header-compact .ops-indicator-anchor { position: relative; height: 64px; }`,
- left-positioned indicator rules.

Those legacy rules override the active design contract and keep occupying layout space.

## Locked behavior

1. Expanded header remains a compact 64px-class in-flow surface.
2. Down-scroll collapse reduces the shell's measured layout height below expanded height (target: zero reserved row, indicator is absolute).
3. Desktop indicator uses the active absolute anchor and sits in the right outer gutter with a 12-16px top offset.
4. No persistent fixed-height compact shell or relative 64px indicator anchor remains in global CSS.
5. Breathing remains slow and is disabled for reduced motion.
6. No Full/Compact horizontal overflow or manual-order change.

## Implementation

1. Add source-contract regression tests that fail while the legacy global compact-header/indicator rules remain.
2. Remove the obsolete global header/indicator override block from `app.css`; keep the component-owned implementation in `monitor-dashboard.css` as the single source of truth.
3. Set the active expanded header rhythm explicitly to the compact 64px-class height.
4. Move/retain the slow indicator breathing keyframes and reduced-motion rule in the component-owned stylesheet.
5. Run targeted tests, all Node tests, `npm run check`, `npm run build`, and `git diff --check`.
6. Re-run 1440px browser measurements and screenshots before continuing to soft holds.
7. Remove the earlier obsolete `app.css` header block beginning at `/* Quiet control-plane header: compact surface, one segmented scope switcher. */` and ending before `.badge-online`, preserving adjacent dashboard/log/modal rules.
8. Keep menu positioning/stacking and responsive expanded-header rhythm in `monitor-dashboard.css`; do not restore fixed compact shell height.

## Stop conditions

- Do not edit the live repository.
- Do not push or deploy.
- Do not change collector or WebSocket payload contracts.
