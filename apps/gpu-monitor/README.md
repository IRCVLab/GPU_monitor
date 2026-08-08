# GPU Monitor

Application-local GPU monitoring stack with an independent Python backend and Svelte frontend.

## Local checks

Run commands from the repository root through the delegating Makefile:

```bash
make test-gpu
make build-gpu
```

The root Makefile delegates into `apps/gpu-monitor` and `apps/gpu-monitor/frontend`; it does not require a shared monorepo package manager.

## Clean baseline

```bash
cd apps/gpu-monitor/frontend
npm ci
npm run check
npm run build
cd ..
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python -m unittest discover -s backend/tests -v
deactivate
rm -rf .venv
```

## App-local scripts

Operational scripts live under `apps/gpu-monitor/scripts/` and resolve the application root from their own file location, so callers do not need to run them from a particular working directory.

```bash
cd apps/gpu-monitor
./scripts/run_monitoring.sh status
./scripts/run_development.sh status
```

These scripts manage tmux sessions and application ports. Do not run them as part of repository verification unless you explicitly intend to inspect or change a local runtime stack.

## Production release boundary

GPU Live promotion follows the repository contract:

`local development -> optional PR or trusted direct main push -> main CI -> outbound server puller -> exact successful SHA live activation`

There is no permanent Dev server. Local tmux scripts are for inspection and development only; production Live is managed by systemd after the guarded cutover.

The Live environment must keep production database invariants in `/etc/gpu-monitor/live.env`:

```text
MONITORING_EXPECTED_SERVER_COUNT=9
MONITORING_DATABASE_BACKUP_DIR=/var/lib/gpu-monitor/live/backups
MONITORING_DATABASE_BACKUP_KEEP=5
```

Before first managed activation, validate a candidate copy made from a disposable online backup of the restored Live SQLite database. Run candidate services on non-production ports with collectors and Slack disabled, verify the nine registered server identities and readable notes, then publish the verified database to the managed Live path.

Documentation-only or Storage-only commits can pass CI without changing the GPU runtime payload. If the candidate artifact has the same GPU release digest as the active release, the puller advances `current-live-sha` and does not restart Live.

Useful server inspection commands after installation:

```bash
sudo systemctl status gpu-monitor-release-puller.timer
sudo systemctl status gpu-monitor-release-puller.service
sudo journalctl -u gpu-monitor-release-puller.service
sudo systemctl status gpu-monitor-backend@live.service
sudo systemctl status gpu-monitor-frontend@live.service
sudo systemctl status gpu-monitor-bridge@live.service
sudo -u gpu-deploy-live /usr/local/libexec/activate-release.sh status live
```

During the first managed cutover only, the legacy tmux stack remains the emergency fallback until the first promoted release and one subsequent no-op puller cycle are verified. The exact manual emergency forced-command forms are `status live` and `rollback live`; they are not the automatic deployment transport.
