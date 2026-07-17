# Multi-Server Storage Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the current single-host storage visualizer into a GPU-Monitor-independent, overview-first multi-server storage dashboard with low-impact six-hour local scans, central SSH/SFTP collection, authorized per-server rescans, and copy-only cleanup assistance.

**Architecture:** Keep the existing C walker as the byte-accounting engine and add a Python standard-library local agent around it for mount policy, immutable generation snapshots, status records, and retention. Add a separate central collector package that reads a strict JSON-compatible YAML inventory, fetches immutable snapshots through short-lived OpenSSH/SFTP subprocesses, retains previous-good data, and exposes bounded API data through the existing Python HTTP adapter. Keep the frontend dependency-free and progressively replace the single-host picker with a stable ordered server overview that opens the existing treemap workspace.

**Tech Stack:** C11/pthreads scanner, Python 3 standard library, OpenSSH/SFTP CLI, vanilla HTML/CSS/JavaScript, ECharts, systemd, Bash, Python `unittest`, Node assertion tests, Playwright smoke/visual verification.

**Design source:** `docs/superpowers/specs/2026-07-17-multiserver-storage-dashboard-design.md`

**Dependency policy:** Add no package dependency. Files named `.yaml` use a documented strict JSON-compatible YAML subset and are parsed with Python `json`; this keeps deployment dependency-free while remaining valid YAML 1.2.

**Credential rule:** Runtime collection always uses the dedicated `monitoring` account and its constrained SSH identity. If that account lacks the fixed `sudo -n systemctl start storage-viz-scan.service` permission during installation, the deployment helper may use the `shchoi` administrator account to install the unit and sudoers rule. It must never store, echo, log, pass as a command-line argument, or commit an administrator password; password entry remains an interactive SSH/sudo concern. Runtime code must never fall back to administrator credentials.

---

## File Structure

### Local agent and scanner policy

- Create `agent/__init__.py` — package marker.
- Create `agent/mount_policy.py` — pure mountinfo parser, locality classifier, deterministic duplicate selection, and `/home` policy.
- Create `agent/scan_runner.py` — local config loading, scanner invocation, schema enrichment, immutable generation/status writes, digest, retention, and lock handling.
- Create `agent/test_mount_policy.py` — fixture-driven policy tests.
- Create `agent/test_scan_runner.py` — generation/status/retention/failure tests with a fake scanner.
- Modify `scanner/hstscan.c` — add explicit `kind` fields to emitted selectable nodes and file rows; preserve CLI target mode for the agent.
- Modify `scanner/test_hstscan.sh` — assert `kind` and explicit-target compatibility.
- Modify `docs/schema-v1.md` — document additive multi-server fields.
- Modify `data/gen_sample.py` — generate a deterministic enriched sample.
- Modify `data/test_fixtures.py` — validate the enriched sample and stable host order.

### Central collector and API

- Create `collector/__init__.py` — package marker.
- Create `collector/inventory.py` — strict inventory loading and stable order validation.
- Create `collector/snapshot.py` — status tuple and schema validation.
- Create `collector/store.py` — atomic previous-good snapshot/state persistence.
- Create `collector/transport.py` — fixed-argument OpenSSH/SFTP transport with pinned known-hosts configuration.
- Create `collector/jobs.py` — per-server job state, cooldown, concurrency, and bounded audit events.
- Create `collector/service.py` — polling scheduler and manual-rescan orchestration.
- Create `collector/test_inventory.py` — inventory safety/order tests.
- Create `collector/test_snapshot.py` — schema/digest/generation tests.
- Create `collector/test_store.py` — previous-good retention tests.
- Create `collector/test_transport.py` — subprocess argument and failure mapping tests.
- Create `collector/test_jobs.py` — conflict/cooldown/concurrency/audit tests.
- Create `collector/test_service.py` — fake-transport poll and rescan state-machine tests.
- Create `config/servers.example.yaml` — synthetic ordered inventory with no credentials.
- Modify `viewer/serve.py` — thin HTTP/API adapter over collector service, trusted-proxy identity, CSRF, and static assets.
- Replace `viewer/test_serve.py` — API/auth/CSRF/no-AI route tests.

### Viewer

- Create `viewer/overview.js` — ordered server summary rendering and status precedence.
- Modify `viewer/index.html` — overview/detail shells; remove AI UI and scripts.
- Modify `viewer/data-client.js` — central API clients and stable-order normalization without default promotion.
- Modify `viewer/app.js` — overview/detail navigation, server-specific rescan, session/CSRF handling.
- Modify `viewer/treemap.js` — preserve detail drill-down and carry node `kind` into selection metadata.
- Modify `viewer/tables.js` — remove advisor badges and carry row `kind`.
- Modify `viewer/selection.js` — fixed inspection templates, destructive reveal, safe kind/path validation.
- Modify `viewer/styles.css` — dense overview, status states, detail navigation, destructive reveal, responsive behavior.
- Delete `viewer/advisor-client.js`, `viewer/advisor-ui.js`, `viewer/advisor-badges.js`, `viewer/ai_advisor.py`, and `viewer/test_ai_advisor.py`.
- Rewrite `viewer/viewer.test.js` and `viewer/viewer_regression_test.js` — overview, no-AI, navigation, command safety, and stable-order contracts.

### Installation and operations

- Create `config/scanner.example.yaml` — strict local agent config with server id and conservative thread limits.
- Create `deploy/systemd/storage-viz-scan.service.in` — hardened root scanner unit.
- Create `deploy/systemd/storage-viz-scan.timer` — six-hour persistent timer with random delay.
- Create `deploy/systemd/storage-viz-dashboard.service.in` — separate central service.
- Create `deploy/sudoers/storage-viz-monitoring` — fixed service-start permission only.
- Create `deploy/install-agent.sh` — remote-host agent installer with dry-run.
- Create `deploy/deploy-agent.sh` — local deployment helper using `monitoring`, with interactive `shchoi` bootstrap fallback only.
- Create `deploy/test_deploy_scripts.sh` — shell and generated-unit contract tests.
- Create `deploy/verify-linux.sh` — temporary-copy Linux verification runner with trap-based cleanup.
- Modify `install.sh` — central dashboard installer only; no GPU Monitor paths or services.
- Modify `.gitignore` — ignore generated snapshots, state, audit, identities, and local inventory.
- Modify `README.md`, `docs/architecture.md`, `docs/operations.md`, and `docs/host-manifest.md` — new topology and runbooks; remove AI documentation.
- Delete `docs/ai-advisor-schema.md` and `docs/ai-cleanup-advisor.md`.

---

### Task 1: Remove AI Without Changing Storage Behavior

**Files:**
- Delete: `viewer/advisor-client.js`
- Delete: `viewer/advisor-ui.js`
- Delete: `viewer/advisor-badges.js`
- Delete: `viewer/ai_advisor.py`
- Delete: `viewer/test_ai_advisor.py`
- Delete: `docs/ai-advisor-schema.md`
- Delete: `docs/ai-cleanup-advisor.md`
- Modify: `viewer/index.html`
- Modify: `viewer/app.js`
- Modify: `viewer/treemap.js`
- Modify: `viewer/tables.js`
- Modify: `viewer/serve.py`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`
- Modify: `viewer/test_serve.py`

- [ ] **Step 1: Replace AI-positive tests with no-AI contracts**

Add assertions that `/ai/status` and `/ai/recommend` return `404`, `/capabilities` has no `ai` field, HTML has no AI controls/scripts, and treemap/table code has no advisor hooks.

- [ ] **Step 2: Run tests and verify the new contracts fail**

Run:

```bash
python3 viewer/test_serve.py
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: failures identifying existing AI routes, markup, scripts, and hooks.

- [ ] **Step 3: Remove AI runtime, UI, styles, tests, and documentation**

Keep storage loading, treemap, tables, rescan-disabled behavior, and cleanup selection intact. Do not replace AI with another recommendation layer.

- [ ] **Step 4: Run focused and baseline tests**

Run:

```bash
python3 viewer/test_serve.py
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
bash -n install.sh scanner/test_hstscan.sh
```

Expected: all pass; `rg -n 'advisor|/ai/|AI Advisor|STORAGE_VIZ_AI' viewer` returns no matches and both legacy AI documentation files are absent. Broader README/operations cleanup is completed in Task 11.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: remove storage advisor"
```

### Task 2: Lock the Additive Snapshot Schema and Fixtures

**Files:**
- Modify: `docs/schema-v1.md`
- Modify: `data/gen_sample.py`
- Modify: `data/hinton.sample.json`
- Modify: `data/test_fixtures.py`

- [ ] **Step 1: Write failing fixture assertions**

Assert top-level `server_id`, `scan_finished_unix`, `scan_generation`, `selected_roots`; selected-root identity/status fields; mount linkage; and selectable `kind` values.

- [ ] **Step 2: Verify failure**

Run: `python3 data/test_fixtures.py`

Expected: fail on missing additive fields.

- [ ] **Step 3: Extend generator and schema documentation**

Use deterministic synthetic values. Preserve byte invariants and schema major version `1`.

- [ ] **Step 4: Regenerate and verify**

Run:

```bash
python3 data/gen_sample.py
python3 data/test_fixtures.py
node viewer/viewer_regression_test.js
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add docs/schema-v1.md data/gen_sample.py data/hinton.sample.json data/test_fixtures.py
git commit -m "feat: extend storage snapshot schema"
```

### Task 3: Implement Deterministic Mount Policy

**Files:**
- Create: `agent/__init__.py`
- Create: `agent/mount_policy.py`
- Create: `agent/test_mount_policy.py`

- [ ] **Step 1: Write mountinfo fixture tests**

Cover:

```python
def test_root_scans_home_only(): ...
def test_separate_home_is_scanned_once(): ...
def test_plain_ext4_xfs_lvm_and_mdraid_mounts_are_local(): ...
def test_btrfs_subvolumes_with_distinct_roots_remain_distinct(): ...
def test_zfs_datasets_with_distinct_sources_remain_distinct(): ...
def test_nfs_cifs_smb_sshfs_ceph_gluster_lustre_gpfs_and_9p_are_rejected(): ...
def test_netdev_remote_colon_and_unc_sources_are_rejected(): ...
def test_generic_fuse_virtual_overlay_squashfs_aufs_and_loop_sources_are_rejected(): ...
def test_duplicate_identity_uses_lowest_mount_id_then_shortest_then_lexical(): ...
def test_nested_mounts_are_separate_roots(): ...
def test_unsupported_mount_is_reported_not_guessed(): ...
```

The denylist test is table-driven and includes every filesystem family enumerated in the design spec. Expected classification is explicit for every row: `selected`, `duplicate`, `prohibited`, or `unsupported` with a bounded reason code.

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest agent.test_mount_policy -v`

Expected: import/function failures.

- [ ] **Step 3: Implement pure parser and selector**

Expose focused functions:

```python
def parse_mountinfo(text: str) -> list[MountEntry]: ...
def classify_mount(entry: MountEntry) -> Classification: ...
def select_scan_roots(entries: list[MountEntry], home_path="/home") -> SelectionResult: ...
```

Use no live filesystem calls inside selection logic; isolate them in a small adapter so fixtures are deterministic.

- [ ] **Step 4: Run tests**

Run: `python3 -m unittest agent.test_mount_policy -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add agent
git commit -m "feat: select safe local scan roots"
```

### Task 4: Add Local Immutable Scan Runner

**Files:**
- Create: `agent/scan_runner.py`
- Create: `agent/test_scan_runner.py`
- Create: `config/scanner.example.yaml`
- Modify: `scanner/hstscan.c`
- Modify: `scanner/test_hstscan.sh`

- [ ] **Step 1: Write failing runner tests with a fake scanner**

Cover config validation, explicit selected-root arguments, schema enrichment, node/row `kind`, SHA-256/size tuple, immutable filename, atomic status, previous generation retention, config digest, lock conflict, and scanner failure preserving previous-good status. Config tests must reject unknown mount-selection keys such as `targets`, `include_mounts`, `exclude_mounts`, `/`, or any path; version one derives scope only from tested mount policy.

Add explicit partial-state cases:

```python
def test_partial_snapshot_requires_at_least_one_completed_root(): ...
def test_partial_snapshot_keeps_failed_and_skipped_selected_roots(): ...
def test_total_failure_keeps_previous_generation_and_status(): ...
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python3 -m unittest agent.test_scan_runner -v
make -C scanner test
```

Expected: runner tests fail because implementation is absent; existing scanner test still passes.

- [ ] **Step 3: Add `kind` to C output and implement runner**

The runner invokes only an argument array such as:

```python
[scanner, "--threads", str(threads), "--out", raw_path, *selected_scan_roots]
```

Never invoke a shell. Enrich validated raw JSON, write `snapshots/<generation>.json`, fsync file and directory, write status last, and retain at least two successful generations.

- [ ] **Step 4: Run local tests**

Run:

```bash
python3 -m unittest agent.test_mount_policy agent.test_scan_runner -v
make -C scanner test
python3 data/test_fixtures.py
```

Expected: all pass on Linux; on macOS the C build is recorded as a known `SYS_getdents64` platform limitation and run through the existing isolated Linux verification path.

- [ ] **Step 5: Commit**

```bash
git add agent config/scanner.example.yaml scanner
git commit -m "feat: write immutable local scan snapshots"
```

### Task 5: Add Hardened Local Agent Installation and Admin Bootstrap Fallback

**Files:**
- Create: `deploy/systemd/storage-viz-scan.service.in`
- Create: `deploy/systemd/storage-viz-scan.timer`
- Create: `deploy/sudoers/storage-viz-monitoring`
- Create: `deploy/install-agent.sh`
- Create: `deploy/deploy-agent.sh`
- Create: `deploy/test_deploy_scripts.sh`

- [ ] **Step 1: Write failing generated-asset tests**

Assert six-hour cadence, `Persistent=true`, `RandomizedDelaySec=30m`, `Nice=19`, idle I/O, lock, `User=root`, `Group=storage-viz-collector`, `UMask=0027`, protected paths, no network, exact sudoers command, strict host-key options, and no password/`sshpass` handling.

- [ ] **Step 2: Verify failure**

Run: `bash deploy/test_deploy_scripts.sh`

Expected: missing files/failing contracts.

- [ ] **Step 3: Implement dry-run-first installers**

`deploy/deploy-agent.sh` behavior:

1. connect as `monitoring` and check the exact fixed sudo capability;
2. use that account when sufficient;
3. otherwise invoke the same bootstrap through `ADMIN_USER=shchoi` with interactive SSH/sudo;
4. install the constrained `monitoring` runtime rule;
5. re-check runtime access as `monitoring`;
6. never persist or forward the administrator password.

- [ ] **Step 4: Verify scripts and generated units**

Run:

```bash
bash -n deploy/install-agent.sh deploy/deploy-agent.sh deploy/test_deploy_scripts.sh
bash deploy/test_deploy_scripts.sh
deploy/install-agent.sh --dry-run
```

Expected: all pass without changing systemd.

- [ ] **Step 5: Commit**

```bash
git add deploy
git commit -m "feat: install hardened storage scan agent"
```

### Task 6: Implement Inventory, Snapshot Validation, and Previous-Good Store

**Files:**
- Create: `collector/__init__.py`
- Create: `collector/inventory.py`
- Create: `collector/snapshot.py`
- Create: `collector/store.py`
- Create: `collector/test_inventory.py`
- Create: `collector/test_snapshot.py`
- Create: `collector/test_store.py`
- Create: `config/servers.example.yaml`

- [ ] **Step 1: Write failing unit tests**

Assert stable array order, unique safe ids, host/port validation, identity/known-host paths outside web root, no inline secrets, no mount-selection override keys, schema/generation/config/digest/size checks, atomic state, and previous-good retention. Add:

```python
def test_partial_snapshot_is_valid_with_completed_root(): ...
def test_total_failure_does_not_replace_previous_good_snapshot(): ...
def test_store_reloads_snapshot_and_state_after_restart(): ...
```

The inventory contains centralized capacity defaults validated by tests:

```json
{
  "capacity_thresholds": {
    "warning_used_pct": 80,
    "critical_used_pct": 92,
    "warning_free_bytes": 549755813888,
    "critical_free_bytes": 137438953472
  }
}
```

A mount is critical when either critical condition is met, warning when either warning condition is met, otherwise normal. Tests cover percentage-only, free-byte-only, exact boundary, invalid ordering, and critical-over-warning precedence.

- [ ] **Step 2: Verify failure**

Run:

```bash
python3 -m unittest collector.test_inventory collector.test_snapshot collector.test_store -v
```

Expected: import/function failures.

- [ ] **Step 3: Implement minimal standard-library modules**

Reject unknown keys that affect security. Represent state domains independently: snapshot availability, freshness, latest pull, latest scan result, configuration sync, and active job.

- [ ] **Step 4: Run tests**

Run the same unittest command; expected all pass.

- [ ] **Step 5: Commit**

```bash
git add collector config/servers.example.yaml
git commit -m "feat: validate central storage snapshots"
```

### Task 7: Implement Fixed OpenSSH/SFTP Transport and Polling

**Files:**
- Create: `collector/transport.py`
- Create: `collector/service.py`
- Create: `collector/test_transport.py`
- Create: `collector/test_service.py`

- [ ] **Step 1: Write fake-subprocess and fake-transport tests**

Assert `BatchMode=yes`, `StrictHostKeyChecking=yes`, dedicated storage-viz identity for the `monitoring` account, explicit known-hosts file, bounded timeout, validated generation filename, SFTP batch get, no shell, no administrator fallback, unchanged-generation skip, digest race rejection, unreachable mapping, and previous-good retention. Service tests include restart recovery from persisted state, partial snapshot acceptance, failed-root visibility, total-failure retention, and manual-rescan completion followed by immediate fetch.

- [ ] **Step 2: Verify failure**

Run: `python3 -m unittest collector.test_transport collector.test_service -v`

Expected: import/function failures.

- [ ] **Step 3: Implement transport and poll service**

Status is fetched first; the exact immutable generation file is fetched only when new. Use injected clock/transport for deterministic tests.

- [ ] **Step 4: Run collector tests**

Run: `python3 -m unittest discover -s collector -p 'test_*.py' -v`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add collector
git commit -m "feat: collect remote storage snapshots"
```

### Task 8: Add Per-Server Jobs, Authorization, CSRF, and Bounded API

**Files:**
- Create: `collector/jobs.py`
- Create: `collector/test_jobs.py`
- Modify: `collector/service.py`
- Modify: `collector/test_service.py`
- Modify: `viewer/serve.py`
- Replace: `viewer/test_serve.py`

- [ ] **Step 1: Write failing job and HTTP tests**

Cover read-only viewer access, operator allowlist, loopback trusted-proxy requirement, direct-mode rescan disablement, exact-origin validation, SameSite CSRF cookie/header, unknown server, one active job/server, 15-minute cooldown, global concurrency, bounded audit fields, fixed remote service command, and AI routes returning `404`.

- [ ] **Step 2: Verify failure**

Run:

```bash
python3 -m unittest collector.test_jobs collector.test_service viewer.test_serve -v
```

Expected: failures for absent job/API controls.

- [ ] **Step 3: Implement the bounded API**

Routes:

```text
GET  /api/session
GET  /api/servers
GET  /api/servers/<id>/snapshot
GET  /api/servers/<id>/job
POST /api/servers/<id>/rescan
```

No route accepts a shell command or filesystem path. The POST starts only `storage-viz-scan.service` through `monitoring`.

For local browser development only, `STORAGE_VIZ_DEV_SAMPLE_DIR` may seed read-only synthetic server summaries/snapshots from tracked `*.sample.json` files. This mode is explicit, cannot trigger remote collection or rescan, and is rejected when trusted-proxy production mode is enabled.

- [ ] **Step 4: Run backend tests**

Run:

```bash
python3 -m unittest discover -s collector -p 'test_*.py' -v
python3 viewer/test_serve.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add collector viewer/serve.py viewer/test_serve.py
git commit -m "feat: expose bounded storage dashboard api"
```

### Task 9: Build the Dense Overview-First Viewer

**Files:**
- Create: `viewer/overview.js`
- Modify: `viewer/index.html`
- Modify: `viewer/data-client.js`
- Modify: `viewer/app.js`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`

- [ ] **Step 1: Write failing rendering and source-contract tests**

Assert inventory order is unchanged, all servers render in one dense list, mount bars use percentage plus available bytes, configured percentage/remaining-byte thresholds produce normal/warning/critical pressure, normal freshness is quiet, exceptional state has text and shape, click/Enter opens detail, Back returns overview, and no host is reordered by pressure or status.

Primary exceptional-state precedence is tested and documented as:

1. snapshot absent or agent not installed;
2. latest pull unreachable or invalid;
3. latest scan failed;
4. configuration drift;
5. partial scan;
6. stale retained snapshot;
7. active scan as a secondary progress cue;
8. capacity pressure.

Capacity bars remain visible even when a higher-priority operational state supplies the row's primary label.

- [ ] **Step 2: Verify failure**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: failures for absent overview/navigation.

- [ ] **Step 3: Implement overview and navigation**

Use one logical `h1`, keyboard-accessible server rows, stable configured order, compact bars, and query/hash state that works on refresh without a framework. Do not add card-grid decoration that reduces information density.

- [ ] **Step 4: Run frontend tests**

Run the same Node commands; expected all pass.

- [ ] **Step 5: Commit**

```bash
git add viewer
git commit -m "feat: add multiserver storage overview"
```

### Task 10: Preserve Detail Workspace and Replace Cleanup Workflow

**Files:**
- Modify: `viewer/treemap.js`
- Modify: `viewer/tables.js`
- Modify: `viewer/selection.js`
- Modify: `viewer/index.html`
- Modify: `viewer/styles.css`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`

- [ ] **Step 1: Write failing selection safety tests**

Cover `kind` required, symlink/other/unknown rejection, root/mount/scan-root/control-character rejection, shell quoting, leading dash, glob, whitespace, quotes, Unicode, inspection-first ordering, stale snapshot warning, explicit destructive reveal, `rm -i` for file, `rm -ri --one-file-system` for directory, no `-f`, and no browser execution path.

- [ ] **Step 2: Verify failure**

Run: `node viewer/viewer.test.js`

Expected: current `sudo rm -rf` behavior fails the new contracts.

- [ ] **Step 3: Implement fixed templates and detail integration**

Preserve treemap accuracy, users, top files, stale files, blocked paths, and per-server rescan. The browser only copies commands.

- [ ] **Step 4: Run frontend tests**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
python3 viewer/test_serve.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add viewer
git commit -m "feat: add safe storage cleanup workflow"
```

### Task 11: Central Service Installation and Operations Documentation

**Files:**
- Create: `deploy/systemd/storage-viz-dashboard.service.in`
- Modify: `install.sh`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/operations.md`
- Modify: `docs/host-manifest.md`
- Modify: `deploy/test_deploy_scripts.sh`

- [ ] **Step 1: Add failing deployment/documentation contracts**

Assert central service binds loopback by default, uses separate paths/port/service names, requires external identity/known-host paths, has no GPU Monitor references, documents reverse-proxy auth, records the `monitoring`/interactive `shchoi` bootstrap rule, and never documents a password value.

- [ ] **Step 2: Verify failure**

Run:

```bash
bash deploy/test_deploy_scripts.sh
bash -n install.sh
```

Expected: missing central unit/current single-server docs fail contracts.

- [ ] **Step 3: Implement central installer and rewrite operations docs**

`install.sh --dry-run` must not call `systemctl`, connect to a host, change GPU Monitor, or start scans.

- [ ] **Step 4: Verify install assets**

Run:

```bash
bash deploy/test_deploy_scripts.sh
./install.sh --dry-run
git grep -nEi '(GPU[ _-]?Monitor|gpu[_-]?monitor|monitoring_v2|166\.104\.167\.11|/home/ircv/workspace/monitoring)' \
  -- agent collector config deploy scanner viewer install.sh
```

Expected: tests pass and grep returns no product/runtime/config coupling. Generic `monitoring` account references are allowed only when paired with the separate storage-viz identity and constrained sudo rule.

- [ ] **Step 5: Commit**

```bash
git add .gitignore README.md docs install.sh deploy
git commit -m "docs: add multiserver storage operations"
```

### Task 12: Full Verification, Browser QA, and Isolated Linux Pilot

**Files:**
- Modify only when a verification failure requires a targeted fix.

- [ ] **Step 1: Run all local automated tests**

Run:

```bash
python3 data/test_fixtures.py
python3 -m unittest discover -s agent -p 'test_*.py' -v
python3 -m unittest discover -s collector -p 'test_*.py' -v
python3 viewer/test_serve.py
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
bash deploy/test_deploy_scripts.sh
bash -n install.sh scanner/test_hstscan.sh deploy/*.sh
git diff --check
```

Expected: zero failures.

- [ ] **Step 2: Verify scanner on isolated Linux**

Run the repository-owned temporary verification wrapper. It creates a unique `/tmp/storage-viz-verify.*` directory, streams only tracked files, runs commands there, and removes it through a trap. It must reject a remote working directory under `/home/ircv/workspace/monitoring*`.

When already on Linux:

```bash
bash deploy/verify-linux.sh --local
```

From the current macOS development host, using the existing isolated build host only as a temporary executor:

```bash
STORAGE_VIZ_LINUX_HOST=ircv@166.104.167.11 \
STORAGE_VIZ_LINUX_PORT=2200 \
bash deploy/verify-linux.sh --remote
```

The wrapper runs:

```bash
make -C scanner clean all test
python3 data/test_fixtures.py
python3 -m unittest discover -s agent -p 'test_*.py' -v
python3 -m unittest discover -s collector -p 'test_*.py' -v
bash deploy/test_deploy_scripts.sh
deploy/install-agent.sh --dry-run
```

Expected artifact: `output/verification/linux-verification.txt` containing command names, exit codes, remote temp path, and cleanup confirmation but no credentials or private paths from snapshots.

- [ ] **Step 3: Run browser smoke and visual checks**

Playwright is an external Codex operator tool, not a repository dependency. First verify the wrapper prerequisite and start the local server with synthetic data:

```bash
command -v npx >/dev/null 2>&1
mkdir -p output/playwright
STORAGE_VIZ_INVENTORY="$PWD/config/servers.example.yaml" \
STORAGE_VIZ_STATE_DIR="$PWD/output/playwright/state" \
STORAGE_VIZ_DEV_SAMPLE_DIR="$PWD/data" \
python3 viewer/serve.py 8088 >output/playwright/server.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT
curl -fsS http://127.0.0.1:8088/api/servers >/dev/null
export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
export PWCLI="$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh"
export PLAYWRIGHT_CLI_SESSION=storage-viz
cd output/playwright
"$PWCLI" open http://127.0.0.1:8088 --headed
"$PWCLI" resize 1440 1000
"$PWCLI" snapshot | tee overview-desktop.txt
"$PWCLI" screenshot
```

Use references from each fresh snapshot to execute:

```text
overview -> stable rows -> open server -> switch mount -> treemap drill -> select path -> inspect commands -> reveal destructive commands -> back to overview
```

Then run `"$PWCLI" resize 390 844`, repeat the flow, save `overview-mobile.txt`, screenshots, `"$PWCLI" console`, and `"$PWCLI" network`, and close with `"$PWCLI" close`. Expected artifacts live under `output/playwright/` and include desktop/mobile snapshots, screenshots, console/network logs, and `server.log`. If `npx` is unavailable, do not add a package dependency; record the browser-verification gap and use the in-app browser fallback before completion.

- [ ] **Step 4: Verify GPU Monitor separation before any real deployment**

Capture exact before/after evidence around any Storage Dashboard dry-run or pilot. This is an operator-only verification and is not added to product runtime code. The two health URL variables are filled from the currently deployed, unchanged GPU Monitor service/tmux configuration before the first capture:

```bash
mkdir -p output/verification
test -n "${GPU_MONITOR_LIVE_HEALTH_URL:?set current LIVE health URL}"
test -n "${GPU_MONITOR_DEV_HEALTH_URL:?set current DEV health URL}"

capture_gpu_monitor_state() {
  local output=$1
  ssh -p 2200 ircv@166.104.167.11 \
    "GPU_MONITOR_LIVE_HEALTH_URL='$GPU_MONITOR_LIVE_HEALTH_URL' \
     GPU_MONITOR_DEV_HEALTH_URL='$GPU_MONITOR_DEV_HEALTH_URL' bash -s" \
    > "$output" <<'REMOTE'
set -eu
printf "== repos ==\n"
git -C /home/ircv/workspace/monitoring_v2 status --short --branch
git -C /home/ircv/workspace/monitoring_v2_dev status --short --branch
printf "== units ==\n"
systemctl list-units --all --no-legend | grep -Ei "gpu|monitor" || true
printf "== processes ==\n"
pgrep -af "GPU.?Monitor|gpu.?monitor|monitoring_v2" || true
printf "== ports ==\n"
ss -lntp
printf "== live-health ==\n"
curl -fsS "$GPU_MONITOR_LIVE_HEALTH_URL" | sha256sum
printf "== dev-health ==\n"
curl -fsS "$GPU_MONITOR_DEV_HEALTH_URL" | sha256sum
REMOTE
}

capture_gpu_monitor_state output/verification/gpu-monitor-before.txt

# Run only the isolated Storage Dashboard dry-run/pilot here.

capture_gpu_monitor_state output/verification/gpu-monitor-after.txt
diff -u output/verification/gpu-monitor-before.txt \
  output/verification/gpu-monitor-after.txt \
  > output/verification/gpu-monitor.diff || true
```

Review the diff. Repository status, unit definitions/state, listening endpoints, process command lines, and health-body digests must be unchanged; PID-only churn requires an independently documented unrelated cause. No Storage Dashboard command may modify or restart GPU Monitor.

- [ ] **Step 5: Verify deployment credential behavior**

On a designated test host only:

1. try the dedicated `monitoring` account;
2. if its fixed sudo rule is absent, bootstrap interactively with `ADMIN_USER=shchoi`;
3. verify runtime collection/rescan works solely as `monitoring` afterward;
4. confirm no password appears in files, Git diff, process arguments, or logs.

- [ ] **Step 6: Commit verification fixes, if any**

```bash
git add <targeted-files-only>
git commit -m "fix: harden multiserver storage rollout"
```

- [ ] **Step 7: Request final code review**

Dispatch a code-reviewer and verifier against the design, plan, diff, and fresh test evidence. Resolve all critical/high findings before deployment.

---

## Stop Conditions

- Stop local implementation only when all task tests pass and the frontend works with synthetic multi-server fixtures.
- Do not deploy to real monitored servers until an inventory of host ids/addresses/ports is provided or safely discovered from an approved source.
- Do not use the `shchoi` administrator account at runtime.
- Do not write administrator credentials into this repository, configuration files, shell history, command arguments, environment files, logs, or audit records.
- Do not modify or restart GPU Monitor LIVE or DEV services at any stage.
