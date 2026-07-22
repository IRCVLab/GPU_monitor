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
