# GPU Live Promotion and Automatic Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the current GPU Monitor development line to production, preserve the existing nine-server Live database, and enable exact-SHA automatic deployment after successful `main` CI.

**Architecture:** Keep GitHub as the validation source and the existing outbound server puller as the deployment transport. Add a production-only debug boundary, a SQLite preflight/backup invariant before schema changes, release health checks that require the configured server floor, and payload-digest no-op handling so non-runtime commits do not restart Live. Validate the promoted release on non-production ports against a disposable online backup before one guarded cutover from legacy tmux to managed systemd.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/aiosqlite, SvelteKit/Svelte 5, Node 18+, Bash, systemd, GitHub Actions, SQLite.

## Global Constraints

- GPU Monitor is the only application entering automatic deployment in this plan.
- Storage Monitor must not be restarted, reconfigured, or deployed.
- `main` is the production trunk; local and feature branches never deploy.
- The existing nine registered servers and current Live notes must survive promotion.
- Candidate validation uses a disposable database copy with collectors and Slack disabled.
- No new runtime dependency is added for cleanup or deployment.
- Production deployment remains outbound-only; GitHub does not SSH into the server.
- A failed CI, preflight, activation, or health check leaves or restores the last healthy Live runtime.

---

### Task 1: Lock the promotion and repository-cleanup contracts

**Files:**
- Modify: `tests/test_repository_layout.py`
- Modify: `apps/gpu-monitor/frontend/tests/runtime-server.test.mjs`
- Delete: `apps/gpu-monitor/output/task-briefs/quiet-rack-task-3-report.md`
- Delete: `apps/gpu-monitor/output/task-briefs/quiet-rack-task-4-report.md`
- Delete: `apps/gpu-monitor/output/task-briefs/quiet-rack-task-5-report.md`
- Delete: `apps/gpu-monitor/output/task-briefs/quiet-rack-task-6-report.md`
- Delete: `apps/gpu-monitor/output/task-briefs/quiet-rack-task-7-report.md`
- Delete: `apps/gpu-monitor/BACKEND_REVIEW.md`
- Delete: `apps/gpu-monitor/FRONTEND_REVIEW.md`
- Delete: `apps/gpu-monitor/PLAN.md`
- Delete: `apps/gpu-monitor/PLAN_REVIEW.md`
- Delete: `apps/gpu-monitor/GIT_CONVENTIONS.md`

**Interfaces:**
- Consumes: existing repository tracked-file inventory and production runtime test harness.
- Produces: `RepositoryLayoutTest.test_gpu_tree_excludes_development_output_and_obsolete_review_files` and a production `/debug` denial assertion used by later tasks.

- [ ] **Step 1: Write the failing repository-layout test**

Add a test that calls `git ls-files apps/gpu-monitor` and fails when any tracked path starts with `apps/gpu-monitor/output/` or equals one of the five obsolete root review/plan files above.

- [ ] **Step 2: Write the failing production runtime assertion**

In `runtime-server.test.mjs`, add an assertion to the production server test:

```js
const debugResponse = await fetch(`${baseUrl}/debug`, { redirect: 'manual' });
assert.equal(debugResponse.status, 404);
```

- [ ] **Step 3: Run the focused tests and confirm failure**

Run:

```bash
python3.12 -m unittest \
  tests.test_repository_layout.RepositoryLayoutTest.test_gpu_tree_excludes_development_output_and_obsolete_review_files -v
cd apps/gpu-monitor/frontend && npm run test:runtime
```

Expected: repository test fails on tracked output/review files; runtime test fails because `/debug` is exposed.

- [ ] **Step 4: Remove only the obsolete tracked artifacts**

Delete the listed output reports and superseded root review/plan files. Preserve `README.md`, `DESIGN.md`, `FEATURES.md`, `CLAUDE.md`, `feature/`, and `docs/` because they remain product or historical design documentation.

- [ ] **Step 5: Run the repository test**

Run:

```bash
python3.12 -m unittest tests.test_repository_layout -v
```

Expected: repository-layout tests pass; runtime `/debug` assertion still fails until Task 2.

- [ ] **Step 6: Commit the cleanup contract**

```bash
git add tests/test_repository_layout.py apps/gpu-monitor
git commit -m "chore: remove gpu development artifacts"
```

---

### Task 2: Make debug scenarios local-development-only

**Files:**
- Create: `apps/gpu-monitor/frontend/src/routes/debug/+page.ts`
- Modify: `apps/gpu-monitor/frontend/src/routes/+page.svelte`
- Modify: `apps/gpu-monitor/frontend/src/routes/dev-scenario-integration.contract.test.ts`
- Modify: `apps/gpu-monitor/frontend/tests/runtime-server.test.mjs`

**Interfaces:**
- Consumes: SvelteKit `$app/environment.dev`, existing `activeDevScenario`, `applyDevScenario`, and `/debug` page.
- Produces: production `/debug` HTTP 404; production dashboard never reads or applies a persisted simulation; local `vite dev` keeps the debug route and scenario controls.

- [ ] **Step 1: Extend the source contract test**

Require `+page.ts` to import `dev` from `$app/environment`, call `error(404, 'Not found')` when `!dev`, and require the main page to render debug links/simulation banners only under `{#if dev}`.

- [ ] **Step 2: Run the source contract test and confirm failure**

```bash
cd apps/gpu-monitor/frontend
node --test src/routes/dev-scenario-integration.contract.test.ts
```

Expected: FAIL because the route guard and production UI guards do not exist.

- [ ] **Step 3: Add the route guard**

Create `src/routes/debug/+page.ts`:

```ts
import { dev } from '$app/environment';
import { error } from '@sveltejs/kit';

export const load = () => {
  if (!dev) error(404, 'Not found');
  return {};
};
```

- [ ] **Step 4: Guard simulation state in the dashboard**

Import `dev` from `$app/environment`; when `dev` is false, use live server/status data directly, never call `applyDevScenario`, never show `SIMULATION`, and omit `/debug` links from the management menu.

- [ ] **Step 5: Run frontend checks and production runtime tests**

```bash
cd apps/gpu-monitor/frontend
npm run check
npm run build
npm run test:runtime
```

Expected: 0 Svelte diagnostics; build passes; production `/debug` returns 404; `/`, `/logs`, `/api/health`, and WebSocket runtime tests remain green.

- [ ] **Step 6: Commit the production debug boundary**

```bash
git add apps/gpu-monitor/frontend
git commit -m "fix: keep gpu simulations out of production"
```

---

### Task 3: Add SQLite Live-state preflight, online backup, and compatibility tests

**Files:**
- Create: `apps/gpu-monitor/backend/live_database.py`
- Create: `apps/gpu-monitor/backend/tests/test_live_database.py`
- Modify: `apps/gpu-monitor/backend/config.py`
- Modify: `apps/gpu-monitor/backend/database.py`
- Modify: `apps/gpu-monitor/backend/main.py`
- Modify: `apps/gpu-monitor/.env.example`

**Interfaces:**
- Produces:
  - `sqlite_path_from_url(database_url: str) -> pathlib.Path`
  - `inspect_live_database(path: Path) -> DatabaseSnapshot`
  - `backup_live_database(source: Path, backup_dir: Path, keep: int) -> Path`
  - `prepare_live_database(database_url: str, expected_server_count: int, backup_dir: str | None, backup_keep: int) -> DatabaseSnapshot`
- `DatabaseSnapshot` contains `server_count: int`, `server_names: tuple[str, ...]`, `note_count: int`, and `integrity_ok: bool`.

- [ ] **Step 1: Write legacy-schema compatibility tests**

Create a temporary SQLite fixture with the legacy `servers` table, nine named rows, and a legacy `notes` table lacking `display_name`, `priority`, `kind`, `gpu_indices`, and `expires_at`. Test that:

```python
snapshot = prepare_live_database(url, 9, str(backup_dir), 3)
self.assertEqual(snapshot.server_count, 9)
self.assertTrue(snapshot.integrity_ok)
self.assertTrue(next(backup_dir.glob("gpu-monitor-*.db"), None))
```

Also test missing DB, corrupt DB, zero-server DB, and an expected count greater than the real count all raise `LiveDatabaseError` before schema mutation.

- [ ] **Step 2: Run the new tests and confirm failure**

```bash
cd apps/gpu-monitor
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python3.12 -m unittest backend.tests.test_live_database -v
```

Expected: FAIL because `backend.live_database` does not exist.

- [ ] **Step 3: Implement read-only inspection and SQLite online backup**

Use Python's standard `sqlite3` module. Run `PRAGMA integrity_check`, read server names ordered by `display_order, id` when available, count notes when the table exists, and use `sqlite3.Connection.backup()` into a temporary file followed by `os.replace()` for publication. Set backup files to mode `0600` and retain the newest `keep` files.

- [ ] **Step 4: Add explicit production settings**

Add to `Settings`:

```python
monitoring_expected_server_count: int = 0
monitoring_database_backup_dir: str = ""
monitoring_database_backup_keep: int = 5
```

Validate all integer fields as non-negative and backup retention as at least 1 when a backup directory is configured. Document matching environment variables in `.env.example`.

- [ ] **Step 5: Run preflight before schema initialization**

At FastAPI lifespan startup, call `prepare_live_database` via `await asyncio.to_thread(...)` before `init_db()` whenever `MONITORING_EXPECTED_SERVER_COUNT > 0`. Do not create a missing database in this mode. Keep local development behavior unchanged when the expected count is 0.

- [ ] **Step 6: Verify schema upgrade on the disposable legacy fixture**

Extend the test to run `ensure_notes_expiry_schema_sync` after preflight and assert that all five newer note columns exist while nine server rows and legacy notes remain.

- [ ] **Step 7: Run backend regression tests**

```bash
cd apps/gpu-monitor
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python3.12 -m unittest discover -s backend/tests -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit the Live-state guard**

```bash
git add apps/gpu-monitor/backend apps/gpu-monitor/.env.example
git commit -m "feat: guard and back up gpu live database"
```

---

### Task 4: Enforce server invariants in activation health checks

**Files:**
- Modify: `apps/gpu-monitor/deploy/server/health-check.sh`
- Modify: `apps/gpu-monitor/deploy/server/install-deployer.sh`
- Modify: `apps/gpu-monitor/deploy/test_release_scripts.sh`
- Modify: `apps/gpu-monitor/deploy/README.md`

**Interfaces:**
- Consumes: `/etc/gpu-monitor/live.env` values `MONITORING_EXPECTED_SERVER_COUNT`, `GPU_MONITOR_BACKEND_PORT`, and the managed backend `/servers` endpoint.
- Produces: activation failure when the API server count is lower than the configured floor; installer preserves operator-set invariant/backup values while managing only reserved port keys.

- [ ] **Step 1: Add failing shell regression cases**

Add health-check test fixtures where `/health` succeeds but `/servers` returns `[]`, malformed JSON, or fewer rows than `MONITORING_EXPECTED_SERVER_COUNT=9`. Each must fail. Add a passing case with nine objects.

- [ ] **Step 2: Run the focused release-script suite and confirm failure**

```bash
bash apps/gpu-monitor/deploy/test_release_scripts.sh
```

Expected: new invariant cases fail because `health-check.sh` checks only endpoint health/listeners/PIDs.

- [ ] **Step 3: Implement the API invariant check**

After the backend/frontend health probes and before the final stable-PID snapshot, fetch `/servers` with bounded retries and parse it using `/usr/bin/python3`. Require a JSON array and `len(array) >= expected`. Skip only when expected is exactly `0`.

- [ ] **Step 4: Preserve operator state in installer reconciliation**

Keep `MONITORING_EXPECTED_SERVER_COUNT`, `MONITORING_DATABASE_BACKUP_DIR`, `MONITORING_DATABASE_BACKUP_KEEP`, `DATABASE_URL`, and secrets untouched. Continue atomically rewriting only reserved host/port/shared-directory keys.

- [ ] **Step 5: Run release and installer tests**

```bash
make release-script-test
```

Expected: all activation, rollback, PID, listener, installer, and server-floor cases pass.

- [ ] **Step 6: Commit the activation invariant**

```bash
git add apps/gpu-monitor/deploy
git commit -m "fix: require registered servers in gpu live health"
```

---

### Task 5: Avoid Live restart when the GPU runtime payload is unchanged

**Files:**
- Modify: `apps/gpu-monitor/deploy/server/gpu-monitor-release-puller.py`
- Modify: `tests/test_gpu_release_puller.py`
- Modify: `apps/gpu-monitor/deploy/test_release_scripts.sh`
- Modify: `apps/gpu-monitor/deploy/README.md`

**Interfaces:**
- Consumes: built release manifest `sha256`, `status live` JSON `current_sha256`, and current `main` SHA.
- Produces: `record_current_sha(config, sha)` without upload/activate when the newly built artifact digest equals the active release digest.

- [ ] **Step 1: Write the failing puller unit test**

Simulate a newer authorized `main` SHA whose built manifest digest equals `status live.current_sha256`. Assert the call order includes fetch, authorize, checkout, build, and status but excludes `upload` and `activate`; assert `current-live-sha` advances to the new SHA.

- [ ] **Step 2: Add a cross-commit deterministic artifact test**

In a disposable Git repository, build one commit, add a root documentation-only commit, build again, and assert both tarball SHA-256 digests are identical. This proves no-op digest comparison is safe for non-runtime changes.

- [ ] **Step 3: Run tests and confirm failure**

```bash
python3.12 -m unittest tests.test_gpu_release_puller -v
bash apps/gpu-monitor/deploy/test_release_scripts.sh
```

Expected: puller attempts activation and/or the deterministic cross-commit contract is absent.

- [ ] **Step 4: Implement digest-based no-op advancement**

After build validation and final authorization, obtain `status live`. If the active digest equals the candidate digest, atomically record the new current SHA, clear failed-release state, clean build output, and return without upload/activation.

- [ ] **Step 5: Run puller and release suites**

```bash
make release-puller-test release-script-test
```

Expected: all tests pass and unchanged runtime payloads do not restart Live.

- [ ] **Step 6: Commit the no-op optimization**

```bash
git add tests/test_gpu_release_puller.py apps/gpu-monitor/deploy
git commit -m "perf: skip unchanged gpu live payloads"
```

---

### Task 6: Update production operating documentation and run adversarial review

**Files:**
- Modify: `README.md`
- Modify: `CONTRIBUTING.md`
- Modify: `apps/gpu-monitor/README.md`
- Modify: `docs/development.md`
- Modify: `docs/operations/github-cicd.md`
- Modify: `docs/architecture.md`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Produces: one unambiguous workflow: local development -> optional PR/direct main push -> CI -> exact-SHA outbound deployment; no permanent Dev server; Storage not auto-deployed.

- [ ] **Step 1: Add documentation contract assertions**

Require documentation to mention the Live DB invariant variables, candidate-copy verification, payload no-op behavior, emergency legacy fallback during first promotion, and that Storage-only changes cannot restart GPU Live.

- [ ] **Step 2: Run repository contract tests and confirm failure**

```bash
python3.12 -m unittest tests.test_repository_layout -v
```

- [ ] **Step 3: Rewrite contradictory operating sections**

Remove stale GitHub Environment/secret deployment instructions. Document systemd timer status, puller logs, exact emergency rollback commands, database backup location, how to identify the active release, and the first-cutover legacy fallback boundary.

- [ ] **Step 4: Run complete local verification**

```bash
make verify
git diff --check
git status --short
```

Expected: all repository, GPU, Storage, deployment, frontend, backend, and static checks pass; only intended tracked changes are present.

- [ ] **Step 5: Request independent reviews**

Dispatch:

- `code-reviewer`: correctness, security, data-loss, and rollback review.
- `test-engineer`: test adequacy and failure-mode review.
- `code-simplifier`: behavior-preserving simplification of changed code only.

Address Critical/Important findings with focused tests before proceeding.

- [ ] **Step 6: Commit documentation and review fixes**

```bash
git add README.md CONTRIBUTING.md apps/gpu-monitor/README.md docs tests apps/gpu-monitor
git commit -m "docs: finalize gpu live promotion operations"
```

---

### Task 7: Validate the exact candidate against a real Live database copy

**Files:**
- No repository file changes expected.
- Remote temporary paths: `/home/ircv/workspace/gpu-monitor-promotion-candidate/`, `/home/ircv/workspace/gpu-monitor-promotion-data/`.

**Interfaces:**
- Consumes: exact feature-branch SHA, restored source DB `/home/ircv/workspace/monitoring_v2/backend/data/recovery_gpu_monitor.db`.
- Produces: candidate evidence for nine server identities, notes preservation, health, WebSocket, and disabled side effects.

- [ ] **Step 1: Push the feature branch and require CI success**

```bash
git push -u origin feat/promote-gpu-live-autodeploy
gh run list --branch feat/promote-gpu-live-autodeploy --workflow ci.yml --limit 1
gh run watch <run-id> --exit-status
```

- [ ] **Step 2: Snapshot production before candidate work**

Record legacy tmux sessions, PIDs, listeners, frontend/API health, the nine server names, database integrity/counts, Storage service PID/start time, and Storage HTTP status.

- [ ] **Step 3: Create a disposable online backup**

Use Python `sqlite3.Connection.backup()` while legacy Live remains running. Run `PRAGMA integrity_check`; compare registered server names and note count with the source.

- [ ] **Step 4: Build and run the exact candidate on non-production ports**

Check out the exact branch SHA into the temporary candidate directory. Run backend on `127.0.0.1:18101`, frontend on `127.0.0.1:15173`, and bridge only if needed on `127.0.0.1:18000`, with:

```text
DATABASE_URL=<disposable-copy>
MONITORING_EXPECTED_SERVER_COUNT=9
MONITORING_DATABASE_BACKUP_DIR=<candidate-backups>
MONITORING_DISABLE_COLLECTORS=true
MONITORING_DISABLE_SLACK=true
```

- [ ] **Step 5: Verify candidate behavior**

Require HTTP health, frontend proxy health, `/debug` 404, nine exact server names, notes readability, WebSocket handshake, no SSH collector processes, no Slack Socket Mode, and no writes to the production DB inode.

- [ ] **Step 6: Stop candidate processes and retain evidence only**

Remove temporary processes and candidate runtime directories after saving sanitized command outputs. Confirm legacy Live and Storage PIDs remain unchanged.

---

### Task 8: Fast-forward main, provision managed Live data, and enable automatic deployment

**Files:**
- Server configuration: `/etc/gpu-monitor/live.env`
- Managed data: `/var/lib/gpu-monitor/live/gpu_monitor.db`
- Puller state: `/var/lib/gpu-monitor/puller/`

**Interfaces:**
- Consumes: verified feature SHA, successful `main` CI, verified candidate DB copy.
- Produces: managed systemd Live on ports 5173/8001/8000 and enabled outbound puller timer.

- [ ] **Step 1: Fast-forward and push main**

```bash
git switch main
git merge --ff-only feat/promote-gpu-live-autodeploy
git push origin main
```

- [ ] **Step 2: Wait for exact main CI success**

Require `ci/impact`, `ci/repository`, `ci/gpu`, and `ci/required` success for the exact new `main` SHA before changing Live.

- [ ] **Step 3: Provision managed Live DB atomically**

Create a final online backup from the still-running legacy DB, run integrity/count/name checks, publish it atomically to `/var/lib/gpu-monitor/live/gpu_monitor.db`, and set ownership/mode for `gpu-monitor-live`. Configure:

```text
DATABASE_URL=sqlite+aiosqlite:////var/lib/gpu-monitor/live/gpu_monitor.db
MONITORING_EXPECTED_SERVER_COUNT=9
MONITORING_DATABASE_BACKUP_DIR=/var/lib/gpu-monitor/live/backups
MONITORING_DATABASE_BACKUP_KEEP=5
```

Preserve existing secrets and Slack settings.

- [ ] **Step 4: Perform one guarded cutover**

Use an EXIT trap: stop legacy tmux only immediately before starting the puller; on any nonzero result stop failed managed units and restart the exact three legacy tmux sessions. Start the puller once and wait for activation/health completion.

- [ ] **Step 5: Verify production**

Require:

- Managed backend/frontend/bridge units active with stable PIDs.
- Listeners exactly `127.0.0.1:8001`, `0.0.0.0:5173`, and `0.0.0.0:8000`.
- External `/api/health` success.
- `/api/servers` contains the same nine server identities in preserved order.
- Existing notes are readable.
- Collector freshness resumes after one normal interval.
- `/debug` returns 404.
- Storage service PID/start time is unchanged and port 8088 returns 200.

- [ ] **Step 6: Enable and verify the timer**

Enable `gpu-monitor-release-puller.timer`, wait for the next scheduled cycle, and verify it exits successfully without restarting active PIDs for the already-current SHA.

- [ ] **Step 7: Verify one documentation-only no-op release**

After the initial promotion is stable, use a harmless documentation-only commit or the next naturally occurring docs commit. Require CI success, puller state advancement, identical GPU payload digest, and unchanged Live PIDs.

- [ ] **Step 8: Final evidence and cleanup**

Remove temporary candidate files and failed-release state only when no failure is active. Keep the legacy source tree and database backup as a documented emergency checkpoint; do not delete them in this task.

---

## Plan Self-Review

- Every requirement in the approved design maps to Tasks 1-8.
- Production data is copied and verified before any cutover.
- Debug scenarios remain available locally but are inaccessible in production.
- Storage is tested but not deployed or restarted.
- The first cutover has a legacy fallback; later releases use immutable pointer rollback.
- No task requires GitHub deployment secrets or inbound SSH.
- All code changes use focused failing tests before implementation.

