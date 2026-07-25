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

## Continuous integration and GPU Live release

Local development is the default path. Contributors may open pull requests when review is useful, and trusted team members may also push directly to `main`.

The supported GPU Live release contract is:

`local development -> optional PR or trusted direct main push -> main CI -> outbound server puller -> exact successful SHA live activation`

Automatic deployment does not accept GitHub-hosted inbound SSH. A systemd timer on the server uses a five-minute base interval with bounded jitter, reads public GitHub API evidence for the current `main` SHA, reuses `scripts/authorize_gpu_release.py` to require successful `ci/required` on that exact SHA, builds from a clean exact-SHA checkout as the dedicated non-login builder, then runs the local `activate-release.sh` path as `gpu-deploy-live` (`upload` -> `activate` -> `status`). Failed CI, a changed `main`, build failure, authorization failure, or activation failure leaves the current Live release unchanged; authorized release failures use persistent exponential retry backoff rather than restarting Live every timer tick.

Storage is independent and is not deployed by the GPU Live path. There is no self-hosted runner and no always-on development server.

See `docs/operations/github-cicd.md` for the outbound-only deployment contract, migration notes, and emergency status/rollback boundary.

## Repository rules

- Keep generated, collected, runtime, cache, database, browser-output, virtual-environment, and dependency-install data out of Git.
- Privacy-safe Storage sample fixtures under `apps/storage-monitor/data/` are allowed because they are reviewed sample data, not collected runtime snapshots.
- Keep setup, tests, and runtime assumptions application-local unless a root migration or governance file explicitly says otherwise.
- Direct pushes to `main` and optional merged PRs are supported entry points into the GPU Live contract; the same fail-closed rule applies after either path.
- Production GPU deployment is server-pulled and outbound-only. The GitHub-hosted SSH workflow has been removed; its obsolete GitHub environment/secrets are deleted after outbound rollout verification.

See `docs/history-migration.md` for migration evidence and history-preservation details.
