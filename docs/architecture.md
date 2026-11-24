# Architecture

The monitoring platform uses one source-control boundary with two independent application/runtime boundaries.

```text
apps/
  gpu-monitor/       GPU dashboard, FastAPI API, Slack bridge, GPU collectors
  storage-monitor/   Storage dashboard, collector, scanner, agent, deploy templates
docs/                Architecture, development, and migration records
scripts/             Repository-level verification helpers
.github/             Ownership metadata and path-aware CI
```

## Product boundaries

### GPU Monitor

GPU Monitor owns:

- GPU collection and availability semantics;
- Svelte frontend and FastAPI backend;
- Slack bridge behavior;
- GPU-specific configuration, tests, ports, scripts, and release history.

The managed Live services use these default ports:

- backend API: `127.0.0.1:8001`;
- frontend preview: `0.0.0.0:5173`;
- Slack bridge: `0.0.0.0:8000`.

Development ports are isolated by `apps/gpu-monitor/scripts/run_development.sh` and must not overwrite the live stack.

### Storage Monitor

Storage Monitor owns:

- storage dashboard/viewer behavior;
- collector and API behavior;
- scanner and per-host agent behavior;
- Storage-specific tests, deployment templates, service contracts, snapshots, and release history.

Storage central dashboard deployment and per-host scanner/agent rollout are separate operational targets. Foundation migration does not restart either target.

## Shared boundary

At the foundation stage, shared code is intentionally minimal. Root files provide repository contracts, ownership, security guidance, and verification entry points. Shared backend state, shared application models, cross-application API clients, and framework-specific shared UI components are not introduced.

Future shared packages may be added only when repeated duplication proves the maintenance cost and compatible runtime contracts exist.

## Refactoring sequence

Keep the app boundary. GPU and Storage have different data stores, release artifacts,
and activation checks. Root commands own repository verification. Each app owns its
runtime dependencies.

1. **GPU backend.** Keep HTTP validation and authorization in routers. A Power Flow
   diagram is a saved pointer to immutable revisions. `power_flow/diagrams.py`
   owns those records and selects the revision at the requested time.
   `power_flow/service.py` reads telemetry and computes the readings for that
   revision. `telemetry_time.py` owns UTC conversion for API timestamps and
   SQLite history columns. Check create, edit conflicts, historical selection,
   and peak coverage with the Power Flow API tests before moving the next
   boundary.
2. **GPU frontend.** `dashboardRefresh.svelte.ts` owns catalog and status
   requests, retries, and polling timers. `ws.ts` publishes live updates to
   the same server store. The dashboard route binds the resulting state to
   its tabs, cards, and header. Check polling and browser rendering when
   changing these boundaries.
3. **Storage runtime.** Trace one rescan from `agent/scan_runner.py` through
   `collector/jobs.py` and `collector/store.py` to `viewer/scan-status.js`.
   The collector owns job completion. `viewer/scan-status.js` interprets the
   reported job state and `viewer/app.js` handles polling and rendering.
   The agent executes scans. Exercise a rescan through the lifecycle tests
   and viewer when changing the boundary.
4. **Release tools.** Share a rule only when both pullers have the same inputs
   and outcomes. Keep per-app packaging, activation, rollback, and restart
   decisions in each app. Verify the exact-SHA and unchanged-payload tests
   before changing installed tools.

Use independently reviewable commits. Preserve public routes, payloads, SQLite
tables, deployment paths, and active services during development.

## History architecture

The migration preserves source history in two ways:

1. Original source refs are retained as archive refs and checkpoint tags.
2. Active GPU development and active Storage histories are rewritten under path prefixes and merged into `main`.

Primary checkpoint refs:

| Source | Checkpoint/archive ref | Object ID |
| --- | --- | --- |
| GPU development | `refs/tags/pre-monorepo-gpu-dev` | `64c4b838d6e1293daf52ab0039084a2b9f84bc59` |
| GPU live | `refs/tags/pre-monorepo-gpu-live` | `f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce` |
| Storage active | `refs/tags/pre-monorepo-storage` | `0d7e1dcf2cfd9cfe819851e37384e8bb80930365` |
| Storage checkpoint | `refs/heads/archive/storage/checkpoint/ai-advisor-workspace-20260717` | `0685b5f2161041ccce7025a8e5d2b4dd140d6590` |

The live GPU branch remains reachable under `refs/heads/archive/gpu-live/main`; it is not merged into the active app history.

## Deployment architecture status

GPU Live deployment is server-pulled and outbound-only after promotion. The architecture is:

1. local development;
2. optional pull request or trusted direct `main` push;
3. path-aware `main` CI with `ci/required`;
4. exact-SHA authorization through `scripts/authorize_gpu_release.py`;
5. clean exact-SHA GPU artifact build by `gpu-monitor-builder`;
6. candidate copy validation from a disposable online backup with collectors and Slack disabled;
7. local activation by `gpu-deploy-live`;
8. health checks against managed systemd units and the registered-server floor.

Live data remains server-local and outside release artifacts. Production uses:

```text
MONITORING_EXPECTED_SERVER_COUNT=9
MONITORING_DATABASE_BACKUP_DIR=/var/lib/gpu-monitor/live/backups
MONITORING_DATABASE_BACKUP_KEEP=5
```

The active release is inspected with `status live`, the `current` generation pointer under `/srv/gpu-monitor/live`, and `/var/lib/gpu-monitor/puller/current-live-sha`. Operators inspect service state with `systemctl status gpu-monitor-release-puller.timer`, `systemctl status gpu-monitor-backend@live.service`, `systemctl status gpu-monitor-frontend@live.service`, and `systemctl status gpu-monitor-bridge@live.service`; puller logs come from `journalctl -u gpu-monitor-release-puller.service`.

If a newer successful `main` SHA builds to the same GPU release digest, the puller records the SHA and does not restart Live. Documentation-only and Storage-only changes therefore cannot restart GPU Live when the GPU runtime payload is unchanged.

The Storage puller owns Storage activation. A Storage-only commit can advance
the GPU puller's recorded SHA when the verified GPU artifact digest matches
the live deployment.

The legacy tmux stack is retained only as the first managed cutover fallback until the first promoted release and one subsequent no-op puller cycle are verified. After that first-cutover boundary, GPU rollback uses immutable release pointers and the exact emergency command `rollback live`; emergency inspection uses `status live`.
