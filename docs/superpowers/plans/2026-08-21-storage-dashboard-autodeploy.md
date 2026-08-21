# Storage Dashboard Automatic Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically activate every CI-approved `main` commit as the independent Storage Dashboard Live release while preserving configuration/data and rolling back unhealthy releases.

**Architecture:** A root-owned five-minute server puller verifies GitHub `main` and `ci/required`, builds an allowlisted deterministic artifact as `storage-viz-builder`, and streams it to an installed activator. The activator publishes immutable releases, atomically points `/opt/storage-viz-dashboard` at the selected release, restarts only Storage dashboard/proxy services, and restores the previous target if the public capability/inventory health contract fails.

**Tech Stack:** Python 3 standard library, Bash, systemd, GitHub REST API, existing repository CI authorizer, unittest/pytest.

---

## File structure

### New production files

- `apps/storage-monitor/deploy/build-dashboard-release.py` — deterministic, allowlisted Storage central-runtime artifact builder.
- `apps/storage-monitor/deploy/server/storage-monitor-release-puller.py` — outbound GitHub evidence, builder orchestration, backoff, activation, and SHA state.
- `apps/storage-monitor/deploy/server/activate-dashboard-release.py` — bounded artifact intake, archive validation, immutable publication, active pointer, restart, rollback, and status.
- `apps/storage-monitor/deploy/server/health-check-dashboard.py` — public proxy capability and production inventory health contract.
- `apps/storage-monitor/deploy/server/storage-viz-proxy-launcher.py` — installed target-validated launcher for active or protected legacy proxy code.
- `apps/storage-monitor/deploy/server/install-dashboard-deployer.sh` — one-time root bootstrap for builder identity, directories, installed scripts, runtime/puller units, and timer.
- `apps/storage-monitor/deploy/server/systemd/storage-viz-proxy.service` — systemd owner of public port 505.
- `apps/storage-monitor/deploy/server/systemd/storage-monitor-release-puller.service` — hardened one-shot puller.
- `apps/storage-monitor/deploy/server/systemd/storage-monitor-release-puller.timer` — persistent five-minute polling cadence.

### New tests

- `apps/storage-monitor/deploy/test_dashboard_release.py` — builder and activator behavior/security tests.
- `tests/test_storage_release_puller.py` — puller state machine and GitHub authorization tests.

### Modified files

- `Makefile` — add Storage puller tests to repository CI without removing or renaming the existing GPU `release-puller-test`, and provide an explicit Storage release-build target.
- `tests/test_repository_layout.py` — require the Storage deployment assets and Make targets.
- `apps/storage-monitor/deploy/test_deploy_scripts.sh` — validate installer/systemd isolation and dry-run contracts.
- `apps/storage-monitor/README.md` — state automatic central-dashboard deployment behavior.
- `apps/storage-monitor/docs/operations.md` — bootstrap, status, rollback, failure, and manual-rescan verification runbook.
- `docs/operations/github-cicd.md` — narrowly replace only the obsolete Storage-manual paragraph with the independent Storage puller contract; do not alter GPU Live policy or commands.
- `apps/storage-monitor/viewer/serve.py` — add a fail-closed loopback-only candidate preflight mode that disables polling and real rescan jobs while retaining production inventory/session/CSRF routing.
- `apps/storage-monitor/viewer/test_serve.py` — validate preflight startup restrictions and absence of polling/real jobs.
- `scripts/ci_impact.py` — classify Storage dashboard build/puller/activator/health/proxy paths as `storage_dashboard`.
- `tests/test_ci_impact.py` — lock the deployment asset classification.

## Task 1: Lock the repository deployment contract

**Files:**
- Modify: `tests/test_repository_layout.py`
- Modify: `Makefile`
- Modify: `scripts/ci_impact.py`
- Modify: `tests/test_ci_impact.py`

- [ ] Add failing layout tests requiring all Storage release-puller, activator, health, installer, and systemd paths.
- [ ] Add a failing test requiring `make test` to depend additively on `storage-release-puller-test` while preserving the existing GPU `release-puller-test` dependency and recipe unchanged.
- [ ] Add failing path-classification tests for `apps/storage-monitor/deploy/server/**`, `build-dashboard-release.py`, `direct_proxy.py`, and their tests; each must set `storage_dashboard` so `ci/storage` gates the SHA.
- [ ] Run `python3 -m unittest tests.test_ci_impact -v` and confirm the new deployment-path cases fail before changing `scripts/ci_impact.py`.
- [ ] Run `python3 -m unittest tests.test_repository_layout -v` and confirm failures identify missing assets/target.
- [ ] Add the minimal Make target wiring without creating production deploy files yet.
- [ ] Re-run the layout test; retain expected missing-file failures for subsequent tasks.

## Task 2: Build a deterministic central-runtime artifact

**Files:**
- Create: `apps/storage-monitor/deploy/build-dashboard-release.py`
- Create: `apps/storage-monitor/deploy/test_dashboard_release.py`

- [ ] Write failing tests for exact SHA/clean-checkout requirements, deterministic bytes, manifest/checksum binding, required runtime paths, and exclusion of inventory, keys, environment, data/state, cache, tests, samples, generated output, scanner binaries, and secrets.
- [ ] Run the focused tests and confirm they fail because the builder is absent.
- [ ] Implement the minimal builder using `git archive`/tracked source inspection and standard-library deterministic `tar.gz` generation with a single `storage-monitor/` root.
- [ ] Re-run focused tests and `git diff --check`.
- [ ] Commit the artifact builder slice.

## Task 3: Implement bounded activation and rollback

**Files:**
- Create: `apps/storage-monitor/deploy/server/activate-dashboard-release.py`
- Extend: `apps/storage-monitor/deploy/test_dashboard_release.py`

- [ ] Write failing tests for SHA/digest validation, stdin size bound, tar traversal, absolute paths, links/devices/FIFOs, file-count/expanded-size bounds, required files, duplicate release mismatch, and status output.
- [ ] Write failing integration-style tests for first migration from an existing `/opt/storage-viz-dashboard` directory, atomic release switch, successful state persistence, failed health rollback, and refusal to modify any GPU path.
- [ ] Cover three starting states explicitly: a real legacy directory, a valid symlink to an immutable Storage release, and a malformed/broken/external symlink that must fail closed without restart.
- [ ] Run focused tests and verify expected failures.
- [ ] Implement private incoming files, strict archive inspection/extraction, immutable `/srv/storage-viz-dashboard/releases/<sha>/storage-monitor`, `/opt/storage-viz-dashboard` pointer switching, locked state, restart callback, rollback, and bounded pruning.
- [ ] On first migration atomically rename the real app directory to `/opt/storage-viz-dashboard.legacy.<timestamp>`, record `legacy_backup`, restore it exactly on failure, and exclude all legacy backups from automatic pruning.
- [ ] For an existing valid release symlink, record its canonical Storage release target as `previous` and switch only the symlink; for any target outside `/srv/storage-viz-dashboard/releases`, reject activation before changing files or services.
- [ ] Acceptance checks after successful first migration: `/opt/storage-viz-dashboard` is a symlink to the exact SHA release, the legacy directory still exists with unchanged sentinel content, state records both current release and `legacy_backup`, and a simulated health failure restores the original real directory byte-for-byte at the original path.
- [ ] Re-run focused tests and verify no temporary or partially active paths remain.
- [ ] Commit the activator slice.

## Task 4: Define the production health contract and managed proxy

**Files:**
- Create: `apps/storage-monitor/deploy/server/health-check-dashboard.py`
- Create: `apps/storage-monitor/deploy/server/systemd/storage-viz-proxy.service`
- Extend: `apps/storage-monitor/deploy/test_dashboard_release.py`

- [ ] Write failing tests for inactive services, invalid session JSON, `can_rescan: false`, failed non-mutating POST readiness, sample mode, missing/reordered enabled inventory servers, incoherent environment pairs, and successful production capability/inventory responses.
- [ ] Write failing backend tests proving candidate preflight mode requires loopback + production inventory, rejects sample/direct modes, does not start the central poller, and cannot launch a real rescan while still returning `UNKNOWN_SERVER` after valid session/CSRF authentication.
- [ ] Implement strict non-shell parsing of dashboard/proxy environment files and reject missing, duplicate, malformed, or conflicting values before network checks.
- [ ] Implement bounded retries and exact parsing of `/etc/storage-viz/servers.json`, `/api/session`, and `/api/servers` through the public proxy.
- [ ] Retain the session cookie/CSRF token and POST `{}` with exact Host/Origin to a guaranteed-nonexistent valid server id; require `404 UNKNOWN_SERVER` so no scan is started.
- [ ] Generate the probe ID randomly within the shared 1-128 character server-id grammar and assert it is absent from the parsed inventory before sending the request.
- [ ] Require the systemd proxy unit to execute the installed target-validating launcher, load `/etc/storage-viz/proxy.env`, own port 505, and use hardening independent of GPU services.
- [ ] Run the proxy as the unprivileged Storage identity with only `CapabilityBoundingSet=CAP_NET_BIND_SERVICE` and `AmbientCapabilities=CAP_NET_BIND_SERVICE`; test that root execution and all broader capabilities are absent.
- [ ] Test that the launcher accepts only the active immutable Storage release or recorded legacy backup and rejects broken, unrecorded, external, writable, or GPU targets.
- [ ] Re-run focused tests and systemd asset checks.
- [ ] Commit the health/proxy slice.

## Task 5: Implement the outbound Storage release puller

**Files:**
- Create: `apps/storage-monitor/deploy/server/storage-monitor-release-puller.py`
- Create: `tests/test_storage_release_puller.py`

- [ ] Port the proven GPU puller tests to Storage names and paths before implementation: already-current cheap exit, exact `main` SHA, successful `ci.yml`/`ci/required`, pre/post-build reauthorization, SHA drift rejection, unprivileged clean checkout, manifest validation, streamed activation, status reconciliation, same-digest no-op, lock, failure backoff, malformed evidence, API pagination, and timeout handling.
- [ ] Run `python3 -m unittest tests.test_storage_release_puller -v` and confirm failure because the puller is absent.
- [ ] Implement a Storage-specific puller without importing or modifying GPU deployment code.
- [ ] Use `storage-viz-builder`, `/var/lib/storage-viz-dashboard/puller`, `/var/lib/storage-viz-dashboard/builder`, `/usr/local/libexec/storage-dashboard-activate.py`, and the Storage artifact names.
- [ ] Install and invoke a Storage-owned copy of `scripts/authorize_gpu_release.py` at `/usr/local/libexec/storage-release-authorizer.py`; never depend on GPU deployer installation state or GPU runtime paths.
- [ ] Re-run focused tests and compare behavior with `tests/test_gpu_release_puller.py`.
- [ ] Commit the puller slice.

## Task 6: Install and schedule the deployer safely

**Files:**
- Create: `apps/storage-monitor/deploy/server/install-dashboard-deployer.sh`
- Create: `apps/storage-monitor/deploy/server/systemd/storage-monitor-release-puller.service`
- Create: `apps/storage-monitor/deploy/server/systemd/storage-monitor-release-puller.timer`
- Modify: `apps/storage-monitor/deploy/test_deploy_scripts.sh`

- [ ] Write failing dry-run tests for rendered destinations, ownership/modes, root requirement, builder isolation, state/release directories, exact installed script hashes, no network/service actions in dry-run, and no GPU path/service/user references.
- [ ] Write failing systemd tests for root puller, low CPU/IO priority, strict writable paths, five-minute persistent timer, and explicit Storage-only services.
- [ ] Implement idempotent bootstrap and `--dry-run --prefix` rendering.
- [ ] Ensure real bootstrap preserves `/etc/storage-viz`, `/var/lib/storage-viz-dashboard/data`, `/var/lib/storage-viz-dashboard/state`, and the existing `/opt/storage-viz-dashboard` until first approved activation.
- [ ] Keep the legacy dashboard on 8088 and tmux proxy on 505 while starting the candidate dashboard on loopback 18088 with isolated temporary data/state and preflight mode, plus the candidate proxy on loopback 1505 targeting 18088 with the configured public Host/Origin.
- [ ] Stop the exact 505 owner and legacy 8088 dashboard only after the full candidate-topology non-mutating probe passes; then switch the release and start managed 8088/505 services.
- [ ] On cutover failure, point the managed launcher at the protected legacy backup, restore the legacy dashboard path, start the managed proxy with legacy code/config, and require previous dashboard/inventory GET health on port 505 before reporting rollback success; do not attempt to recreate tmux.
- [ ] Enable the timer only after an approved release passes health checks; never enable remote scan timers here.
- [ ] Re-run deploy-script tests.
- [ ] Commit the installer/systemd slice.

## Task 7: Document the operational invariant

**Files:**
- Modify: `apps/storage-monitor/README.md`
- Modify: `apps/storage-monitor/docs/operations.md`
- Modify: `docs/operations/github-cicd.md`

- [ ] Document that `main` + successful `ci/required` automatically converges Storage Live without approvals, as a storage-only addition that leaves every existing GPU Live policy statement and command intact.
- [ ] Document expected delay, status commands, active SHA inspection, failed-release backoff, rollback, proxy service ownership, and one-time bootstrap.
- [ ] Explicitly state that central deployment never changes GPU Monitor or remote storage agents.
- [ ] Add a post-bootstrap verification sequence for port 505, `can_rescan`, inventory mode/order, and the non-mutating authenticated `UNKNOWN_SERVER` POST readiness probe. Keep any real rescan as a separate optional operator action.
- [ ] Run repository documentation/policy tests.
- [ ] Commit documentation.

## Task 8: Full review, CI, and Live bootstrap

**Files:** all changed files.

- [ ] Run `python3 -m unittest tests.test_storage_release_puller -v`.
- [ ] Run `make test-storage`.
- [ ] Run `make test`.
- [ ] Run `python3 scripts/validate_workflows.py .github/workflows`.
- [ ] Run `git diff --check`.
- [ ] Request independent code/security review and fix all blocking findings.
- [ ] Push the exact reviewed SHA to `main` and wait for `ci/required` success.
- [ ] When the Storage host route is reachable, inspect existing dashboard/proxy processes and configuration without printing secrets.
- [ ] Run installer dry-run on the host, then bootstrap the approved SHA.
- [ ] Confirm the tmux proxy is replaced by `storage-viz-proxy.service` and no unrelated process is terminated.
- [ ] Verify active SHA, port 505, production inventory order, `can_rescan: true`, and a real manual rescan lifecycle.
- [ ] Confirm a no-change puller run exits `already-current` and record rollback/status commands.
