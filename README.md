# storage-viz — multi-server storage dashboard

Fast local disk scanner plus a central loopback dashboard for lab storage visibility.

`storage-viz` is split into two independent surfaces:

- **Central dashboard:** `storage-viz-dashboard.service` runs on the operator host, binds `127.0.0.1:8088` by default, reads `/etc/storage-viz/servers.json`, stores pulled snapshots under `/var/lib/storage-viz-dashboard`, and is published through an authenticating reverse proxy.
- **Per-server agent:** `storage-viz-scan.service` and `storage-viz-scan.timer` run on each storage server. The timer performs six-hour scheduled collection; manual rescan is the fixed `systemctl start storage-viz-scan.service` command only.

## Quick local demo

Run the local sample dashboard from the repository root:

```bash
STORAGE_VIZ_DEV_SAMPLE_DIR="$(pwd)/data" \
STORAGE_VIZ_BIND=127.0.0.1 \
STORAGE_VIZ_PORT=8088 \
python3 viewer/serve.py
```

Open `http://127.0.0.1:8088`. Development sample mode is explicit: `/api/servers` reports `data_mode: "sample"`, the UI shows the sample marker, and the four tracked sample servers appear in deterministic order: `hinton`, `atlas`, `orion`, `zeus`. Production inventory mode reads `/etc/storage-viz/servers.json` and is not sample data.

## Capacity and media shown in the overview

The overview reports **managed local storage**: unique filesystem capacity for scan-eligible local mounts on each server. It is not raw physical disk inventory, and it is not the sum of every mount row. Duplicate mounts with the same capacity identity count once per server. If identities are unresolved or capacity numbers conflict, the affected mounts stay visible but are excluded from exact totals, which are marked partial/unknown instead of guessed.

Media labels come from the backing leaf block devices resolved through Linux sysfs. `Mixed` means both SSD and HDD leaves back that mount. `Unknown` means topology or rotational data could not be resolved safely. Both states are expected safe outcomes, not scanner failures. Existing policy still excludes network, distributed, virtual, and container mounts from collection.

## Central install

```bash
./install.sh --dry-run
sudo ./install.sh
```

Dry-run renders central assets and does not call `systemctl`, connect to remote hosts, change unrelated services, or start scans.

Central defaults:

- Service: `storage-viz-dashboard.service`
- Bind/port: `127.0.0.1:8088`
- App root: `/opt/storage-viz-dashboard`
- Inventory: `/etc/storage-viz/servers.json`
- SSH material: `/etc/storage-viz/keys` and `/etc/storage-viz/known_hosts`
- Data/state: `/var/lib/storage-viz-dashboard/data` and `/var/lib/storage-viz-dashboard/state`

## Remote agent deployment

Use `deploy/deploy-agent.sh` to bootstrap storage servers. Runtime access uses the existing `monitoring` account with exactly this sudoers rule:

```text
monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service
```

Interactive bootstrap is through `shchoi` only when the exact rule is missing or too broad. The deploy script uses strict host key checking, explicit identity files, no password handling, and private temporary cleanup.

## Operator security

Publish the loopback dashboard through a reverse proxy that authenticates users. Operator rescans require exact `STORAGE_VIZ_ALLOWED_ORIGINS`, proxy identity, allowlist membership, signed session cookie, and CSRF token. Do not store or document password values; use SSH identity files and known-host entries.

The cleanup workflow is copy-only. The UI may prepare commands for human review, but it does not execute destructive cleanup actions.

See `docs/operations.md`, `docs/architecture.md`, `docs/host-manifest.md`, and `docs/schema-v1.md`.
