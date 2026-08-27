# Storage Capacity and Navigation Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and keep viewer/scanner write scopes disjoint.

**Goal:** Make Storage Monitor distinguish physical-disk capacity from scanned directory size, while retaining one useful navigation level below `/data` without following symlinks.

**Architecture:** Capacity is grouped by `capacity_id`; scan roots sharing one physical filesystem are presented beneath a single capacity summary. Treemap/detail views continue to represent directory data and explicitly label scanned bytes. The scanner keeps immediate children and one additional directory level regardless of prune threshold, then resumes threshold pruning below that level.

**Tech Stack:** Vanilla JavaScript viewer, C scanner, Python scan runner, Node and shell regression tests.

**Spec:** Approved 2026-08-27 conversation design: physical capacity by disk, navigation by directory.

## Global Constraints

- Do not follow directory symlinks or include their targets in scanned-byte accounting.
- Preserve existing server and scan-root ordering.
- Keep existing scan interval and collection cost; only retained snapshot nodes may increase.
- Bound retained children using the existing per-directory node cap.
- Test-first: every behavior change must be observed failing before implementation.

---

### Task 1: Group capacity while retaining scan-root navigation

**Files:**
- Modify: `apps/storage-monitor/viewer/overview.js`
- Modify: `apps/storage-monitor/viewer/viewer_regression_test.js`
- Modify only if presentation requires it: `apps/storage-monitor/viewer/styles.css`

- [ ] Add a failing regression test proving `/home` and `/data` with the same `capacity_id` render one physical capacity summary and two scan paths.
- [ ] Add a failing regression test proving `/data` detail labels `scanned_bytes` instead of presenting shared `df_used` as directory usage.
- [ ] Run the focused Node regression test and confirm both fail for missing behavior.
- [ ] Implement grouping by first-seen `capacity_id`, preserving first-seen mount/path order.
- [ ] Render scanned-directory bytes and shared-capacity context with semantic labels.
- [ ] Run viewer tests and keep existing mount-selection behavior intact.

### Task 2: Preserve one additional directory navigation level

**Files:**
- Modify: `apps/storage-monitor/scanner/hstscan.c`
- Modify: `apps/storage-monitor/scanner/test_hstscan.sh`

- [ ] Add a failing scanner test with `/data/user/project/tiny` where `user` and `project` remain but `tiny` is pruned below the configured threshold.
- [ ] Confirm the scanner test fails because `project` is currently folded into `other_bytes`.
- [ ] Implement depth-aware pruning that preserves two directory levels below a scan root and resumes threshold pruning afterward.
- [ ] Keep symlink no-follow behavior and existing child-count caps unchanged.
- [ ] Run scanner tests and confirm snapshot size remains bounded.

### Task 3: Integrate, verify, and deploy

**Files:**
- Update: plan checkboxes as execution evidence if useful.

- [ ] Run focused viewer and scanner tests.
- [ ] Run `make test-storage`.
- [ ] Run repository verification relevant to Storage Monitor.
- [ ] Review the complete diff for capacity double-counting, ordering regression, and scanner cost.
- [ ] Commit and push to `main` through the existing deployment workflow.
- [ ] Verify Storage Live on port `505` shows grouped physical capacity.
- [ ] Deploy the scanner change through the existing agent deployment path, trigger a neo rescan, and verify `/data/minjae/resworld_navsim` is navigable.
