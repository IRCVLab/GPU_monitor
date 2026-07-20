# Compact Storage Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exclude boot filesystems from collection and replace the storage dashboard's aggregate/card-heavy presentation with compact mount-centric rows.

**Architecture:** Collection policy rejects `/boot` and descendants before generic local filesystem selection, while the viewer also filters legacy snapshots for rolling compatibility. The overview removes global and server aggregate prose and renders compact mount strips; detail capacity cards become compact exact-value rows.

**Tech Stack:** Python 3 standard library, C scanner integration, vanilla JavaScript, HTML, CSS, Node-based regression tests, Python unittest, systemd deployment.

## Global Constraints

- Do not add dependencies.
- Preserve configured server order and snapshot mount order.
- Exclude `/boot` and descendants by path, not by filesystem type.
- Preserve eligible non-boot `vfat` and `exfat` data mounts.
- Remove the page-wide total-storage aggregate from the rendered overview.
- Overview mount strips show path, media, percentage, pressure bar, and free capacity; exact used/total remains in detail.
- Preserve keyboard activation, focus-visible, reduced-motion, mobile no-overflow, and text-plus-shape exceptional status cues.
- Do not touch GPU Monitor paths, processes, ports, services, or source trees.

---

### Task 1: Exclude boot filesystems from collection

**Files:**
- Modify: `agent/mount_policy.py`
- Modify: `agent/test_mount_policy.py`

**Interfaces:**
- Produces: mount-policy decisions with `reason == "boot-filesystem"` for `/boot` and descendants.
- Preserves: generic local filesystem selection for non-boot supported data mounts.

- [ ] **Step 1: Write failing policy tests**

Add one test containing `/boot` and `/boot/efi` entries and assert both are skipped with `boot-filesystem`. Add a non-boot `/data/transfer` `vfat` entry and assert it remains selected.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest agent.test_mount_policy -v`

Expected: failure because boot mounts are currently selected by generic local-fs policy.

- [ ] **Step 3: Add the minimal path-specific policy**

Add a helper equivalent to:

```python
def is_boot_filesystem_path(path):
    normalized = normalize_mountpoint(path)
    return normalized == "/boot" or normalized.startswith("/boot/")
```

Evaluate it before generic local-filesystem selection and emit `boot-filesystem`.

- [ ] **Step 4: Run agent tests and verify GREEN**

Run: `python3 -m unittest discover -s agent -p 'test_*.py' -v`

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add agent/mount_policy.py agent/test_mount_policy.py
git commit -m "fix: exclude boot filesystems from storage scans"
```

### Task 2: Replace overview aggregate and nested mount cards

**Files:**
- Modify: `viewer/index.html`
- Modify: `viewer/app.js`
- Modify: `viewer/overview.js`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`

**Interfaces:**
- Consumes: snapshot `mounts[]` and existing `summarizeMounts()` pressure/media fields.
- Produces: `isActionableMountPath(path)` and compact overview mount-strip DOM.

- [ ] **Step 1: Write failing viewer tests**

Add assertions that `/boot` and `/boot/efi` are omitted from summarized/rendered legacy snapshots, non-boot mounts preserve input order, `#overviewAggregate` and `renderOverviewAggregate()` invocation are absent, server rows do not render subtotal/normal labels, and each mount strip renders path, media, percent, bar, and free capacity without `usedTotalText`.

- [ ] **Step 2: Run viewer tests and verify RED**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: failures on aggregate removal, boot filtering, and compact DOM/CSS contracts.

- [ ] **Step 3: Implement legacy-snapshot filtering and compact DOM**

In `summarizeMounts()`, filter using:

```javascript
function isActionableMountPath(path) {
  const value = String(path || "").replace(/\/+$/, "") || "/";
  return value !== "/boot" && !value.startsWith("/boot/");
}
```

Remove the aggregate section from `index.html` and its render call from `app.js`. Keep server name, actionable mount count, and exceptional badges. Render each mount strip in the order path/media/percent/bar/free.

- [ ] **Step 4: Implement dense responsive styles**

Use a narrow server column, 10px or smaller outer padding, 6px or smaller grid gaps, one-line mount strips, a 4px pressure bar, path truncation with `title`, tabular numbers, responsive wrapping without horizontal overflow, and reduced-motion preservation.

- [ ] **Step 5: Run viewer tests and verify GREEN**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
python3 -m unittest viewer.test_serve -v
```

Expected: 0 failures.

- [ ] **Step 6: Commit**

```bash
git add viewer/index.html viewer/app.js viewer/overview.js viewer/styles.css viewer/viewer.test.js viewer/viewer_regression_test.js
git commit -m "feat: compact the storage overview"
```

### Task 3: Compact detail capacity presentation

**Files:**
- Modify: `viewer/data-client.js`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer_regression_test.js`

**Interfaces:**
- Consumes: already filtered `DATA.mounts` and exact `df_used`, `df_total`, `df_avail`, `df_use_pct` values.
- Produces: compact detail capacity rows while preserving mount selectors and analysis tabs.

- [ ] **Step 1: Write failing detail regression tests**

Assert detail capacity uses a compact row/rail class, preserves path/filesystem/media/used-total/percentage/free/bar, omits boot mounts from legacy snapshots, and no longer uses the large capacity-card grid contract.

- [ ] **Step 2: Run regression tests and verify RED**

Run: `node viewer/viewer_regression_test.js`

Expected: failure on missing compact detail rail.

- [ ] **Step 3: Implement compact detail rows**

Filter boot paths before deriving `mountPaths` and rendering `caps`. Render exact values on one aligned row per mount with a thin utilization bar. Keep existing element IDs required by detail logic.

- [ ] **Step 4: Run viewer suites and verify GREEN**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
python3 -m unittest viewer.test_serve -v
```

Expected: 0 failures.

- [ ] **Step 5: Commit**

```bash
git add viewer/data-client.js viewer/styles.css viewer/viewer_regression_test.js
git commit -m "feat: compact detail capacity rows"
```

### Task 4: Deploy and verify all seven servers

**Files:**
- Modify only if tests require: `docs/operations.md`
- Runtime deployment: storage-viz agent and dashboard paths only.

**Interfaces:**
- Consumes: committed Tasks 1-3.
- Produces: seven fresh snapshots without boot mounts and a live compact dashboard at the existing loopback tunnel URL.

- [ ] **Step 1: Run complete code verification**

Run remote Linux verification, agent/collector suites, viewer Python tests, and both JavaScript suites. Expected: all exit 0.

- [ ] **Step 2: Deploy scanner policy to seven agents**

Deploy in configured order using the existing hardened deployment workflow and `shchoi` admin bootstrap only when required. Do not restart or modify GPU Monitor services.

- [ ] **Step 3: Trigger one scan per agent and refresh central collection**

Confirm each scan is low-priority, completes successfully, and the central API exposes no `/boot` mount.

- [ ] **Step 4: Deploy dashboard static files and restart only `storage-viz-dashboard.service`**

Keep bind address `127.0.0.1:8088` and existing `storage-viz-tunnel` tmux session.

- [ ] **Step 5: Verify live behavior and isolation**

Assert all seven servers remain available and ordered, aggregate UI is absent, compact HTML/CSS/JS assets return HTTP 200, timers remain active/waiting, worktree is clean, and GPU Monitor PIDs plus `/health` hashes are unchanged.

- [ ] **Step 6: Commit any required operations documentation**

```bash
git add docs/operations.md
git commit -m "docs: document compact storage collection"
```

