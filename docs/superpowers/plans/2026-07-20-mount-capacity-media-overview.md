# Mount-Centric Capacity and Media Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show four clearly labeled development sample servers and a compact mount-centric capacity overview with exact/partial aggregate capacity and trustworthy SSD/HDD/Mixed/Unknown classification.

**Architecture:** Add one bounded sysfs-only block-media resolver to the per-server Python agent, then enrich schema-v1 snapshots with optional capacity/media metadata. Keep aggregation and rendering in `viewer/overview.js`, preserve manifest order through the API/bootstrap path, and treat unresolved identity/media as explicit partial/unknown data rather than guessing.

**Tech Stack:** Python 3 standard library, Linux sysfs, existing JSON schema-v1 validator, vanilla JavaScript, CSS, `unittest`, Node assertion tests, Playwright CLI for final browser QA.

---

## File Structure

- Create `agent/block_media.py` — pure, injectable, bounded sysfs resolver and canonical capacity-id helper.
- Create `agent/test_block_media.py` — focused resolver tests using temporary fake sysfs trees.
- Modify `agent/scan_runner.py` — resolve metadata once per selected major/minor and copy it to linked roots/mounts.
- Modify `agent/test_scan_runner.py` — integration and no-failure-on-unresolved coverage.
- Modify `collector/snapshot.py` — optional schema-v1 validation for capacity/media fields and root/mount equality.
- Modify `collector/test_snapshot.py` — compatibility and adversarial validation tests.
- Modify `data/gen_sample.py` — deterministic four-server fixture generation; no hand-edited sample JSON.
- Modify `data/hosts.json` — authoritative stable sample order.
- Modify `data/hinton.sample.json` — generated deterministic fixture; never hand-edit.
- Create `data/atlas.sample.json`, `data/orion.sample.json`, `data/zeus.sample.json` — generated deterministic fixtures.
- Modify `data/test_fixtures.py` — all generated fixtures and metadata validated.
- Modify `viewer/serve.py` — manifest-order development service and explicit `data_mode` API signal.
- Modify `viewer/test_serve.py` — API order/mode/isolation tests.
- Modify `viewer/data-client.js` — preserve `/api/servers` envelope metadata and static sample mode.
- Modify `viewer/app.js` — carry mode/aggregate state to the renderer and render the sample marker.
- Modify `viewer/overview.js` — mount summaries, identity-aware aggregation, partial semantics, and mount-centric DOM.
- Modify `viewer/index.html` — aggregate summary and sample marker containers.
- Modify `viewer/styles.css` — compact approved layout and responsive behavior.
- Modify `viewer/viewer.test.js` — pure model/API helper tests.
- Modify `viewer/viewer_regression_test.js` — DOM, order, copy, accessibility, and CSS regression tests.
- Modify `docs/schema-v1.md` — additive field contract.
- Modify `docs/operations.md` — explain managed filesystem capacity and media Unknown semantics.

### Task 1: Bounded block-media resolver

**Files:**
- Create: `agent/block_media.py`
- Create: `agent/test_block_media.py`

- [ ] **Step 1: Write failing canonical-id and SSD/HDD tests**

Create fake `/sys/dev/block` and `/sys/class/block` trees in a temporary directory. Assert:

```python
self.assertEqual(capacity_id("8:1"), "dev-8-1")
self.assertIsNone(capacity_id("0:0"))
self.assertEqual(resolver.resolve("259:1").media, "ssd")
self.assertEqual(resolver.resolve("8:17").media, "hdd")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python3 -m unittest agent.test_block_media -v`  
Expected: FAIL because `agent.block_media` does not exist.

- [ ] **Step 3: Implement canonical capacity identity and bounded resolver skeleton**

Implement:

```python
@dataclass(frozen=True)
class MediaResult:
    capacity_id: Optional[str]
    media: str
    confidence: str

def capacity_id(major_minor: str) -> Optional[str]:
    match = re.fullmatch(r"(0|[1-9][0-9]{0,9}):(0|[1-9][0-9]{0,9})", major_minor)
    if not match or match.group(1) == match.group(2) == "0":
        return None
    return f"dev-{match.group(1)}-{match.group(2)}"
```

`BlockMediaResolver(sysfs_root=Path("/sys"), max_depth=16, max_nodes=256)` must cache by major/minor and return Unknown/Unresolved instead of raising for discovery failures.

- [ ] **Step 4: Add partition-parent, dm/md, mixed, cycle, and cache tests**

Cover a partition whose `queue/rotational` exists only on its parent disk, device-mapper slaves, mdraid slaves, mixed rotational values, missing nodes, unreadable files, cycles, depth/node limits, and repeated lookup reading each node once.

- [ ] **Step 5: Implement recursive resolution**

Resolve `/sys/dev/block/<major>:<minor>`, stay beneath `/sys/class/block`, follow `slaves`, and ascend from partition to whole disk when required. Use only `Path` reads; never invoke subprocesses.

- [ ] **Step 6: Run focused tests**

Run: `python3 -m unittest agent.test_block_media -v`  
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add agent/block_media.py agent/test_block_media.py
git commit -m "feat: classify local block storage media"
```

### Task 2: Snapshot enrichment and validation

**Files:**
- Modify: `agent/scan_runner.py:162-260,406-470,508-570`
- Modify: `agent/test_scan_runner.py`
- Modify: `collector/snapshot.py:190-245`
- Modify: `collector/test_snapshot.py`
- Modify: `docs/schema-v1.md`

- [ ] **Step 1: Write failing scan-runner integration tests**

Inject a fake resolver into `run_once(..., media_resolver=resolver)` and assert every complete/partial root and linked mount receives identical optional fields:

```python
"capacity_id": "dev-8-1",
"storage_media": "ssd",
"storage_media_confidence": "resolved",
```

Assert Unknown/Unresolved does not fail or suppress the snapshot.

- [ ] **Step 2: Run the focused agent tests and verify failure**

Run: `python3 -m unittest agent.test_scan_runner.ScanRunnerTests.test_capacity_and_media_metadata_links_roots_and_mounts -v`  
Expected: FAIL because `run_once` has no resolver injection and snapshots lack fields.

- [ ] **Step 3: Enrich roots and mounts once per selected device**

Add `media_resolver` injection defaulting to `BlockMediaResolver()`. Build a metadata map keyed by selected-root `major_minor` before `_enrich_payload`. Pass metadata into `_root_record` and copy exactly the same fields to linked mounts. Omit `capacity_id` when unresolved; always emit valid media/confidence enums.

- [ ] **Step 4: Run agent tests**

Run: `python3 -m unittest discover -s agent -p 'test_*.py' -v`  
Expected: all tests PASS.

- [ ] **Step 5: Write failing collector compatibility/adversarial tests**

Test old snapshots without new fields, exact capacity-id regex and max length, absent versus null, invalid media/confidence values, and root/mount mismatch.

- [ ] **Step 6: Implement optional strict validation**

Add helpers equivalent to:

```python
CAPACITY_ID_RE = re.compile(r"^dev-(0|[1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})$")
MEDIA_VALUES = frozenset({"ssd", "hdd", "mixed", "unknown"})
MEDIA_CONFIDENCE_VALUES = frozenset({"resolved", "unresolved"})
```

Reject `dev-0-0`, null fields, mismatched linked metadata, and resolved confidence paired with Unknown.

- [ ] **Step 7: Run collector tests**

Run: `python3 -m unittest discover -s collector -p 'test_*.py' -v`  
Expected: all tests PASS.

- [ ] **Step 8: Document additive fields and commit**

```bash
git add agent/scan_runner.py agent/test_scan_runner.py collector/snapshot.py collector/test_snapshot.py docs/schema-v1.md
git commit -m "feat: publish storage capacity identity and media"
```

### Task 3: Four ordered sample servers and explicit sample mode

**Files:**
- Modify: `data/gen_sample.py`
- Modify: `data/hosts.json`
- Modify: `data/hinton.sample.json`
- Create: `data/atlas.sample.json`
- Create: `data/orion.sample.json`
- Create: `data/zeus.sample.json`
- Modify: `data/test_fixtures.py`
- Modify: `viewer/serve.py:62-110,350-356`
- Modify: `viewer/test_serve.py`

- [ ] **Step 1: Write failing fixture and service tests**

Assert the manifest order is exactly `hinton`, `atlas`, `orion`, `zeus`; every fixture contains media/capacity coverage; `_DevSampleService` follows manifest order rather than filename order; `/api/servers` returns `data_mode: "sample"`; production inventory returns `data_mode: "inventory"`.

- [ ] **Step 2: Run focused tests and verify failure**

Run:

```bash
python3 data/test_fixtures.py
python3 -m unittest viewer.test_serve.ApiServerTest.test_dev_sample_api_is_read_only_and_ai_routes_404 -v
```

Expected: FAIL because only hinton exists and the API lacks `data_mode`.

- [ ] **Step 3: Refactor the generator into deterministic server profiles**

Keep existing hinton trees as the base. Generate four independent JSON documents using explicit profiles for hostname/id, generation time, capacity multiplier/pressure, and media assignment. Reset mutable user tallies per profile. All four sample JSON files, including `hinton.sample.json`, are generator-owned and must never be hand-edited.

- [ ] **Step 4: Expand the authoritative manifest**

Update `data/hosts.json` in approved order. Include a boolean `sample_data: true` on each tracked sample entry for static fallback; update `normalizeHosts` to preserve it in Task 5.

- [ ] **Step 5: Make development API manifest-driven**

Load `hosts.json` from `DEV_SAMPLE_DIR`, reject unsafe ids/files, require each listed sample file, and preserve manifest order. Add a service-level `data_mode` property and include it in `/api/servers` without weakening production read auth.

- [ ] **Step 6: Regenerate and test**

Run:

```bash
python3 data/gen_sample.py
python3 data/test_fixtures.py
python3 viewer/test_serve.py
```

Expected: all tests PASS and four sample JSON files are deterministic.

- [ ] **Step 7: Commit**

```bash
git add data/gen_sample.py data/*.sample.json data/hosts.json data/test_fixtures.py viewer/serve.py viewer/test_serve.py
git commit -m "feat: add ordered storage sample servers"
```

### Task 4: Identity-aware mount and aggregate model

**Files:**
- Modify: `viewer/overview.js:32-160,199-265`
- Modify: `viewer/viewer.test.js:80-180`
- Modify: `viewer/viewer_regression_test.js:216-326`

- [ ] **Step 1: Write failing pure-model tests**

Add snapshots with linked `selected_roots` and mounts. Assert:

- `usedBytes`, `totalBytes`, `availableBytes`, `usedPct`, `media`, and identity appear in every mount summary;
- duplicate identity counts once;
- inconsistent duplicate capacity becomes partial and excluded;
- legacy non-zero `major_minor` derives the same in-memory id;
- unresolved identity produces `excludedMountCount` and partial reasons;
- row order and mount order remain input order.

- [ ] **Step 2: Run Node model tests and verify failure**

Run: `node viewer/viewer.test.js`  
Expected: FAIL because the current model only exposes percent/free bytes.

- [ ] **Step 3: Implement mount linking and aggregate helpers**

Add focused functions:

```javascript
function selectedRootByMountId(snapshot) { /* preserve first valid root */ }
function summarizeMounts(snapshot, thresholds) { /* enrich in input order */ }
function aggregateMountCapacity(mounts) { /* exact/partial known-only model */ }
function buildOverviewAggregate(rows) { /* dedupe per server identity namespace */ }
```

Namespace page identities by server id so `dev-8-1` on two servers does not collide. For inconsistent duplicates, exclude the identity’s entire contribution.

- [ ] **Step 4: Implement exact partial copy helpers**

Return semantic fields rather than preformatted HTML: `isPartial`, `excludedMountCount`, `partialReasons`, `totalLabel`, `usedLabel`, `availableLabel`, and `utilizationLabel`. Use “확인된 용량 ≥ …”, “확인된 범위 …%”, and “N개 마운트 제외” only for partial aggregates.

- [ ] **Step 5: Run model and DOM regression tests**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add viewer/overview.js viewer/viewer.test.js viewer/viewer_regression_test.js
git commit -m "feat: aggregate mount capacity safely"
```

### Task 5: Approved mount-centric overview UI

**Files:**
- Modify: `viewer/data-client.js:120-225`
- Modify: `viewer/app.js:150-220`
- Modify: `viewer/index.html:15-58`
- Modify: `viewer/overview.js:180-265`
- Modify: `viewer/styles.css:65-115,416-432`
- Modify: `viewer/viewer.test.js`
- Modify: `viewer/viewer_regression_test.js`

- [ ] **Step 1: Write failing bootstrap and DOM tests**

Assert `/api/servers` returns `{servers, data_mode}` to callers, static hosts preserve `sample_data`, the visible sample marker appears only for sample mode, the page aggregate has one logical heading, server headers contain compact subtotals, and every mount cell contains path/media/used-total/percent/bar/free text.

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: FAIL on missing envelope metadata and mount-centric DOM.

- [ ] **Step 3: Preserve mode through bootstrap**

Change `loadServerSummaries` to return a normalized envelope. Carry `dataMode` through API/static bootstrap and `rememberBootstrap`. Static mode is sample only when the authoritative manifest rows explicitly carry `sample_data: true`.

- [ ] **Step 4: Add aggregate and sample-marker containers**

Add semantic, initially hidden elements in `index.html`:

```html
<p id="sampleDataMarker" class="sample-data-marker" hidden>샘플 데이터</p>
<section id="overviewAggregate" class="overview-aggregate" aria-label="전체 로컬 스토리지"></section>
```

Keep a single logical `h1`.

- [ ] **Step 5: Replace the current server-wide row meter with mount cells**

Render the approved A/mount-centric hierarchy. Use native button/list semantics, existing click navigation, text plus color for pressure, and neutral media labels. Do not sort rows or mounts. Keep number and percent nodes adjacent in the DOM and visually.

- [ ] **Step 6: Implement restrained responsive CSS**

Desktop: up to three mount cells per server group. Mobile: one column. No horizontal overflow. Use existing semantic tokens, quiet borders, compact spacing, and reduced-motion behavior. Avoid cards-inside-cards shadow stacking.

- [ ] **Step 7: Run all viewer tests**

Run:

```bash
python3 viewer/test_serve.py
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
```

Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add viewer/data-client.js viewer/app.js viewer/index.html viewer/overview.js viewer/styles.css viewer/viewer.test.js viewer/viewer_regression_test.js
git commit -m "feat: render mount-centric storage overview"
```

### Task 6: Operations documentation and full verification

**Files:**
- Modify: `docs/operations.md`
- Modify: `README.md` if local demo commands or sample count changed
- Evidence only: external verification archive outside the repository

- [ ] **Step 1: Create the external evidence root and capture the GPU baseline**

Run this before Linux verification, browser verification, or Storage Dashboard restart:

```bash
EVIDENCE_ROOT="/Users/shchoi/workspace/storage-viz-verification-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$EVIDENCE_ROOT"
VERIFY_STATE=/tmp/storage-viz-capacity-verification.env
GPU_LIVE_PID_BEFORE="$(lsof -tiTCP:8001 -sTCP:LISTEN)"
test -n "$GPU_LIVE_PID_BEFORE"
curl -fsS http://127.0.0.1:8001/health > "$EVIDENCE_ROOT/gpu-live-before.json"
GPU_LIVE_SHA_BEFORE="$(shasum -a 256 "$EVIDENCE_ROOT/gpu-live-before.json" | awk '{print $1}')"
GPU_DEV_PID_BEFORE="$(lsof -tiTCP:8101 -sTCP:LISTEN || true)"
if test -n "$GPU_DEV_PID_BEFORE"; then
  curl -fsS http://127.0.0.1:8101/health > "$EVIDENCE_ROOT/gpu-dev-before.json"
fi
{
  printf 'EVIDENCE_ROOT=%q\n' "$EVIDENCE_ROOT"
  printf 'GPU_LIVE_PID_BEFORE=%q\n' "$GPU_LIVE_PID_BEFORE"
  printf 'GPU_LIVE_SHA_BEFORE=%q\n' "$GPU_LIVE_SHA_BEFORE"
  printf 'GPU_DEV_PID_BEFORE=%q\n' "$GPU_DEV_PID_BEFORE"
} > "$VERIFY_STATE"
chmod 600 "$VERIFY_STATE"
```

- [ ] **Step 2: Document interpretation boundaries**

Explain that “managed local storage” is unique filesystem capacity, not raw disk inventory; SSD/HDD classification comes from backing leaf block devices; Mixed and Unknown are expected safe states; network mounts remain excluded.

- [ ] **Step 3: Run complete local regression gates**

Run:

```bash
python3 data/test_fixtures.py
python3 -m unittest discover -s agent -p 'test_*.py' -v
python3 -m unittest discover -s collector -p 'test_*.py' -v
python3 viewer/test_serve.py
node viewer/viewer.test.js
node viewer/viewer_regression_test.js
bash deploy/test_deploy_scripts.sh
bash -n install.sh deploy/install-agent.sh deploy/*.sh
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 4: Run Linux-only resolver/scanner verification**

Use the existing approved temporary Linux verification host with the repository-owned remote wrapper:

```bash
source /tmp/storage-viz-capacity-verification.env
STORAGE_VIZ_LINUX_HOST=ircv@166.104.167.11 \
STORAGE_VIZ_LINUX_PORT=2200 \
bash deploy/verify-linux.sh --remote
cp output/verification/linux-verification.txt "$EVIDENCE_ROOT/linux-verification.txt"
grep -q '^overall_exit_code=0$' "$EVIDENCE_ROOT/linux-verification.txt"
grep -q '^remote_cleanup=removed$' "$EVIDENCE_ROOT/linux-verification.txt"
```

Pass requires wrapper exit 0, `overall_exit_code=0`, and `remote_cleanup=removed`. Independently check that the recorded remote `/tmp/storage-viz-verify.*` path is absent. Do not install an agent or touch GPU Monitor paths. At the end of Task 6, copy browser evidence into the same external `EVIDENCE_ROOT`, then remove generated repository `output/` so the worktree remains clean.

- [ ] **Step 5: Restart only the local Storage Dashboard development tmux process**

Restart `storage-viz-dev` with `STORAGE_VIZ_DEV_SAMPLE_DIR=<worktree>/data`, keep `127.0.0.1:8088`, and verify `/api/servers` returns four ids in manifest order with `data_mode: sample`.

- [ ] **Step 6: Run Playwright UI QA and clean it up**

Use a named CLI session so cleanup checks never target another Playwright user:

```bash
source /tmp/storage-viz-capacity-verification.env
command -v npx >/dev/null
export PWCLI="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
export PLAYWRIGHT_CLI_SESSION=storage-viz-capacity
mkdir -p output/playwright
bash "$PWCLI" open http://127.0.0.1:8088/ --headed
bash "$PWCLI" resize 1440 1000
bash "$PWCLI" snapshot
bash "$PWCLI" eval "document.querySelectorAll('h1').length"
bash "$PWCLI" eval "document.documentElement.scrollWidth <= innerWidth"
bash "$PWCLI" screenshot --filename=output/playwright/desktop.png
bash "$PWCLI" resize 390 844
bash "$PWCLI" snapshot
bash "$PWCLI" eval "document.documentElement.scrollWidth <= innerWidth"
bash "$PWCLI" screenshot --filename=output/playwright/mobile.png
bash "$PWCLI" run-code "await page.emulateMedia({ reducedMotion: 'reduce' }); await page.reload(); await page.waitForLoadState('networkidle')"
bash "$PWCLI" snapshot
bash "$PWCLI" eval "getComputedStyle(document.documentElement).scrollBehavior === 'auto'"
bash "$PWCLI" console error
bash "$PWCLI" close
```

Snapshot/interaction checks must verify four servers in manifest order, aggregate capacity, all four media labels, detail navigation, sample marker, one `h1`, no horizontal overflow, zero console errors, and a successful reduced-motion reload. After `close`, this command must produce no output. Restrict the check to orphaned (`PPID 1`) named-session CLI daemons so the inspection command cannot match itself:

```bash
PLAYWRIGHT_LEFTOVERS="$(ps -axo pid=,ppid=,command= | awk '$2 == 1 && $0 ~ /cliDaemon[.]js/ && $0 ~ /storage-viz-capacity/ {print}')"
test -z "$PLAYWRIGHT_LEFTOVERS"
```

Any matching process is a failure and must be terminated by exact PID before continuing; unrelated Playwright sessions are not touched. Copy `output/playwright` to `$EVIDENCE_ROOT/playwright` before removing repository `output/`.

- [ ] **Step 7: Capture the GPU endpoint after-state and verify isolation**

Run immediately after browser verification:

```bash
source /tmp/storage-viz-capacity-verification.env
GPU_LIVE_PID_AFTER="$(lsof -tiTCP:8001 -sTCP:LISTEN)"
test "$GPU_LIVE_PID_AFTER" = "$GPU_LIVE_PID_BEFORE"
curl -fsS http://127.0.0.1:8001/health > "$EVIDENCE_ROOT/gpu-live-after.json"
test "$(shasum -a 256 "$EVIDENCE_ROOT/gpu-live-after.json" | awk '{print $1}')" = "$GPU_LIVE_SHA_BEFORE"
GPU_DEV_PID_AFTER="$(lsof -tiTCP:8101 -sTCP:LISTEN || true)"
if test -n "$GPU_DEV_PID_BEFORE"; then
  test "$GPU_DEV_PID_AFTER" = "$GPU_DEV_PID_BEFORE"
  curl -fsS http://127.0.0.1:8101/health > "$EVIDENCE_ROOT/gpu-dev-after.json"
  cmp -s "$EVIDENCE_ROOT/gpu-dev-before.json" "$EVIDENCE_ROOT/gpu-dev-after.json"
else
  test -z "$GPU_DEV_PID_AFTER"
  printf '%s\n' 'SKIP: GPU dev endpoint was not running' > "$EVIDENCE_ROOT/gpu-dev-skip.txt"
fi
```

Pass requires the live PID and SHA-256 to be identical before/after. For port 8101: if present before, it must remain present with the same PID and byte-identical response; if absent before and after, the explicit skip file is required. Any availability transition is a failure. This PID contract proves no monitored GPU process restarted. No GPU tmux/service command is permitted.

Finally archive and clean generated repository evidence:

```bash
cp -R output/playwright "$EVIDENCE_ROOT/playwright"
rm -rf output
rm -f /tmp/storage-viz-capacity-verification.env
git status --short
```

- [ ] **Step 8: Commit docs and final repairs**

```bash
git add docs/operations.md README.md
git commit -m "docs: explain storage capacity and media"
```

- [ ] **Step 9: Request final code review and verifier verdict**

Dispatch separate `code-reviewer` and `verifier` agents. Resolve Critical/Important findings, rerun affected gates, and leave the feature branch/worktree intact unless the user explicitly chooses merge or PR.
