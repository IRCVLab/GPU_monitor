# storage-viz operations

`install.sh` is the production entry point for a generic Linux server. It builds
`scanner/hstscan`, writes systemd units, serves the viewer with `viewer/serve.py`,
and schedules a root scan timer.

## Dry-run first

Run this on any checkout before touching systemd:

```bash
./install.sh --dry-run
```

Dry-run mode builds the scanner, writes units to a temporary directory, runs
`systemd-analyze verify` when available, and never calls `systemctl` or starts a
scan.

## Runtime configuration

All deployment-specific values can be overridden without editing source:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORAGE_VIZ_ROOT` | directory containing `install.sh` | Project/clone root. |
| `STORAGE_VIZ_DATA_DIR` | `$STORAGE_VIZ_ROOT/data` | Directory for `<hostname>.json` scan outputs. |
| `STORAGE_VIZ_SCAN_TARGETS` | `/ /data /data1 /data3` | Space-separated scanner target list. Quote it as one shell value. |
| `STORAGE_VIZ_PORT` | `8088` | Dashboard port. |
| `STORAGE_VIZ_BIND` | `0.0.0.0` | Dashboard bind address. |
| `STORAGE_VIZ_SERVE_USER` | `$SUDO_USER` or `root` | User for `storage-viz-http.service`; HTTP serving is manual-rescan-only by default. |
| `STORAGE_VIZ_SCAN_TIME` | `02:00` | Nightly timer time in `HH:MM`. |
| `UNIT_DIR` | `/etc/systemd/system` | Unit output directory; dry-runs use a temp dir unless this is set. |

Example:

```bash
sudo STORAGE_VIZ_ROOT=/opt/storage-viz \
  STORAGE_VIZ_DATA_DIR=/var/lib/storage-viz \
  STORAGE_VIZ_SCAN_TARGETS='/ /scratch /data' \
  STORAGE_VIZ_PORT=8090 \
  ./install.sh
```

## Services

`storage-viz-http.service` runs `viewer/serve.py` instead of plain
`python -m http.server`. This keeps data routing and the Rescan button truthful:

- `GET /capabilities` reports whether server-side rescan is supported.
- `GET /rescan-status` returns progress/capability metadata, targets, output path, and any scanner error.
- By default `POST /rescan` returns `503` with a manual-only message, so the HTTP
  service never starts privileged scans accidentally.
- `GET /data/<host>.json` is served from `STORAGE_VIZ_DATA_DIR`, so the data directory
  does not have to be inside `viewer/`.

`storage-viz-scan.service` performs one scheduled scan. `storage-viz-scan.timer`
starts it nightly.

Useful commands:

```bash
sudo systemctl status storage-viz-http.service storage-viz-scan.timer
sudo systemctl start storage-viz-scan.service
journalctl -u storage-viz-http.service -u storage-viz-scan.service
```

## Privilege model

Root scans are recommended for complete accounting. Keep scheduled/manual scans in
`storage-viz-scan.service`; the HTTP service should normally stay unprivileged and
manual-only. To expose server-side rescans intentionally, set either
`STORAGE_VIZ_ENABLE_RESCAN=1` or `STORAGE_VIZ_RESCAN_COMMAND` in the HTTP service
environment, then review the operational/security tradeoff before binding it to a
shared network.

## Optional AI Cleanup Advisor

The AI Cleanup Advisor is disabled by default. Enable it only on a trusted local
deployment after reviewing privacy and latency expectations. Production scan
snapshots can contain private paths, user names, project names, and activity
patterns, so the recommended path is a local model endpoint.

### Core AI environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORAGE_VIZ_AI_ENABLED` | unset/disabled | Enables `/ai/status` and `/ai/recommend` behavior. Without this, AI status should report disabled and the dashboard remains usable. |
| `STORAGE_VIZ_AI_PROVIDER` | `ollama` when enabled | `ollama`, `openai-compatible`, or `mock`. Tests should use `mock` or rule-only behavior. |
| `STORAGE_VIZ_AI_ENDPOINT` | `http://127.0.0.1:11434` for Ollama | Local model server endpoint. Prefer loopback or a trusted internal gateway. |
| `STORAGE_VIZ_AI_MODEL` | `qwen3.6:27b` | Recommended default local GPU advisor model; override per server. |
| `STORAGE_VIZ_AI_OUTPUT_LANGUAGE` | `ko` | Final user-facing advisor language. The current UI sends `language=ko` per request. |
| `STORAGE_VIZ_AI_TIMEOUT_SEC` | implementation default | Timeout for model requests. Keep bounded so the dashboard stays responsive. |
| `STORAGE_VIZ_AI_CACHE_DIR` | implementation default | Cache for validated advisor results. Do not place generated cache files in tracked `data/`. |

Recommended Ollama setup:

```bash
ollama pull qwen3.6:27b

STORAGE_VIZ_AI_ENABLED=1 \
STORAGE_VIZ_AI_PROVIDER=ollama \
STORAGE_VIZ_AI_ENDPOINT=http://127.0.0.1:11434 \
STORAGE_VIZ_AI_MODEL=qwen3.6:27b \
python3 viewer/serve.py 8088
```

OpenAI-compatible local endpoint example for vLLM, SGLang, llama.cpp server, or
an internal gateway:

```bash
STORAGE_VIZ_AI_ENABLED=1 \
STORAGE_VIZ_AI_PROVIDER=openai-compatible \
STORAGE_VIZ_AI_ENDPOINT=http://127.0.0.1:8000/v1 \
STORAGE_VIZ_AI_MODEL=qwen3.6:27b \
python3 viewer/serve.py 8088
```

Model tiers:

- Fast fallback: `qwen3.5:9b` or another 7B-9B instruct model when latency is
  more important than explanation quality.
- Default GPU advisor: `qwen3.6:27b`.
- High-quality batch mode: `qwen3.5:35b` or `llama3.3:70b` only when VRAM and
  latency budgets allow.

The product must remain testable without a live model runtime. Use rule-only or
mock mode for CI and development checks.

For operator-facing deployments, do not run the dashboard with
`STORAGE_VIZ_AI_PROVIDER=mock`; mock mode is only a deterministic fixture. If the
local LLM service is down, run with the real provider anyway. The advisor will
show a Korean rule-only fallback plus the model connection error instead of
pretending that LLM analysis succeeded.

The local LLM path is two-pass: an English analyzer pass produces structured
recommendations from bounded evidence, then a Korean translator pass localizes
only the user-facing text while preserving ids, paths, actions, risk, confidence,
and evidence.

### Read-only inspection environment

Live filesystem evidence is separate from model enablement and remains disabled
by default. Turn it on only when snapshot evidence is not enough and the server
policy is clear.

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORAGE_VIZ_AI_READONLY_INSPECTION` | unset/disabled | Enables server-owned live metadata inspection. |
| `STORAGE_VIZ_AI_ALLOWED_ROOTS` | unset | Comma-separated roots eligible for inspection, for example `/home,/data,/data1`. |
| `STORAGE_VIZ_AI_MAX_INSPECT_PATHS` | implementation default | Max paths inspected per request. |
| `STORAGE_VIZ_AI_MAX_INSPECT_DEPTH` | implementation default | Max shallow traversal depth. |
| `STORAGE_VIZ_AI_INSPECT_TIMEOUT_SEC` | implementation default | Hard timeout for inspection. |

Inspection safety requirements:

- Reject `/`, one-segment top-level paths such as `/home`, system-critical
  paths, relative paths, NUL-byte paths, and symlinks that escape allowed roots.
- Return metadata only by default: stat data, shallow entry counts, aggregate
  size summaries, mtime ranges, and extension/type counts.
- Do not read file contents, run shell strings, use `sudo`, write files, delete,
  move, chmod/chown, or traverse unbounded symlink trees.
- Enforce timeout, depth, path-count, entry-count, and returned-evidence limits.

### Advisor troubleshooting

- `/ai/status` disabled: verify `STORAGE_VIZ_AI_ENABLED=1` is present in the HTTP
  service environment and restart the service.
- Model timeout/error: keep the dashboard usable, fall back to rule-only results
  where available, and check the local model server logs.
- No badges: confirm `/ai/recommend` returns `schema_version: 1`
  recommendations that pass `docs/ai-advisor-schema.md` validation and that
  exclusions are not hiding them.
- Unsafe recommendation dropped: inspect validation errors first; the validator
  should reject root, mount, top-level, system, relative, and malformed paths.
