# storage-viz operations

This project has two independent runtime surfaces:

- **Central dashboard:** `storage-viz-dashboard.service` runs `viewer/serve.py` on the operator host, polls configured remote servers, stores pulled snapshots, and serves the multi-server UI.
- **Per-server agent:** `storage-viz-scan.service` plus `storage-viz-scan.timer` run on each storage server through `deploy/install-agent.sh`. The timer uses `OnUnitActiveSec=6h` for six-hour scheduled collection.

The central installer and docs are intentionally separate from any other monitoring stack. Do not reuse other services, ports, credentials, or paths.

## Central dashboard install

Run the dry-run first:

```bash
./install.sh --dry-run
```

Dry-run mode renders files under a temporary prefix, syntax-checks Python and systemd when tools are present, and does not call `systemctl`, connect to a host, change unrelated services, or start scans.

Real central install:

```bash
sudo ./install.sh
```

Central defaults:

| Setting | Default | Purpose |
| --- | --- | --- |
| Service | `storage-viz-dashboard.service` | Central dashboard service name. |
| App root | `/opt/storage-viz-dashboard` | Separate central application path. |
| Data dir | `/var/lib/storage-viz-dashboard/data` | Pulled remote snapshots. |
| State dir | `/var/lib/storage-viz-dashboard/state` | Central status/job state. |
| Inventory | `/etc/storage-viz/servers.json` | Remote server inventory. |
| Identity dir | `/etc/storage-viz/keys` | External SSH identity files referenced by inventory. |
| Known hosts | `/etc/storage-viz/known_hosts` | Strict host key file referenced by inventory. |
| Bind | `127.0.0.1` | Loopback-only by default. |
| Port | `8088` | Dashboard HTTP port. |

`install.sh` installs only the central dashboard. It does not install scanner agents; use `deploy/deploy-agent.sh` for remote agent bootstrap.

Useful central commands:

```bash
sudo systemctl status storage-viz-dashboard.service
sudo systemctl restart storage-viz-dashboard.service
journalctl -u storage-viz-dashboard.service
```

## Reverse proxy authentication and CSRF

The dashboard binds to `127.0.0.1:8088` by default. Publish it through a reverse proxy that authenticates users and forwards a stable identity header.

Set these in `/etc/storage-viz/dashboard.env`:

```bash
STORAGE_VIZ_ALLOWED_ORIGINS=https://storage.example.test
STORAGE_VIZ_OPERATOR_ALLOWLIST=operator-1,operator-2
```

Security rules:

- Origins are exact string matches through `STORAGE_VIZ_ALLOWED_ORIGINS`; do not use wildcards.
- Operator actions require trusted-proxy mode, an authenticated identity header, allowlisted operator id, a signed session cookie, and `X-CSRF-Token`.
- Read-only viewers may load status and snapshots after proxy authentication but cannot request rescans.
- Do not store or document password values. Use SSH identity files and strict known-hosts entries instead.

## Server inventory and SSH material

`/etc/storage-viz/servers.json` is strict JSON-compatible configuration. Each enabled server references external SSH files:

```json
{
  "servers": [
    {
      "id": "lab-alpha",
      "display_name": "Lab Alpha",
      "order": 10,
      "host": "alpha.example.test",
      "port": 22,
      "enabled": true,
      "username": "monitoring",
      "identity_file": "/etc/storage-viz/keys/lab-alpha_ed25519",
      "known_hosts_file": "/etc/storage-viz/known_hosts",
      "scanner": {
        "server_id": "lab-alpha",
        "scanner_path": "/opt/storage-viz/scanner/hstscan",
        "data_dir": "/var/lib/storage-viz",
        "run_dir": "/run/storage-viz",
        "threads": 4,
        "prune_home_mb": 50,
        "prune_data_mb": 100,
        "top": 200,
        "stale_days": 180
      }
    }
  ]
}
```

The inventory must not contain passwords, tokens, private key material, shell commands, arbitrary SSH arguments, or scan roots. Scanner roots are local-only on each agent and stay controlled by `agent.scan_runner` policy.

## Per-server agent bootstrap

The remote runtime identity is the existing `monitoring` account. Bootstrap is interactive only through `shchoi`; after bootstrap, `monitoring` may run exactly this noninteractive sudo command:

```text
monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service
```

`deploy/deploy-agent.sh` verifies that exact sudo policy with `LC_ALL=C sudo -n -l`. If it is missing or broader than expected, the script uses the interactive `shchoi` bootstrap path once, copies an archive to a private remote temporary directory, runs `deploy/install-agent.sh`, cleans up by removing the copied temporary directory, then rechecks the exact policy.

Do not grant shell, restart, stop, daemon-reload, edit, or wildcard sudo privileges to `monitoring`.

## Collection and manual rescan

Each agent writes snapshots under `/var/lib/storage-viz` and status under `/var/lib/storage-viz/scan-status.json`. The agent timer runs every six hours using `storage-viz-scan.timer` with `OnUnitActiveSec=6h`, `Persistent=true`, and randomized delay.

Central manual rescans are bounded: the dashboard can request only the fixed remote command `sudo -n /usr/bin/systemctl start storage-viz-scan.service` for a configured server id. Operators cannot submit paths or commands. Rescan transport has a bounded timeout and central job concurrency/cooldown limits.

## Cleanup workflow

The UI may prepare copy-only cleanup command snippets for humans to review. It must not execute delete, move, chmod, chown, or arbitrary shell actions. Operators should copy commands into their own terminal only after reviewing the selected paths and policy.
