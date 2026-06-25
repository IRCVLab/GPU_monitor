# storage-viz — WizTree-style storage visualizer for lab servers

Fast parallel disk scanner (C) + offline interactive treemap dashboard (HTML/ECharts).
Built for `hinton`, designed to be portable to any Linux lab server.

> **Why:** the server fills up and nobody can see *who/what* is using space. This tool lets
> every lab member open a browser, see their own (and everyone's) usage, find large/old/cache
> files, and clean up — instead of one admin guessing with `du`.

## Components

| Part | Path | What it is |
|------|------|------------|
| Scanner | `scanner/hstscan.c` | C11 + pthreads parallel directory walker → one JSON snapshot. Zero external deps. |
| Viewer | `viewer/index.html` + `viewer/*.js` + `viewer/echarts.min.js` | Offline dashboard (treemap / users / top files / stale). |
| AI Advisor | `docs/ai-cleanup-advisor.md`, `docs/ai-advisor-schema.md`, optional `/ai/*` runtime endpoints | Optional local-first cleanup advice: badges/details/exclusions from snapshot evidence. Disabled by default. |
| Data | `data/<hostname>.json` | Scan output the viewer reads. Hostname-tagged for future multi-server. |
| Install | `install.sh` | Build + install binary, systemd timer, LAN serving, dry-run verification. |

## Quick start (this server)

```bash
# 1. build the scanner
cd scanner && make && cd ..

# 2. run a scan (root recommended — see note) → writes data/<hostname>.json
sudo ./scanner/hstscan            # defaults: scan / /data /data1 /data3

# 3. serve the dashboard on the LAN
python3 viewer/serve.py 8088      # run from the project root; open http://<host>:8088/
```

## Deploy on another server

The deployment paths are configurable with environment variables (`STORAGE_VIZ_ROOT`,
`STORAGE_VIZ_DATA_DIR`, `STORAGE_VIZ_SCAN_TARGETS`, `STORAGE_VIZ_PORT`, `STORAGE_VIZ_BIND`).
Mount metadata is discovered from `/proc/self/mountinfo` and users from the password database.
To add a server:

```bash
git clone <repo> && cd storage-viz
./install.sh --dry-run                # build + unit syntax check, no privileged systemctl actions
cd scanner && make
sudo ./hstscan --out ../data/$(hostname).json   # produces data/<that-host>.json
```

Drop that host's JSON into the configured data directory and add it to `data/hosts.json` —
the same dashboard renders any server's snapshot.

## Root access

The scanner runs fine as a normal user but can only measure what it can read; unreadable
directories (other users' private homes, `/var/lib/docker`, etc.) are listed under `blocked[]`
and the dashboard shows how much is hidden. For a **complete** picture run it as root (the
nightly systemd timer runs as root, so scheduled scans are always complete).

## Refresh

`install.sh` sets up a systemd timer that re-scans and atomically replaces the JSON. The
dashboard fetches fresh JSON on each page load, and installed deployments use `viewer/serve.py`
so the Rescan button truthfully shows manual-only status unless server-side rescan is
explicitly enabled. Run `./install.sh --dry-run` first to write and syntax-check units
without privileged `systemctl` actions.

## Optional AI Cleanup Advisor

The AI Cleanup Advisor is optional and local-first. With no AI environment
variables set, the dashboard continues to work normally and `/ai/status` should
report a disabled, non-blocking state. When enabled, the advisor uses
deterministic snapshot rules first and can optionally ask a local LLM to
summarize/prioritize bounded evidence. Run it from the global header control;
recommendations then appear as badges in the treemap, Top files, and Stale
views. The AI tab is for compact grouped summaries, details, and exclusions.

Safety rules:

- AI results are suggestions for humans to review; storage-viz does not execute
  delete or move commands.
- The LLM never receives shell/filesystem authority. Optional live inspection is
  server-owned, read-only, metadata-only, allowlisted, and disabled by default.
- Only low-risk delete recommendations may be staged into the existing
  copy-only cleanup command panel; move/dedupe/investigate advice opens details.
- The LLM path is two-pass: an English analyzer creates structured advice, then
  a Korean translator localizes the final user-facing text.

Recommended local GPU default:

```bash
STORAGE_VIZ_AI_ENABLED=1
STORAGE_VIZ_AI_PROVIDER=ollama
STORAGE_VIZ_AI_ENDPOINT=http://127.0.0.1:11434
STORAGE_VIZ_AI_MODEL=qwen3.6:27b
```

See `docs/ai-cleanup-advisor.md`, `docs/ai-advisor-schema.md`, and
`docs/operations.md` for the contract, privacy model, and runtime setup.

## Data format

See `docs/schema-v1.md`, `docs/operations.md`, and `docs/host-manifest.md`. Key idea: a pruned size **tree** per mount
(small entries collapsed into an `other_bytes` remainder so the treemap stays exact),
plus **per-user totals by file owner**, a global **top-N largest files** list, and a
**stale** (big + old) list of deletion candidates.
