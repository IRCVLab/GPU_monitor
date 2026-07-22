# Architecture

The monitoring platform uses one source-control boundary with two independent application/runtime boundaries.

```text
apps/
  gpu-monitor/       GPU dashboard, FastAPI API, Slack bridge, GPU collectors
  storage-monitor/   Storage dashboard, collector, scanner, agent, deploy templates
docs/                Architecture, development, and migration records
scripts/             Repository-level verification helpers
.github/             Ownership metadata only at the foundation stage
```

## Product boundaries

### GPU Monitor

GPU Monitor owns:

- GPU collection and availability semantics;
- Svelte frontend and FastAPI backend;
- Slack bridge behavior;
- GPU-specific configuration, tests, ports, scripts, and release history.

Production-style app-local ports are defined by `apps/gpu-monitor/scripts/run_monitoring.sh`:

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

Deployment automation is intentionally not configured by this foundation task. Before deployment work begins, separate plans must cover:

1. CI and contribution controls;
2. central-service deployment;
3. Storage-agent rollout.

Until those plans are reviewed and implemented, this repository is a verified local monorepo foundation, not a production deployment system.
