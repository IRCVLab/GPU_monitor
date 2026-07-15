# Quiet Rack Task 4 Report

## Scope
Unify Full GPU state and metric visual language with Compact using a single selected-theme accent, widen the Mem track, preserve integer memory display, and keep dense multi-user rows intact.

## Files
- `frontend/src/lib/components/GpuBar.contract.test.ts`
- `frontend/src/lib/styles/monitor-cards.contract.test.ts`
- `frontend/src/lib/styles/monitor-cards.css`
- `frontend/src/routes/page-view.contract.test.ts`

## RED evidence
Command:

```bash
cd frontend
node --experimental-strip-types --test src/lib/components/GpuBar.contract.test.ts src/lib/styles/monitor-cards.contract.test.ts src/routes/page-view.contract.test.ts
```

Result:
- failed: 5 tests
- failures proved the old Full-card split still existed:
  - Util fill was `var(--chart-2)` instead of `var(--ops-primary)`
  - Mem fill still used chart-token styling instead of the same primary accent
  - GPU available/occupied index styles still used `var(--chart-2)`
  - metrics grid was still `minmax(0, 0.78fr) minmax(0, 1.22fr)` instead of `minmax(0, 0.72fr) minmax(0, 1.28fr)`
  - narrow mobile override still collapsed `.monitor-gpu-row__metrics` to `1fr`

## GREEN evidence
Targeted command:

```bash
cd frontend
node --experimental-strip-types --test src/lib/components/GpuBar.contract.test.ts src/lib/styles/monitor-cards.contract.test.ts src/routes/page-view.contract.test.ts
```

Result:
- passed: 40 tests
- confirmed:
  - available `G#` uses a dark/quiet surface with `var(--ops-primary)` border/text
  - occupied `G#` uses `var(--ops-primary)` fill/border with `var(--ops-primary-fg)` foreground
  - unknown stays neutral
  - Util and Mem now share one selected accent with quieter Mem fill
  - metrics grid is exactly `minmax(0, 0.72fr) minmax(0, 1.28fr)`
  - memory value column remains `8ch`
  - no mobile fallback overrides the two-column metric split

## Full frontend verification
Command:

```bash
cd frontend
node --experimental-strip-types --test $(find src -name "*.test.ts" -print | sort)
npm run check
npm run build
```

Result:
- Node tests: 127 passed, 0 failed
- `npm run check`: 0 errors, 0 warnings
- `npm run build`: passed

## Notes
- No live repo/process paths were touched.
- No extra badges or new GPU colors were introduced.
- Integer memory display and multi-user rows were preserved.

## Task 4 contrast follow-up (2026-07-15)

### Scope
Fix the verified Full-card contrast regression so available `G#` keeps the accent border/fill language but uses high-contrast `var(--ops-fg)` text on the quiet surface, and occupied `G#` uses one semantic `--ops-on-primary` token instead of `--ops-primary-fg`.

### Files
- `frontend/src/app-css-token.contract.test.ts`
- `frontend/src/app.css`
- `frontend/src/lib/styles/monitor-cards.contract.test.ts`
- `frontend/src/lib/styles/monitor-cards.css`
- `frontend/src/routes/page-view.contract.test.ts`

### RED evidence
Command:

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use --silent 24
cd frontend
node --experimental-strip-types --test src/app-css-token.contract.test.ts src/lib/styles/monitor-cards.contract.test.ts src/routes/page-view.contract.test.ts
```

Result:
- failed: 7 tests
- failures proved the old contracts were still present:
  - `:root`, `html.dark`, `html.light`, and `html.rose` had no `--ops-on-primary`
  - light-violet had no semantic override to white
  - Full available `G#` still used `var(--ops-primary)` text on the quiet surface
  - Full occupied `G#` still used `var(--ops-primary-fg)` instead of a central semantic token

### GREEN evidence
Targeted command:

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use --silent 24
cd frontend
node --experimental-strip-types --test src/app-css-token.contract.test.ts src/lib/styles/monitor-cards.contract.test.ts src/routes/page-view.contract.test.ts
```

Result:
- passed: 40 tests
- confirmed:
  - available Full `G#` keeps the selected accent border and uses `var(--ops-fg)` text
  - occupied Full `G#` keeps the selected accent fill and uses `var(--ops-on-primary)`
  - `--ops-on-primary` is centralized in `app.css` and only light-violet overrides it to white
  - contrast math is validated for shipped accent/theme combinations:
    - default light `#297cef` on `#040609`: `5.05`
    - emerald light `#00a381` on `#040609`: `6.33`
    - violet light `#864ad2` on `#ffffff`: `5.35`
    - default dark `#3a8cff` on `#040609`: `6.16`
    - emerald dark `#00b793` on `#040609`: `7.92`
    - violet dark `#9b61ea` on `#040609`: `5.11`
    - rose `#a06bdc` on `#040609`: `5.44`

### Full frontend verification
Command:

```bash
export NVM_DIR="$HOME/.nvm"
. "$NVM_DIR/nvm.sh"
nvm use --silent 24
cd frontend
node --experimental-strip-types --test $(find src -name "*.test.ts" -print | sort)
npm run check
npm run build
```

Result:
- Node tests: 129 passed, 0 failed
- `npm run check`: 0 errors, 0 warnings
- `npm run build`: passed

### Notes
- Live paths/processes were not touched.
- Compact occupied badge text was not broadened; the new token is reusable for later follow-up if needed.
