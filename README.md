# Monitoring Platform Monorepo

This repository contains one source history for two independent monitoring products:

- `apps/gpu-monitor` — GPU monitoring dashboard and FastAPI backend.
- `apps/storage-monitor` — storage inventory scanner, collector, and dashboard.

The products share repository governance, migration tests, and documentation only. Application code must remain app-local: do not add cross-imports between `apps/gpu-monitor` and `apps/storage-monitor`.

## Local setup

Run setup and checks from each application directory.

### GPU monitor

```bash
cd apps/gpu-monitor/backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
npm run check
npm run build
```

### Storage monitor

```bash
cd apps/storage-monitor
python3.12 -m unittest \
  agent.test_block_media \
  agent.test_mount_policy \
  agent.test_scan_runner \
  collector.test_inventory \
  collector.test_jobs \
  collector.test_service \
  collector.test_snapshot \
  collector.test_store \
  collector.test_transport \
  viewer.test_serve
```

For a local sample dashboard only:

```bash
cd apps/storage-monitor
STORAGE_VIZ_DEV_SAMPLE_DIR="$(pwd)/data" \
STORAGE_VIZ_BIND=127.0.0.1 \
STORAGE_VIZ_PORT=8088 \
python3 viewer/serve.py
```

## Continuous integration

Local development is the default and supported path. Contributors may use optional pull requests or direct pushes to `main`. The supported release contract is:

`local development -> optional PR or direct main push -> main CI -> exact successful SHA live deployment`

`ci/required` gates required repository checks on CI; a failed `main` CI leaves the current live release unchanged even though the failed commit remains in Git history.

See `docs/operations/github-cicd.md` for the live authorization contract, current status checks, secrets/runner constraints, and status/rollback commands.

## Repository rules

- Keep generated, collected, runtime, cache, database, browser-output, virtual-environment, and dependency-install data out of Git.
- Privacy-safe Storage sample fixtures under `apps/storage-monitor/data/` are allowed because they are reviewed sample data, not collected runtime snapshots.
- Keep setup, tests, and runtime assumptions application-local unless a root migration or governance file explicitly says otherwise.
- Direct pushes to `main` (or optional merged PRs) are the supported entry points into this contract; the same failure rule applies if CI fails after either path.
- Production deployment follows the optional PR/direct `main` contract and the successful same-repository `main` deployment rules in `docs/operations/github-cicd.md`.

See `docs/history-migration.md` for migration evidence and history-preservation details.
