# Development guide

This repository is a hybrid monorepo for two independently deployable monitoring applications:

- `apps/gpu-monitor` — GPU Monitor frontend, FastAPI backend, Slack bridge, and GPU-specific scripts.
- `apps/storage-monitor` — Storage Monitor viewer, collector, scanner, agent, and deployment templates.

The foundation migration intentionally enables local validation and documentation only. It does not enable deployment, register runners, restart live services, or change production ports.

## Safety rules

- Work in application-local directories unless a root-level contract requires otherwise.
- Do not commit real `.env` files, local databases, scan output, runtime snapshots, `node_modules`, virtual environments, build output, cache directories, tmux state, or machine-specific configuration.
- Root documentation-only changes do not deploy applications.
- Deployment planning, CI registration, runner setup, and service installation are separate future plans.
- The GPU runtime scripts manage tmux sessions and ports; do not run them during ordinary repository verification.
- Storage agent/dashboard install and deploy scripts are operational tools; ordinary verification uses their dry-run and contract tests, not live installs.

## Pull request CI

GitHub Actions runs on pull requests, pushes to `main`, and manual dispatch. Every pull request reports `ci/required`; path-aware GPU and Storage jobs run only when their application paths or shared workflow inputs change, so documentation-only changes skip the app suites. Pushes to feature branches do not deploy or run the production path. Production deployment remains intentionally disabled until repository protection is available.

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
