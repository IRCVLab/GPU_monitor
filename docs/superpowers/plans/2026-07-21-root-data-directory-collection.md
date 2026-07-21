# Root `/data` Directory Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect an ordinary root-filesystem `/data` directory alongside `/home` without scanning `/`, crossing filesystem boundaries, or double-counting physical capacity.

**Architecture:** `scan_runner` performs the live `lstat` probe and passes verified synthetic paths into pure mount selection. `mount_policy` canonicalizes mount identities before expanding the root source into multiple logical roots. The C scanner receives every production target with an expected device and revalidates both target and opened-directory devices. Snapshot enrichment assigns a stable synthetic logical mount ID while preserving shared physical identity.

**Tech Stack:** Python 3 standard library and unittest/pytest, C11/pthreads/libc, shell integration tests, existing collector snapshot schema.

---

### Task 1: Pure mount selection supports multiple root-backed scan roots

**Files:**
- Modify: `agent/test_mount_policy.py`
- Modify: `agent/mount_policy.py`

- [ ] Add failing tests proving that supplied `/data` produces `/home` and `/data`, a separate `/home` mount coexists with root-backed `/data`, exact `/data1` and `/dataset` are not synthesized, and a root alias remains a duplicate.
- [ ] Run `python3 -m pytest -q agent/test_mount_policy.py` and confirm the new tests fail because `select_scan_roots` cannot emit multiple root-backed paths.
- [ ] Refactor selection to choose one canonical source per mount identity before expanding the canonical `/` source into `/home` plus verified root-directory paths.
- [ ] Keep explicit `/data` mounts authoritative and preserve prohibited-mount skipped records.
- [ ] Run `python3 -m pytest -q agent/test_mount_policy.py` and confirm all tests pass.

### Task 2: Live `/data` probe is bounded and symlink-safe

**Files:**
- Modify: `agent/test_scan_runner.py`
- Modify: `agent/scan_runner.py`

- [ ] Add failing tests for ordinary same-device `/data`, missing path, non-directory, same-device symlink, different device, exact explicit mount, prohibited explicit mount, and `lstat` failure.
- [ ] Run the focused probe tests and confirm they fail because no bounded root-directory probe exists.
- [ ] Implement an injectable `lstat`-based probe that only returns exact `/data` when it is a real directory on the root device and no exact mountinfo entry owns that path.
- [ ] Pass the verified path list into `mount_policy.select_scan_roots` from `run_once`.
- [ ] Run `python3 -m pytest -q agent/test_scan_runner.py` and confirm the focused and existing tests pass.

### Task 3: Guard every scanner target and opened directory by device

**Files:**
- Modify: `scanner/test_hstscan.sh`
- Modify: `scanner/hstscan.c`
- Modify: `agent/test_scan_runner.py`
- Modify: `agent/scan_runner.py`

- [ ] Add failing scanner tests for repeated `--target PATH MAJOR:MINOR`, malformed/missing device values, target mismatch skipping, positional-path backward compatibility, and source ordering that requires `fstat` before directory reads.
- [ ] Add a failing agent argv test requiring guarded target triplets for every selected root.
- [ ] Run `bash scanner/test_hstscan.sh` and the focused agent argv test; confirm expected failures.
- [ ] Implement strict guarded target parsing while retaining positional target support.
- [ ] Compare target `lstat().st_dev` with expected major/minor before traversal.
- [ ] `fstat` every opened directory fd and skip it if the device differs from the target root device.
- [ ] Change `_scanner_argv` to accept selected roots and emit repeated guarded target triplets.
- [ ] Run scanner and agent tests and confirm all pass.

### Task 4: Preserve logical uniqueness and physical capacity deduplication

**Files:**
- Modify: `agent/test_scan_runner.py`
- Modify: `agent/scan_runner.py`
- Modify: `collector/test_snapshot.py`

- [ ] Add failing enrichment tests requiring `/home` to retain the source mount ID, `/data` to use `<id>-root-data`, and both records/mounts to share `major_minor` and `capacity_id`.
- [ ] Add a collector validation test accepting two unique logical roots with one shared capacity while retaining duplicate logical-ID rejection.
- [ ] Run focused tests and confirm enrichment currently collides on `mount_id`.
- [ ] Add one stable logical-ID helper and use it for selected-root and linked-mount records.
- [ ] Run `python3 -m pytest -q agent/test_scan_runner.py collector/test_snapshot.py` and confirm all tests pass.

### Task 5: Pin hardlink allocation and document policy

**Files:**
- Modify: `scanner/test_hstscan.sh`
- Modify: `docs/operations.md`

- [ ] Add a scanner integration test with a cross-target hardlink proving process-global physical-byte deduplication and deterministic first-target allocation.
- [ ] Run `bash scanner/test_hstscan.sh` and confirm current behavior satisfies the documented allocation contract.
- [ ] Document root `/data` synthesis, exact-path scope, shared capacity identity, prohibited mount behavior, and first-target hardlink allocation.

### Task 6: Full verification and review

**Files:**
- Verify all modified files.

- [ ] Run `python3 -m pytest -q`.
- [ ] Run `bash scanner/test_hstscan.sh`.
- [ ] Run `bash deploy/test_deploy_scripts.sh`.
- [ ] Run `node viewer/viewer.test.js` and `node viewer/viewer_regression_test.js`.
- [ ] Run repository static checks documented by existing scripts.
- [ ] Dispatch a read-only code reviewer for mount safety, device races, snapshot compatibility, and test adequacy; fix any real findings test-first.
- [ ] Commit the implementation with a bounded message.

### Task 7: Deploy agents and verify live collection

**Files:**
- Runtime: `/opt/storage-viz/agent/` on each configured storage server.
- Inventory: `/etc/storage-viz/servers.json` on the central host.

- [ ] Read enabled inventory entries and perform a dry-run deployment check per server.
- [ ] Deploy the agent package per server through `deploy/deploy-agent.sh`; do not alter central GPU Monitor services.
- [ ] Trigger one bounded scan per successfully deployed server through the fixed systemd command or central rescan endpoint, respecting active-job/cooldown guards.
- [ ] Wait for completion, pull snapshots, and verify ordinary root-backed `/data` appears with a unique logical mount ID and shared root capacity.
- [ ] Verify explicit local `/data` mounts remain unchanged and prohibited/network `/data` mounts remain excluded.
- [ ] Confirm Storage Monitor, GPU Dev, and GPU Live endpoints remain HTTP 200.
