# Development guide

This repository is a hybrid monorepo for two independently deployable monitoring applications:

- `apps/gpu-monitor` — GPU Monitor frontend, FastAPI backend, Slack bridge, and GPU-specific scripts.
- `apps/storage-monitor` — Storage Monitor viewer, collector, scanner, agent, and deployment templates.

The foundation migration enabled local validation and documentation. GPU Live deployment is documented separately and is outbound-only from the server: a five-minute systemd timer polls public GitHub API evidence, authorizes the exact current `main` SHA, builds it in an isolated non-login builder checkout, and activates it locally. It does not accept GitHub-hosted inbound SSH deployment, register self-hosted production runners, keep a permanent development server online, or change production ports during ordinary verification.

## Safety rules

- Work in application-local directories unless a root-level contract requires otherwise.
- Do not commit real `.env` files, local databases, scan output, runtime snapshots, `node_modules`, virtual environments, build output, cache directories, tmux state, or machine-specific configuration.
- Root documentation-only changes do not deploy applications.
- Deployment planning, CI registration, and service installation are separate reviewed changes; self-hosted production runners are not used.
- The GPU runtime scripts manage tmux sessions and ports; do not run them during ordinary repository verification.
- Storage agent/dashboard install and deploy scripts are operational tools; ordinary verification uses their dry-run and contract tests, not live installs.

## Pull request CI

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatch. Every pull request reports `ci/required`; path-aware GPU and Storage jobs run only when their application paths or shared workflow inputs change, so documentation-only changes skip the app suites. `ci/required` protects repository contracts for all supported changes.

The supported deployment contract is:

`local development -> optional PR or trusted direct main push -> main CI -> outbound server puller -> exact successful SHA live activation`

This means the server, not a GitHub-hosted deployment job, decides whether to activate GPU Live. The server-side puller reads public GitHub API evidence for the current `main` SHA, requires the successful `ci` workflow and latest successful `ci/required` check for that exact SHA through `scripts/authorize_gpu_release.py`, and rechecks that `main` still equals that SHA before activation. This is a trusted-team policy, not protection against malicious or compromised trusted writers; branch protection with required review is the stronger future control.

Pull requests are optional. A failed `main` CI run does not change the live service, although the failed commit may remain in `main` history. If `main` advances while a candidate is being checked or built, the stale candidate is rejected and Live remains unchanged.

## Root verification

Run the full supported foundation check from the repository root:

```bash
make verify
```

`make verify` runs:

1. repository layout tests;
2. history inventory tests;
3. GPU frontend `npm run check`;
4. GPU frontend production build;
5. GPU production runtime proxy tests against that build;
6. GPU backend unit tests;
7. Storage tests in a disposable no-hardlinks clone under `/tmp`;
8. JavaScript syntax checks for Storage viewer files;
9. Storage deploy-script contract tests;
10. Linux-only scanner checks when running on Linux, otherwise an explicit macOS skip covered by prior Linux verification;
11. `git diff --check`.

On a fresh host, install application dependencies first:

```bash
cd apps/gpu-monitor/frontend
npm ci

cd ../..
python3.12 -m venv apps/gpu-monitor/.venv
. apps/gpu-monitor/.venv/bin/activate
python -m pip install -r apps/gpu-monitor/backend/requirements.txt pytest
cd ../..
PATH="$PWD/apps/gpu-monitor/.venv/bin:$PATH" make verify
```

The app-local virtual environment is ignored by Git.

## GPU Monitor development

Frontend checks:

```bash
cd apps/gpu-monitor/frontend
npm ci
npm run check
npm run build
```

Backend tests:

```bash
cd apps/gpu-monitor
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python -m unittest discover -s backend/tests -v
```

Runtime scripts are app-local:

- `apps/gpu-monitor/scripts/run_monitoring.sh`
- `apps/gpu-monitor/scripts/run_development.sh`

Use them only when explicitly inspecting or changing a local runtime stack.

## Storage Monitor development

Run Storage checks through the root target so generated artifacts stay in a disposable clone:

```bash
make test-storage
```

For app-local read-only or syntax work:

```bash
cd apps/storage-monitor
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check
bash deploy/test_deploy_scripts.sh
```

The scanner uses Linux-specific `SYS_getdents64` behavior. On non-Linux hosts, the root target skips scanner execution explicitly; Linux verification remains required before scanner changes are accepted.

## History and migration evidence

`docs/history-migration.md` records redacted migration evidence and points to ignored local machine-readable artifacts under the planning worktree. Those artifacts are for verification and audit, not for publication, and must not include secrets.

## GPU Live outbound deployment operation

GPU Live deployment is server-pulled and outbound-only:

1. The installer places the puller, the local activation scripts, the canonical authorizer, and systemd service/timer units on the server.
2. The installer does not enable or start `gpu-monitor-release-puller.service` or `gpu-monitor-release-puller.timer`.
3. After installation, the operator verifies files, identities, Node runtime, Live secrets in `/etc/gpu-monitor/live.env`, and manual status/rollback readiness.
4. Only after that verification does the operator explicitly enable/start the timer.

The puller uses a persistent five-minute calendar cadence with bounded jitter after it is enabled. For each observed current `main` SHA, it:

- fetches public GitHub API evidence for the `ci` workflow run and check runs;
- reuses `scripts/authorize_gpu_release.py` with `--required-check ci/required`;
- fails closed unless the exact current `main` SHA has successful `ci/required`;
- builds the exact SHA from a clean checkout as the dedicated non-login `gpu-monitor-builder` user;
- keeps the builder isolated from `/etc/gpu-monitor/live.env` and deploy credentials;
- records an authorized SHA that fails checkout/build/upload/activation and exponentially backs off retries from 15 minutes to a 6-hour ceiling, while a new `main` SHA clears the failure state;
- discards the exact inactive artifact under the activation lock when the final `main`/authorization recheck fails after upload;
- hands the artifact to local activation as `gpu-deploy-live`:

  ```text
  upload live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
  activate live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
  status live
  ```

A failed CI run, missing or failed `ci/required`, changed `main`, public API failure, authorization denial, checkout/build failure, digest/manifest mismatch, activation failure, or health-check failure leaves the current Live release unchanged.

Operators can inspect `/var/lib/gpu-monitor/puller/failed-release.json`. Deleting only that file and starting `gpu-monitor-release-puller.service` requests a manual retry of the unchanged SHA; normal retries remain automatic and bounded.

The old GitHub-hosted SSH deployment workflow has been removed. Its obsolete `gpu-live` environment secrets are retained only until outbound rollout verification and are not the current automatic deployment transport. The existing SSH forced-command path may remain only for manual emergency inspection and rollback:

```text
status live
rollback live
```

Storage remains independent and is not deployed by the GPU Live puller. There is no self-hosted runner and no permanent development deployment.

## Migration notes

The previous GitHub-hosted SSH deployment design failed operationally because the campus firewall blocked inbound SSH from GitHub-hosted runners to the GPU server. The new design removes that inbound path: the server initiates all GitHub communication over outbound HTTPS to the public GitHub API and performs activation locally.

Migration sequence:

1. Install or update server assets for the outbound puller, builder account, canonical authorizer, activation scripts, and systemd units.
2. Verify the installer did not enable/start the puller timer or service.
3. Verify manual `status live` and `rollback live` remain available through the forced-command emergency path.
4. Confirm the GitHub-hosted SSH deployment workflow is absent, then delete the obsolete `gpu-live` environment secrets/environment after outbound rollout verification.
5. Enable/start `gpu-monitor-release-puller.timer` explicitly after operator verification.
6. Observe the first real activation from `main`; until that rollout completes, do not record success evidence.
