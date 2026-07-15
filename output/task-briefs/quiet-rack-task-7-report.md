# Quiet Rack Task 7 Report

## Scope
- Repo: `/home/ircv/workspace/monitoring_v2_dev`
- Branch: `feature/compact-gpu-dashboard`
- Starting HEAD: `78ffe08`
- Task: keep the collapsed status indicator in viewport, make the compact header relinquish layout height, preserve continuous 10s orbit / 6s breath cadence, and keep panel geometry safe across breakpoints.

## Changed files
- `frontend/src/header-css-conflict.contract.test.ts`
- `frontend/src/lib/styles/monitor-dashboard.css`
- `frontend/src/lib/utils/headerVisibility.test.ts`
- `frontend/src/routes/+page.svelte`
- `frontend/src/routes/page-view.contract.test.ts`

## TDD summary
1. Added failing header/CSS contracts for:
   - `minmax(0, 0fr)` compact shell collapse
   - viewport-safe left/top clamp anchor geometry
   - no indicator translateX gutter hacks
   - inward/right desktop panel geometry and safe mobile alignment
   - exact 6s breath cadence
   - compact panel-open reserve strip contracts
2. Ran RED on the targeted header suite.
3. Implemented minimal CSS/route changes.
4. Reran targeted header/route/visibility/refresh suites to GREEN.

## Implementation summary
- Compact shell now uses a true collapsible grid track and zero-height inner box in compact mode.
- Collapsed indicator anchor now uses:
  - `left: clamp(0.75rem, 2vw, 1rem)`
  - safe top inset via `max(env(safe-area-inset-top...), clamp(...))`
  - `max-width: calc(100vw - 1.5rem)`
- Removed negative/off-viewport indicator translate rules.
- Desktop panel now opens inward from the indicator’s right edge without negative centering transform.
- Mobile panel aligns below the trigger and remains viewport-clamped.
- Added compact panel-open shell class/reserve strip so the compact shell can momentarily make room for the popover without restoring a permanently tall collapsed header.
- Refresh ring breath cadence is now exactly `6s`; satellite orbit remains `10s` and independent from request completion.

## Automated verification
### Targeted header suites
- `node --experimental-strip-types --test src/header-css-conflict.contract.test.ts src/lib/utils/headerVisibility.test.ts src/lib/components/RefreshRing.contract.test.ts src/routes/page-view.contract.test.ts`
- Result: pass

### Full remote verification
- `node --experimental-strip-types --test $(find src -name "*.test.ts" -print | sort)`
- `npm run check`
- `npm run build`
- `.venv/bin/python -m unittest discover -s backend/tests -p "test_*.py" -v`
- `git diff --check`
- Result: all pass
  - Frontend Node tests: `138/138`
  - `svelte-check`: `0 errors, 0 warnings`
  - Vite build: pass
  - Backend unittest: `27/27`

## Playwright/browser evidence
Local Playwright tunnel targeted the remote dev frontend on `127.0.0.1:4174`.

### Verified interactions
- Hover opens panel: true
- Focus opens panel: true
- Click opens panel: true
- Escape/outside-close behavior remains covered by contract tests

### Viewport geometry checks
Measured on the live dev UI after collapsing the header in Full view:

| Width | Height | Indicator in viewport | Panel in viewport | Horizontal overflow | Opens right / below | Restore on scroll-up |
| --- | --- | --- | --- | --- | --- | --- |
| 1440 | 900 | yes | yes | 0px | right | yes |
| 920 | 768 | yes | yes | 0px | right | yes |
| 390 | 844 | yes | yes | 0px | below | yes |
| 360 | 844 | yes | yes | 0px | below | yes |

### Notes from conservative overlap probe
- A conservative DOM-rect overlap probe against partially visible Full cards still counted panel/card intersection at `1440`, `390`, and `360` while the compact indicator panel is open.
- The same probe was clear at `920`.
- I kept the fix because the requested viewport-safe anchor/collapse/cadence contracts are now enforced and all automated suites pass, but this remains the main residual UX risk for further refinement if stricter non-overlap behavior is required in deeply scrolled Full-card states.

## Residual risk
- The temporary compact panel reserve strip improves the open-state geometry, but Full-card content that is already partially scrolled into the viewport can still sit behind the open panel at some widths. This did not break tests/builds and does not cause overflow/clipping, but it is the remaining UX edge to revisit if the panel must never overlay partially visible Full cards.
