# storage-viz architecture

`storage-viz` is a multi-server storage dashboard with a small central service and isolated per-server scan agents. The central service presents snapshots and operator controls; agents measure local disks and expose only fixed status/snapshot/rescan surfaces.

## System boundaries

| Boundary | Files | Responsibility | Extension rule |
|---|---|---|---|
| Scanner | `scanner/hstscan.c`, `scanner/Makefile` | Fast local filesystem walker used only by the agent. | Preserve local-only scan roots and JSON correctness. |
| Agent runtime | `agent/`, `deploy/install-agent.sh`, `deploy/systemd/storage-viz-scan.*` | Root scan runner, six-hour timer, status envelope, atomic snapshots, and exact sudo policy for the `monitoring` account. | Do not accept arbitrary paths or commands from central. |
| Central collector | `collector/`, `config/servers.example.yaml` | Strict inventory validation, OpenSSH/SFTP transport, state store, polling, and bounded manual rescan. | Inventory references external identity/known-host paths and must not contain secrets. |
| Central dashboard | `viewer/serve.py`, `deploy/systemd/storage-viz-dashboard.service.in`, `install.sh` | Loopback HTTP API/UI, trusted-proxy auth, exact-origin/CSRF checks, pulled snapshot serving. | Bind loopback by default and publish only through an authenticating reverse proxy. |
| Data fixtures | `data/hosts.json`, `data/*.sample.json`, `data/gen_sample.py` | Tracked demo metadata and scrubbed fixtures. | Generated production snapshots remain ignored. |
| Operations docs | `docs/operations.md`, `docs/host-manifest.md` | Deployment, bootstrap, auth, collection cadence, and cleanup runbooks. | Runtime specifics belong in docs rather than hidden service assumptions. |

## Data flow

```text
configured storage server
  └─ storage-viz-scan.timer (OnUnitActiveSec=6h)
      └─ storage-viz-scan.service
          └─ agent.scan_runner + scanner/hstscan
              ├─ /var/lib/storage-viz/scan-status.json
              └─ /var/lib/storage-viz/snapshots/<server>-<generation>-v1.json

central host
  └─ storage-viz-dashboard.service on 127.0.0.1:8088
      ├─ reads /etc/storage-viz/servers.json
      ├─ uses /etc/storage-viz/keys/* and /etc/storage-viz/known_hosts
      ├─ pulls status/snapshots into /var/lib/storage-viz-dashboard
      └─ serves viewer API behind reverse-proxy auth
```

The central process never scans local filesystem roots for remote data. The per-server agent never receives browser-supplied paths or commands.

## Security model

- SSH uses `BatchMode=yes`, `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`, explicit identity files, and an explicit known-hosts file.
- The remote `monitoring` account can only start `storage-viz-scan.service` through an exact sudoers rule.
- The central dashboard requires trusted-proxy identity for API reads and additionally requires exact origin, allowlisted operator, signed session cookie, and CSRF token for manual rescans.
- The cleanup UI remains copy-only; the browser and central service do not execute destructive cleanup commands.

## Compatibility principles

- Snapshot schema changes are additive-compatible.
- Host ids are stable, filename-safe ids.
- Unknown additive JSON fields should not break viewers.
- Generated production snapshots can contain sensitive path/user activity and stay out of git.
