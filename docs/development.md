# Development guide

This repository is a hybrid monorepo for two independently deployable monitoring applications:

- `apps/gpu-monitor` — GPU Monitor frontend, FastAPI backend, Slack bridge, and GPU-specific scripts.
- `apps/storage-monitor` — Storage Monitor viewer, collector, scanner, agent, and deployment templates.

The foundation migration enabled local validation and documentation. The trusted-team GPU release workflow is documented separately and uses a GitHub-hosted live deployment plus a server-side forced-command wrapper for live release activation. It does not register self-hosted production runners, restart live services during ordinary verification, or change production ports.

## Safety rules

- Work in application-local directories unless a root-level contract requires otherwise.
- Do not commit real `.env` files, local databases, scan output, runtime snapshots, `node_modules`, virtual environments, build output, cache directories, tmux state, or machine-specific configuration.
- Root documentation-only changes do not deploy applications.
- Deployment planning, CI registration, and service installation are separate reviewed changes; self-hosted production runners remain disabled while branch protection is unavailable.
- The GPU runtime scripts manage tmux sessions and ports; do not run them during ordinary repository verification.
- Storage agent/dashboard install and deploy scripts are operational tools; ordinary verification uses their dry-run and contract tests, not live installs.

## Pull request CI

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatch. Every pull request reports `ci/required`; path-aware GPU and Storage jobs run only when their application paths or shared workflow inputs change, so documentation-only changes skip the app suites. `ci/required` protects repository contracts for all supported changes.

The supported deployment contract is:

`local development -> optional PR or direct main push -> main CI -> exact successful SHA live deployment`

This means a successful same-repository `main` push can authorize that exact SHA when the triggering workflow is `.github/workflows/ci.yml`, the latest `ci/required` check succeeded for that SHA, and an immediate current `main` head recheck still equals that SHA. This is a trusted-team policy, not protection against malicious or compromised trusted writers; branch protection with required review is the stronger future control.

Pull requests are optional. A failed `main` CI run does not change the live service, although the failed commit may remain in `main` history.

## Root verification

Run the full supported foundation check from the repository root:

```bash
make verify
```

`make verify` runs:

1. repository layout tests;
2. history inventory tests;
3. GPU frontend `npm run check`;
4. GPU backend unit tests;
5. GPU frontend production build;
6. Storage tests in a disposable no-hardlinks clone under `/tmp`;
7. JavaScript syntax checks for Storage viewer files;
8. Storage deploy-script contract tests;
9. Linux-only scanner checks when running on Linux, otherwise an explicit macOS skip covered by prior Linux verification;
10. `git diff --check`.

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

## GPU deployment workflow operation

Live deployment is automatic after the `ci` workflow completes successfully for `main` with the required provenance conditions:

- `workflow name ci`
- `event push`
- `branch main`
- `status completed`
- `conclusion success`
- `head repository IRCVLab/GPU_monitor`
- workflow `path: .github/workflows/ci.yml`
- latest `ci/required` successful for the exact head SHA
- immediate current `main` head recheck still equals that SHA

The release then builds and deploys that exact SHA in the `gpu-live` environment.

The live workflow requires these exact environment secrets:

```text
GPU_DEPLOY_HOST
GPU_DEPLOY_PORT
GPU_DEPLOY_USER
GPU_DEPLOY_SSH_KEY
GPU_DEPLOY_KNOWN_HOSTS
```

The workflow validates SHA, artifact digest, port, host, user, and `user@host` target before SSH. SSH uses `BatchMode`, strict host verification, and `StrictHostKeyChecking` with the environment-provided `known_hosts`. Remote commands are limited to:

```text
upload live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
status live
```

Rollback is a deliberate operator action:

```text
rollback live
```

A failed live deployment or live health check leaves the current live release unchanged.
