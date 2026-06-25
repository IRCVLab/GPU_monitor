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
| Viewer | `viewer/index.html` + `viewer/echarts.min.js` | Self-contained offline dashboard (treemap / users / top files / stale). |
| Data | `data/<hostname>.json` | Scan output the viewer reads. Hostname-tagged for future multi-server. |
| Install | `install.sh` | Build + install binary, nightly cron, LAN serving (needs root). |

## Quick start (this server)

```bash
# 1. build the scanner
cd scanner && make && cd ..

# 2. run a scan (root recommended — see note) → writes data/<hostname>.json
sudo ./scanner/hstscan            # defaults: scan / /data /data1 /data3

# 3. serve the dashboard on the LAN
python3 -m http.server 8088       # run from the project root; open http://<host>:8088/viewer/
```

## Deploy on another server

The tool hardcodes nothing — mounts are auto-discovered from `/proc/self/mountinfo` and users
from the password database. To add a server:

```bash
git clone <repo> && cd storage-viz/scanner && make
sudo ./hstscan                    # produces data/<that-host>.json
```

Drop that host's JSON next to the viewer and add it to the host dropdown — the same dashboard
renders any server's snapshot.

## Root access

The scanner runs fine as a normal user but can only measure what it can read; unreadable
directories (other users' private homes, `/var/lib/docker`, etc.) are listed under `blocked[]`
and the dashboard shows how much is hidden. For a **complete** picture run it as root (the
nightly cron runs as root, so scheduled scans are always complete).

## Refresh

`install.sh` sets up a nightly root cron that re-scans and atomically replaces the JSON. The
dashboard fetches fresh JSON on each page load — no rebuild needed.

## Data format

See `docs/` and the JSON schema in the scanner. Key idea: a pruned size **tree** per mount
(small entries collapsed into an `other_bytes` remainder so the treemap stays exact),
plus **per-user totals by file owner**, a global **top-N largest files** list, and a
**stale** (big + old) list of deletion candidates.
```
