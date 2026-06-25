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
