# storage-viz operations

This project has two independent runtime surfaces:

- **Central dashboard:** `storage-viz-dashboard.service` runs `viewer/serve.py` on the operator host, polls configured remote servers, stores pulled snapshots, and serves the multi-server UI.
- **Per-server agent:** `storage-viz-scan.service` plus `storage-viz-scan.timer` run on each storage server through `deploy/install-agent.sh`. The timer uses `OnUnitActiveSec=6h` for six-hour scheduled collection.

The central installer and docs are intentionally separate from any unrelated monitoring stack. Storage Dashboard uses its own service names, paths, state, and loopback port; do not reuse unrelated services, ports, credentials, or paths, and do not restart or modify unrelated monitoring services for Storage Dashboard work.

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

`install.sh` installs only the central dashboard. It does not install scanner agents, restart unrelated monitoring services, or modify any unrelated monitoring path, service, port, or credential; use `deploy/deploy-agent.sh` for remote agent bootstrap.

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

## Local development sample mode

Run the deterministic sample dashboard from the repository root when you need a browser demo without production inventory:

```bash
STORAGE_VIZ_DEV_SAMPLE_DIR="$(pwd)/data" \
STORAGE_VIZ_BIND=127.0.0.1 \
STORAGE_VIZ_PORT=8088 \
python3 viewer/serve.py
```

Use `http://127.0.0.1:8088`. `STORAGE_VIZ_DEV_SAMPLE_DIR` must point at the repository `data` directory. In this mode `/api/servers` reports `data_mode: "sample"`, the UI shows the sample marker, and `data/hosts.json` keeps the four generated sample servers in this exact order: `hinton`, `atlas`, `orion`, `zeus`. This mode is read-only sample data for local development. Production inventory mode is separate, reports `data_mode: "inventory"`, and does not imply production storage is sample data.

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

## Managed local storage and mount identity

The overview reports **managed local storage**. This is the unique filesystem capacity of scan-eligible local mounts managed by the Storage Dashboard agent on each server. It is not raw physical disk inventory, does not claim to include unmounted or unformatted devices, and is not computed by adding every visible mount row.

Each complete or partial selected root may carry a `capacity_id` derived from its safe mount identity. When multiple selected mounts on the same server share the same capacity identity, the overview counts that capacity once for server and page totals. The duplicate mount rows may remain visible for navigation, but they do not double-count capacity.

When no exact `/data` mount exists in `/proc/self/mountinfo`, `/data` may be represented by a synthetic root-backed target only for ordinary real directories on the root device. That synthetic target uses logical id `<source-id>-root-data`; it shares `capacity_id` and physical metadata with the root-backed `/home` entry so totals are not inflated.

If a mount identity cannot be resolved safely, or if rows for the same identity report inconsistent capacity numbers, the dashboard does not guess. It leaves the mount visible, excludes that identity from exact totals, and marks the aggregate as partial or unknown. A partial/unknown aggregate means capacity accounting is intentionally conservative; it is not evidence of a scanner failure by itself.

Hardlinks are deduplicated process-wide. If a hardlinked file belongs to both `/home` and synthetic `/data`, it is counted once and attributed deterministically to the first target, `/home`.
## SSD/HDD media classification

The per-server agent classifies storage media from backing leaf block devices through Linux sysfs. It resolves the selected mount's block `major:minor` under `/sys/dev/block`, follows bounded `slaves` topology, and reads leaf `queue/rotational` values. It does not run heavyweight inventory commands or infer media from names, filesystem types, or capacity sizes.

Media labels have these meanings:

| Label | Meaning |
| --- | --- |
| `SSD` | All resolved backing leaves report non-rotational media. |
| `HDD` | All resolved backing leaves report rotational media. |
| `Mixed` | Resolved backing leaves include both SSD and HDD media. |
| `Unknown` | Topology or rotational data could not be resolved safely. |

`Mixed` and `Unknown` are expected safe states. Treat them as conservative metadata, not scanner failures.

## Mount exclusion policy

Agents scan only local filesystems selected by `agent.scan_runner` policy. The central inventory cannot override mount policy or add scan roots. Mandatory exclusions include network, distributed, virtual, and container-backed filesystems such as NFS/NFS4, CIFS/SMB, sshfs, generic FUSE mounts, distributed filesystems, proc/sys/dev pseudo filesystems, overlay/container layers, and other non-local mounts. These exclusions prevent recursive network scans, container internals, and virtual kernel trees from entering central reports. Excluded mounts stay outside managed local storage totals.

## Root and `/data` policy

The production agent path enforces the following behavior:

- The agent never invokes a direct scan of `/`; standalone scanner positional mode remains available for diagnostics.
- `/home` remains scan-eligible and keeps its normal local-policy treatment.
- A synthetic `/data` target is created only for an ordinary real directory under the root filesystem when no exact mountinfo entry owns `/data`.
- Explicit local `/data` mount entries are treated as normal scan targets.
- Explicit prohibited, network, unsupported, or symlinked `/data` roots block synthetic `/data` fallback.
- The scanner performs `lstat` symlink checks and runtime expected-device/opened-directory guards before accepting any candidate root, so synthetic `/data` is only used for local real directories.

## SSH identity ownership and modes

Store private identities under `/etc/storage-viz/keys`. Recommended ownership is `root:storage-viz` with directory mode `0750` and private key mode `0640` so the `storage-viz` service can read only the intended keys without making them world-readable. A stricter `0600` root-owned key is acceptable only if the service receives access through an equivalent narrow mechanism. Keep `/etc/storage-viz/known_hosts` `0644` or stricter and managed by operators.

## Per-server agent bootstrap

The remote runtime identity is the existing `monitoring` account. Bootstrap is interactive only through `shchoi`; after bootstrap, `monitoring` may run exactly this noninteractive sudo command:

```text
monitoring ALL=(root) NOPASSWD: /usr/bin/systemctl start storage-viz-scan.service
```

`deploy/deploy-agent.sh` verifies that exact sudo policy with `LC_ALL=C sudo -n -l`. If it is missing or broader than expected, the script uses the interactive `shchoi` bootstrap path once, copies an archive to a private remote temporary directory, runs `deploy/install-agent.sh`, cleans up by removing the copied temporary directory, then rechecks the exact policy.

Do not grant shell, restart, stop, daemon-reload, edit, or wildcard sudo privileges to `monitoring`.

## Collection and manual rescan

Each agent writes snapshots under `/var/lib/storage-viz` and status under `/var/lib/storage-viz/scan-status.json`. The agent timer runs every six hours using `storage-viz-scan.timer` with `OnUnitActiveSec=6h`, `Persistent=true`, and randomized delay.

Central manual rescans are bounded: the dashboard first queries only the fixed unprivileged remote command `/usr/bin/systemctl show --property=ActiveState --value storage-viz-scan.service`; if the unit is active, activating, or reloading, the request is rejected as an active job before any start/cooldown is consumed. When inactive, the dashboard can request only the fixed remote command `sudo -n /usr/bin/systemctl start storage-viz-scan.service` for a configured server id. Operators cannot submit paths or commands. Rescan transport has bounded timeouts and central job concurrency/cooldown limits.

The `/data` policy affects root selection only; six-hour scheduled scans and manual scan behavior remain unchanged.

## Cleanup workflow

The UI may prepare copy-only cleanup command snippets for humans to review. It must not execute delete, move, chmod, chown, or arbitrary shell actions. Operators should copy commands into their own terminal only after reviewing the selected paths and policy.
