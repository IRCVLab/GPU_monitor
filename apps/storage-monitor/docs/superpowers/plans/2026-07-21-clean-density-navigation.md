# Clean Density and Cross-Tool Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Storage Viz with GPU Monitor's Clean material, make the fully expanded storage overview substantially denser, and add same-tab navigation in both directions.

**Architecture:** Storage Viz remains a standalone vanilla HTML/CSS/JavaScript application and copies GPU Monitor's semantic Clean tokens without importing code at runtime. GPU Monitor receives only a header/menu link; no data or service integration is introduced. Existing routes, server order, mount order, APIs, and deployment units remain unchanged.

**Tech Stack:** Vanilla JavaScript/CSS/Python HTTP server, Svelte 5/SvelteKit GPU Monitor frontend, Node contract tests, Python unittest, Playwright CLI, systemd, SSH deployment.

## Global Constraints

- Keep every actionable mount visible in the Storage Viz overview.
- Preserve configured server order and snapshot mount order.
- Storage Viz uses Clean material only; no material picker or new dependency.
- Share light/dark preference through the `themeMode` cookie.
- Same-tab navigation defaults to `http://127.0.0.1:5173/` and `http://127.0.0.1:8088/`.
- Storage deployment restarts only `storage-viz-dashboard.service`.
- GPU Monitor deployment must not change backend processes or Storage Viz.
- No horizontal overflow at desktop or mobile widths.
- Use regression-first TDD and commit each independently reviewable task.

---

### Task 1: Storage Clean theme bootstrap and suite navigation

**Files:**
- Modify: `viewer/index.html`
- Modify: `viewer/app.js`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`

**Interfaces:**
- Consumes: current Storage Viz shell, GPU Monitor `themeMode` cookie contract.
- Produces: `applyStoredThemeMode()`, `toggleThemeMode()`, `.suite-nav-link`, and `.theme-mode-button` used by the final shell.

- [ ] **Step 1: Add failing shell and theme contract tests**

Assert that `index.html` includes a same-tab `GPU Monitor` link, an accessible theme button, and an early inline script that applies `html.light` or `html.dark` from the `themeMode` cookie before CSS paints. Assert that the old subtitle is absent.

- [ ] **Step 2: Run tests and confirm red**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: failure because the suite link and shared theme bootstrap do not exist.

- [ ] **Step 3: Implement the shell contract**

Add an early inline bootstrap equivalent to:

```js
(() => {
  const saved = document.cookie.split('; ').find((part) => part.startsWith('themeMode='))?.split('=')[1];
  const mode = saved === 'light' || saved === 'dark'
    ? saved
    : (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.classList.add(mode);
  document.documentElement.dataset.material = 'liquid';
})();
```

Add `GPU Monitor` as a normal anchor to `http://127.0.0.1:5173/`, a circular mode button, and small inline SVG icons. In `app.js`, toggle `light`/`dark`, write `themeMode=<mode>; Path=/; SameSite=Lax`, update `aria-pressed`, and avoid touching route/history state.

- [ ] **Step 4: Replace Storage tokens with exact Clean semantic tokens**

Map the approved light/dark colors and Clean material values to existing Storage semantic variables. Keep warning, critical, success, focus, and monospace-number semantics intact. Add 160-220ms color/surface transitions and disable them under reduced motion.

- [ ] **Step 5: Run focused tests**

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add viewer/index.html viewer/app.js viewer/styles.css viewer/viewer.test.js viewer/viewer_regression_test.js
git commit -m "feat: align storage shell with clean monitor theme"
```

---

### Task 2: Fully expanded high-density storage strips

**Files:**
- Modify: `viewer/overview.js`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`

**Interfaces:**
- Consumes: `createOverviewRowElement()` and existing overview mount summaries.
- Produces: the same ordered rows and links using a continuous `.overview-mounts` strip without nested-card depth.

- [ ] **Step 1: Add failing density and hierarchy tests**

Assert that the overview keeps all mounts, contains no healthy status copy, uses one server warning channel, and emits the path/media/percent/bar/free fields in snapshot order. Add CSS contract assertions for a compact server column, connected mount cells, responsive 2/3-column fill, and a mobile no-overflow override.

- [ ] **Step 2: Run tests and confirm red**

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: failure on the new connected-strip CSS and simplified hierarchy contract.

- [ ] **Step 3: Simplify overview rendering**

Keep the current `<button class="overview-row">` navigation boundary. Render only:

```text
server name + mount count + highest exceptional state | ordered mount strip
```

Do not render healthy badges, page-level server count, aggregate capacity, IP metadata, or duplicate pressure text. Preserve warning/critical text for accessibility.

- [ ] **Step 4: Implement connected high-density layout**

Use a 132-148px server column and flexible strip. Remove per-mount shadows and independent card depth. Use shared surface plus separators, 4-6px internal gaps, path/percent as primary text, and warning/critical color only on the pressure bar, percentage, and exceptional copy.

For detail mode, compress the identity/scan metadata/capacity/tab spacing and apply the same segmented-control and connected-strip language without changing treemap or table behavior.

- [ ] **Step 5: Run viewer verification**

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
python3 -m unittest viewer.test_serve
find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check
git diff --check
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add viewer/overview.js viewer/styles.css viewer/viewer.test.js viewer/viewer_regression_test.js
git commit -m "feat: tighten storage overview into continuous strips"
```

---

### Task 3: GPU Monitor reciprocal Storage navigation

**Files:**
- Modify on dev: `/home/ircv/workspace/monitoring_v2_dev/frontend/src/routes/+page.svelte`
- Create on dev: `/home/ircv/workspace/monitoring_v2_dev/frontend/src/routes/storage-link.contract.test.mjs`
- Modify on live: `/home/ircv/workspace/monitoring_v2/frontend/src/routes/+page.svelte`
- Create on live: `/home/ircv/workspace/monitoring_v2/frontend/src/routes/storage-link.contract.test.mjs`

**Interfaces:**
- Consumes: existing `.ops-utility-action` or `.ops-menu-link` header controls.
- Produces: a same-tab `Storage` anchor to `http://127.0.0.1:8088/` in both live and development GPU Monitor frontends.

- [ ] **Step 1: Verify both GPU repositories are clean and commit any pre-existing work**

```bash
git -C /home/ircv/workspace/monitoring_v2 status --short
git -C /home/ircv/workspace/monitoring_v2_dev status --short
```

Expected: no output before editing.

- [ ] **Step 2: Add failing navigation contract tests in each repository**

Create a dependency-free Node contract test in each repository that reads `+page.svelte` and asserts that the dashboard page contains an anchor whose text is `Storage`, whose `href` is `http://127.0.0.1:8088/`, and which does not set `target="_blank"`.

- [ ] **Step 3: Run the focused tests and confirm red**

```bash
node /home/ircv/workspace/monitoring_v2_dev/frontend/src/routes/storage-link.contract.test.mjs
node /home/ircv/workspace/monitoring_v2/frontend/src/routes/storage-link.contract.test.mjs
```

Expected: failure because the Storage link is absent.

- [ ] **Step 4: Add the visible Storage utility link**

Place the link in the primary header action area or first-level management menu using the existing Clean control class. Do not change stores, WebSocket behavior, server filtering, shortcuts, or backend routes.

- [ ] **Step 5: Run focused tests and production builds in each repository**

```bash
cd /home/ircv/workspace/monitoring_v2_dev/frontend && node src/routes/storage-link.contract.test.mjs && npm run check && npm run build
cd /home/ircv/workspace/monitoring_v2/frontend && node src/routes/storage-link.contract.test.mjs && npm run check && npm run build
```

Expected: all pass.

- [ ] **Step 6: Commit independently in each repository**

```bash
git add frontend/src/routes/+page.svelte frontend/src/routes/storage-link.contract.test.mjs
git commit -m "feat: link gpu monitor to storage dashboard"
```

---

### Task 4: Review, deploy, and visual verification

**Files:**
- Deployment target: `/opt/storage-viz-dashboard/viewer`
- Service: `storage-viz-dashboard.service`
- Runtime frontends: GPU Monitor live port 5173, dev port 5174

**Interfaces:**
- Consumes: committed Tasks 1-3.
- Produces: live Clean-aligned Storage Viz and reciprocal navigation with evidence.

- [ ] **Step 1: Run complete Storage verification**

```bash
python3 -m unittest discover -s agent -p 'test_*.py'
python3 -m unittest discover -s collector -p 'test_*.py'
python3 -m unittest viewer.test_serve
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
STORAGE_VIZ_LINUX_HOST=ircv@166.104.167.11 STORAGE_VIZ_LINUX_PORT=2200 bash deploy/verify-linux.sh --remote
git diff --check
```

- [ ] **Step 2: Request an independent code/design review**

Review both repositories for hierarchy, accessibility, route safety, persistence, responsive overflow, service isolation, and missing tests. Fix all blocking findings through one bounded TDD wave.

- [ ] **Step 3: Deploy Storage static files only**

Back up `/opt/storage-viz-dashboard/viewer`, copy the committed viewer directory, restart only `storage-viz-dashboard.service`, and confirm `http://127.0.0.1:8088/` returns 200.

- [ ] **Step 4: Deploy GPU frontend builds without restarting backend services**

Use the existing live/dev frontend process workflow. Confirm ports 5173 and 5174 serve the new link while backend PIDs on 8001 and 8101 remain unchanged.

- [ ] **Step 5: Run Playwright desktop/mobile QA**

Verify:

- ordered seven-server overview;
- every actionable mount remains visible;
- compact connected-strip hierarchy;
- light/dark persistence and shared cookie;
- Storage → GPU Monitor and GPU Monitor → Storage links;
- no horizontal overflow;
- no browser console errors.

- [ ] **Step 6: Clean verification artifacts and report evidence**

Close Playwright sessions, remove generated output, verify each repository is clean, and report commits, test counts, services, ports, remaining long-running scans, and any compatibility limitation.
