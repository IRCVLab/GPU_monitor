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

Pull requests receive one branch-protection-friendly required check named `ci/required`. The workflow also classifies changed paths so documentation-only changes skip GPU and Storage application suites while still running repository contract validation. Pushes to feature branches do not deploy because CI only runs on pull requests, pushes to `main`, or manual dispatch. Production deployment is intentionally disabled until repository protection is available. See `docs/operations/github-cicd.md` for the read-only deployment prerequisite checker, current branch-protection blocker, runner policy, and cutover guardrails.

## Repository rules

- Keep generated, collected, runtime, cache, database, browser-output, virtual-environment, and dependency-install data out of Git.
- Privacy-safe Storage sample fixtures under `apps/storage-monitor/data/` are allowed because they are reviewed sample data, not collected runtime snapshots.
- Keep setup, tests, and runtime assumptions application-local unless a root migration or governance file explicitly says otherwise.
- Merging this foundation to `main` is the later authorization point for production deployment planning.
- Production deployment is not enabled by this foundation plan, this Makefile, or the migration history assembly.

See `docs/history-migration.md` for migration evidence and history-preservation details.
