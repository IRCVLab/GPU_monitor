# Storage Overview Responsive and Theme Transition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Center the Storage Monitor overview in a 1440px Clean-design shell, make every server card reflow predictably across 3/2/1 columns, and remove flashing from the icon-centered circular light/dark transition.

**Architecture:** Keep the existing static HTML/CSS/JavaScript architecture and existing overview-card DOM. Replace height-prioritized masonry with deterministic ordered column stacking, add an overview-only alignment shell, and isolate native View Transition capture from ordinary theme CSS transitions. The detail workspace remains full width and all changes are covered by the existing Node regression harness before browser verification.

**Tech Stack:** Vanilla JavaScript, CSS Grid, CSS View Transitions, ResizeObserver, Node `assert` regression tests, Python pytest, Playwright CLI.

---

## File Map

- Modify `viewer/overview.js`: deterministic ordered masonry, complete relayout scheduling, optional FLIP transition on column-count changes.
- Modify `viewer/app.js`: explicit overview resize fallback, icon-center reveal helper, transition lock lifecycle, root-theme chart refresh.
- Modify `viewer/styles.css`: overview/header 1440px alignment, responsive width contract, card polish, exact icon centering, scoped flash-free View Transition CSS.
- Modify `viewer/index.html`: header inner alignment hook, guarded early theme bootstrap, initial button semantics.
- Modify `viewer/treemap.js`: derive chart theme from `html.light/html.dark` rather than OS preference.
- Modify `viewer/viewer_regression_test.js`: ordered-layout, relayout, reveal-coordinate, and transition-lock behavior tests.
- Modify `viewer/viewer.test.js`: CSS/HTML contract tests for the centered shell, scoped transition, and guarded bootstrap.
- Modify `docs/superpowers/specs/2026-07-22-storage-overview-responsive-theme-design.md`: mark the design implemented after verification.
- Do not commit `.playwright-cli/` or `output/playwright/` artifacts.

## Task 1: Lock the Responsive Layout Contract

**Files:**
- Modify: `viewer/viewer_regression_test.js:918-950`
- Modify: `viewer/viewer.test.js:900-925`

- [x] **Step 1: Replace current shortest-column expectations with deterministic ordered-column expectations**

For three columns assert `['1', '2', '3', '1', '2', '3', '1']`; for two columns assert `['1', '2', '1', '2', '1', '2', '1']`; for one column assert every item is in column `1`. Assert each card starts directly after the previous card in its assigned column and previous inline placement is cleared before recalculation.

- [x] **Step 2: Add CSS contract assertions**

Assert overview `max-width: 1440px`, centered margins, matching overview header shell alignment, 3/2/1 columns, and no overview maximum width on detail mode.

- [x] **Step 3: Run tests and verify RED**

```bash
node viewer/viewer_regression_test.js
node viewer/viewer.test.js
```

Expected: FAIL because placement is height-prioritized and no 1440px overview shell exists.

- [x] **Step 4: Commit failing tests**

```bash
git add viewer/viewer_regression_test.js viewer/viewer.test.js
git commit -m "test: define stable storage overview layout"
```

## Task 2: Implement the Centered Overview Shell

**Files:**
- Modify: `viewer/index.html:24-56`
- Modify: `viewer/styles.css:125-175,250-320,600-635`

- [x] **Step 1: Add an inner header alignment element without changing controls**

Use `width: min(100%, 1440px)` and `margin-inline: auto` in overview mode. Keep detail mode full width.

- [x] **Step 2: Add semantic overview sizing tokens**

```css
--overview-max-width: 1440px;
--overview-gutter: 24px;
--overview-card-gap: 14px;
--overview-card-min: 340px;
```

Apply the maximum width only to `body[data-shell-mode='overview'] #overviewView` and the overview header inner row.

- [x] **Step 3: Make every card share the available column width uniformly**

Keep explicit 3/2/1 column contracts. Adjust breakpoints so cards do not become narrower than the dense-card minimum and remove growth above 1440px.

- [x] **Step 4: Apply bounded Clean card polish**

Reduce diffuse light-mode shadow, preserve 1px hover lift, align card/header border contrast with GPU Monitor Clean, keep pressure colors semantic, and replace mixed `free` copy with one Korean metric phrase.

- [x] **Step 5: Run tests and verify GREEN for shell contract**

```bash
node viewer/viewer.test.js
```

Expected: new CSS/HTML contract passes; masonry behavior may remain red until Task 3.

- [x] **Step 6: Commit**

```bash
git add viewer/index.html viewer/styles.css viewer/overview.js viewer/viewer.test.js
git commit -m "feat: center storage overview layout"
```

## Task 3: Implement Stable Ordered Masonry and Complete Reflow

**Files:**
- Modify: `viewer/overview.js:567-618`
- Modify: `viewer/app.js:706-713`
- Test: `viewer/viewer_regression_test.js`

- [x] **Step 1: Implement deterministic assignment**

Use `column = index % columnCount`, start at `nextRowByColumn[column]`, update only that column, and remove shortest-column selection, `nearTieRows`, and `lastStartRow`.

- [x] **Step 2: Recalculate every card from a clean state**

Reset every `gridRow` and `gridColumn` to `auto`, force one layout read, then calculate all cards. Store column count only for animation decisions.

- [x] **Step 3: Add explicit resize fallback**

Add `relayoutOverview()` in `viewer/app.js` and invoke it from the existing throttled resize handler in addition to ResizeObserver.

- [x] **Step 4: Add restrained breakpoint FLIP motion**

On column-count changes only, record old rectangles and animate inverse `translate3d` offsets to zero using Web Animations. Skip continuous width changes and reduced-motion mode.

- [x] **Step 5: Run regression tests and verify GREEN**

```bash
node viewer/viewer_regression_test.js
node viewer/viewer.test.js
```

Expected: PASS with deterministic 3/2/1 assignment and no stale placement.

- [x] **Step 6: Commit**

```bash
git add viewer/overview.js viewer/app.js viewer/viewer_regression_test.js
git commit -m "fix: stabilize storage card reflow"
```

## Task 4: Lock the Theme Transition Contract

**Files:**
- Modify: `viewer/viewer_regression_test.js:1490-1525`
- Modify: `viewer/viewer.test.js:14-38`

- [x] **Step 1: Add a failing icon-center test**

Provide different visible-icon and button rectangles. Assert the visible icon center is used and the button is only a fallback.

- [x] **Step 2: Add a failing transition lifecycle test**

Stub `document.startViewTransition` and assert the transition lock exists before capture, destination class changes inside the callback, the lock stays through `finished`, cleanup occurs afterward, and rapid activation does not start a second transition.

- [x] **Step 3: Add static CSS/bootstrap assertions**

Assert exact 50% icon centering, scoped View Transition selectors, ordinary-transition suppression during capture, guarded `matchMedia`, and synchronized initial `aria-pressed` state.

- [x] **Step 4: Run tests and verify RED**

```bash
node viewer/viewer_regression_test.js
node viewer/viewer.test.js
```

Expected: FAIL because current code measures the button, globally applies View Transition CSS, and permits component transitions during capture.

- [x] **Step 5: Commit failing tests**

```bash
git add viewer/viewer_regression_test.js viewer/viewer.test.js
git commit -m "test: define flash-free theme reveal"
```

## Task 5: Implement the Exact Icon-Centered Flash-Free Reveal

**Files:**
- Modify: `viewer/app.js:20-115`
- Modify: `viewer/styles.css:90-125,277-300`
- Modify: `viewer/index.html:7-18,40-55`
- Modify: `viewer/treemap.js:60-80`

- [x] **Step 1: Add a pure reveal-origin helper**

Create `resolveThemeRevealOrigin(button, root)` that selects the currently visible sun/moon icon, reads its viewport rectangle, and returns its center. Fall back to button center, then a safe viewport coordinate.

- [x] **Step 2: Center icons mathematically**

Use `left: 50%`, `top: 50%`, and `translate(-50%, -50%)` with subtle rotation/scale states.

- [x] **Step 3: Isolate snapshot capture**

Add `html.theme-transitioning` before `startViewTransition`; disable ordinary element transitions while present; scope root View Transition pseudo-elements to this state; keep old snapshot static and animate only new snapshot clip-path.

- [x] **Step 4: Remove the second color tween**

Apply destination root class synchronously in the transition update callback, await update completion and `finished`, and remove lock only after final styles are live. Always clean up after rejection.

- [x] **Step 5: Synchronize persistence, semantics, and chart colors**

Guard early `matchMedia`, preserve `themeMode` cookie, synchronize `aria-pressed`, and derive chart theme from the root class before OS preference.

- [x] **Step 6: Preserve fallback and reduced motion**

Apply destination immediately without reveal when unsupported or reduced-motion is enabled. Preserve focus and functionality.

- [x] **Step 7: Run regression tests and verify GREEN**

```bash
node viewer/viewer_regression_test.js
node viewer/viewer.test.js
```

Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add viewer/app.js viewer/styles.css viewer/index.html viewer/treemap.js viewer/viewer_regression_test.js viewer/viewer.test.js
git commit -m "fix: smooth storage theme transition"
```

## Task 6: Full Automated Verification

- [x] **Step 1: Run all viewer tests**

```bash
node viewer/viewer_regression_test.js
node viewer/viewer.test.js
python3 -m pytest viewer/test_serve.py -q
```

Expected: all pass without uncaught warnings or errors.

- [x] **Step 2: Inspect diff and accidental scope**

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors and only planned files tracked.

## Task 7: Playwright Visual and Interaction Verification

- [x] **Step 1: Verify Storage Monitor development service**

Confirm `http://127.0.0.1:8088` returns HTTP 200. Restart only the Storage development process if required; do not alter GPU Monitor live.

- [x] **Step 2: Capture both themes at acceptance widths**

Capture 1920x1080, 1600x900, 1280x900, 1100x900, 980x900, 760x900, and 390x844. Verify centered maximum width, equal column widths, 3/2/1 breakpoints, server order, no horizontal overflow, and unchanged detail width.

- [x] **Step 3: Verify live resize without reload**

Resize 1600 → 1280 → 980 → 760 → 1600. Assert all cards remain inside the container and inline columns match current column count.

- [x] **Step 4: Verify transition start, midpoint, and completion**

Capture icon center and transition frames near 0ms, 260ms, and 600ms. Verify origin within one CSS pixel and no post-reveal flash or icon pop.

- [x] **Step 5: Verify rapid activation and reduced motion**

Double-activate and ensure one transition. Emulate reduced motion and ensure immediate switching without focus loss.

- [x] **Step 6: Clean artifacts**

Remove temporary `.playwright-cli/` and `output/playwright/` artifacts; do not commit them.

## Task 8: Documentation and Final Commit

**Files:**
- Modify: `docs/superpowers/specs/2026-07-22-storage-overview-responsive-theme-design.md`
- Modify: `docs/superpowers/plans/2026-07-22-storage-overview-responsive-theme.md`

- [x] **Step 1: Mark verified criteria and record commands**

Set design status to Implemented and record exact automated/browser checks.

- [x] **Step 2: Run final verification**

```bash
node viewer/viewer_regression_test.js
node viewer/viewer.test.js
python3 -m pytest viewer/test_serve.py -q
git diff --check
```

Expected: all pass.

- [x] **Step 3: Commit documentation**

```bash
git add docs/superpowers/specs/2026-07-22-storage-overview-responsive-theme-design.md docs/superpowers/plans/2026-07-22-storage-overview-responsive-theme.md
git commit -m "docs: record storage overview polish"
```

- [x] **Step 4: Report evidence**

Report changed files, simplifications, test output, responsive widths checked, transition evidence, commit hashes, and remaining browser limitations.


## Execution Result

Completed in the current isolated worktree using test-driven subagent execution with separate specification and code-quality reviews for layout, theme transition, and responsive media-label correction. Final comprehensive review and branch verification are recorded in the session evidence.
