# storage-viz — multi-server storage dashboard

Fast local disk scanner plus a central loopback dashboard for lab storage visibility.

`storage-viz` is split into two independent surfaces:

- **Central dashboard:** `storage-viz-dashboard.service` runs on the operator host, binds `127.0.0.1:8088` by default, reads `/etc/storage-viz/servers.json`, stores pulled snapshots under `/var/lib/storage-viz-dashboard`, and is published through an authenticating reverse proxy.
- **Per-server agent:** `storage-viz-scan.service` and `storage-viz-scan.timer` run on each storage server. The timer performs six-hour scheduled collection; manual rescan is the fixed `systemctl start storage-viz-scan.service` command only.

## Quick local demo

```bash
cd scanner && make && cd ..
sudo ./scanner/hstscan --out data/$(hostname).json
python3 viewer/serve.py
```

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
