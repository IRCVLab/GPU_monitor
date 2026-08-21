# storage-viz operations

This project has two independent runtime surfaces:

- **Central dashboard:** `storage-viz-dashboard.service` runs `viewer/serve.py` on the operator host, polls configured remote servers, stores pulled snapshots, and serves the multi-server UI.
- **Per-server agent:** `storage-viz-scan.service` plus `storage-viz-scan.timer` run on each storage server through `deploy/install-agent.sh`. The timer uses `OnUnitActiveSec=6h` for six-hour scheduled collection.

The central installer and docs are intentionally separate from any unrelated monitoring stack. Storage Dashboard uses its own service names, paths, state, and loopback port; do not reuse unrelated services, ports, credentials, or paths, and do not restart or modify unrelated monitoring services for Storage Dashboard work.

## Storage Live automatic deployment

Storage Live is released from the shared monorepo described in the root CI/CD runbook. It uses the same deployment approval condition as GPU Live: the server must observe the exact current `main` SHA, a successful `ci.yml` push run for that SHA, and a successful `ci/required` check run for that SHA. There is no approval step after CI succeeds.

The Storage release path is independent from the GPU application. Storage uses its own service names, users, paths, state files, ports, and health gate:

| Surface | Storage value | Notes |
| --- | --- | --- |
| Puller timer | `storage-monitor-release-puller.timer` | Five-minute outbound polling cadence with persistence and jitter. |
| Puller service | `storage-monitor-release-puller.service` | Root orchestration with systemd hardening; builds as `storage-viz-builder`. |
| Dashboard service | `storage-viz-dashboard.service` | Serves the dashboard on `127.0.0.1:8088`. |
| Public proxy service | `storage-viz-proxy.service` | Owns public port `505` after bootstrap. |
| Runtime symlink | `/opt/storage-viz-dashboard` | Points at the active immutable release after cutover. |
| Release root | `/srv/storage-viz-dashboard/releases/<sha>/storage-monitor` | Immutable central dashboard release content. |
| Puller state | `/var/lib/storage-viz-dashboard/puller` | `current-live-sha`, `puller-state.json`, and `failed-release.json`. |
| Activation state | `/var/lib/storage-viz-dashboard/activation-state.json` | Active, previous, digest, and rollback metadata. |

Expected Live convergence is roughly CI/build time plus one five-minute puller cadence. A timer run that finds `current-live-sha` already equal to the current `main` SHA exits `already-current` after only reading the GitHub main ref; it does not build, restart, or touch dashboard/proxy services. If a built artifact digest already matches the active release digest, the puller records the new SHA and clears failed-release state without activation.

A failed checkout, build, authorization, activation, or health check writes `/var/lib/storage-viz-dashboard/puller/failed-release.json` and backs off retries from 15 minutes up to 6 hours. A newer `main` SHA clears that failed-SHA backoff automatically. To intentionally retry the same failed SHA after fixing local server conditions, remove only `failed-release.json` and start one puller run. Do not remove `current-live-sha`.

Automatic central deployment never changes the GPU application and never changes remote Storage scan agents, inventories, SSH keys, scan data, or `storage-viz-scan.service`/`storage-viz-scan.timer` on storage servers. Remote collection remains governed by the existing six-hour per-server agent timer and explicit manual rescan path.

Useful Storage Live commands on the central host:

```bash
sudo systemctl status storage-monitor-release-puller.timer
sudo systemctl status storage-monitor-release-puller.service
sudo journalctl -u storage-monitor-release-puller.service
sudo systemctl status storage-viz-dashboard.service
sudo systemctl status storage-viz-proxy.service
sudo journalctl -u storage-viz-dashboard.service
sudo journalctl -u storage-viz-proxy.service
sudo cat /var/lib/storage-viz-dashboard/puller/current-live-sha
sudo python3 -m json.tool /var/lib/storage-viz-dashboard/puller/puller-state.json
sudo python3 -m json.tool /var/lib/storage-viz-dashboard/activation-state.json
test ! -e /var/lib/storage-viz-dashboard/puller/failed-release.json || sudo python3 -m json.tool /var/lib/storage-viz-dashboard/puller/failed-release.json
```

Manual rollback is an operator action on the Storage central host. It rolls back only Storage Dashboard state and services:

```bash
sudo /usr/local/libexec/storage-dashboard-activate.py \
  --rollback-state \
  --restart-argv /usr/bin/systemctl restart storage-viz-dashboard.service storage-viz-proxy.service
sudo /usr/local/libexec/storage-dashboard-health-check.py
```

## One-time bootstrap cutover

The installer has two safe phases. Normal install copies Storage-owned deployer assets, users, directories, and systemd units, but it does not enable or start the release puller timer and does not start the managed proxy before cutover:

```bash
./deploy/server/install-dashboard-deployer.sh --dry-run --prefix /tmp/storage-dashboard-install-check
sudo ./deploy/server/install-dashboard-deployer.sh
```

The first live cutover is a separate, approved-candidate action. It requires an exact candidate SHA, expected artifact digest, artifact, and metadata:

```bash
sudo ./deploy/server/install-dashboard-deployer.sh \
  --bootstrap-cutover \
  --candidate-sha <40-lowercase-hex-main-sha> \
  --expected-digest <64-lowercase-hex-sha256> \
  --artifact /path/to/storage-monitor-dashboard-<sha>.tar.gz \
  --metadata /path/to/storage-monitor-dashboard-<sha>.sha256.json
```

Bootstrap preserves the existing live dashboard on `127.0.0.1:8088` and existing public proxy on `:505` until a candidate topology passes the non-mutating health probe. The candidate dashboard listens only on `127.0.0.1:18088`, uses isolated temporary data/state, and runs in preflight mode. The candidate proxy listens only on `127.0.0.1:1505` and forwards to the candidate dashboard while preserving the configured public Host/Origin.

Only after the candidate `can_rescan`, inventory, and `UNKNOWN_SERVER` probe passes does bootstrap identify and stop the exact process that owns `:505`, stop the validated legacy `8088` owner, activate the prepared release, and start `storage-viz-dashboard.service` plus `storage-viz-proxy.service`. If cutover fails after live owners are stopped, the managed rollback path restores the protected legacy dashboard/proxy target, starts Storage services under systemd, and requires previous dashboard/inventory health before reporting rollback success. The puller timer is enabled only after the first managed release passes production health.

This branch documents the bootstrap procedure; it does not claim Storage Live has already been bootstrapped.

## Post-bootstrap verification

After a successful bootstrap, verify the managed central surface from the Storage host. The bundled health checker performs the required non-mutating session, inventory, cookie, CSRF, and `UNKNOWN_SERVER` readiness checks through public port `505`:

```bash
sudo /usr/local/libexec/storage-dashboard-health-check.py
```

Use this expanded probe when you need visible evidence for each contract. It must return `can_rescan: true`, `data_mode: inventory`, enabled server IDs in `/etc/storage-viz/servers.json` order, and HTTP `404` with `UNKNOWN_SERVER` for a guaranteed absent server id. The final POST sends `{}` with the authenticated session cookie, CSRF token, exact `Host`, and exact `Origin`; it does not start a real scan.

```bash
PUBLIC_ORIGIN="$(awk -F= '$1 == "STORAGE_VIZ_PROXY_PUBLIC_ORIGIN" { print substr($0, index($0, "=") + 1) }' /etc/storage-viz/proxy.env)"
PUBLIC_HOST="${PUBLIC_ORIGIN#http://}"
VERIFY_TMP="$(mktemp -d)"

curl -sS -D "$VERIFY_TMP/session.headers" \
  -H "Host: $PUBLIC_HOST" \
  -H 'X-Forwarded-User: fixed-proxy-operator' \
  "http://127.0.0.1:505/api/session" \
  -o "$VERIFY_TMP/session.json"
python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["can_rescan"] is True; assert isinstance(data["csrf_token"], str) and data["csrf_token"]; print("can_rescan=true")' "$VERIFY_TMP/session.json"

COOKIE="$(awk 'BEGIN{IGNORECASE=1} /^Set-Cookie:/ { sub(/^Set-Cookie:[[:space:]]*/, ""); sub(/;.*/, ""); print; exit }' "$VERIFY_TMP/session.headers")"
CSRF="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["csrf_token"])' "$VERIFY_TMP/session.json")"

curl -sS \
  -H "Host: $PUBLIC_HOST" \
  -H 'X-Forwarded-User: fixed-proxy-operator' \
  -H "Cookie: $COOKIE" \
  "http://127.0.0.1:505/api/servers" \
  -o "$VERIFY_TMP/servers.json"
python3 - /etc/storage-viz/servers.json "$VERIFY_TMP/servers.json" <<'PY'
import json, sys
inventory = json.load(open(sys.argv[1]))
servers = json.load(open(sys.argv[2]))
expected = [row["id"] for row in inventory["servers"] if row.get("enabled", True) is True]
observed = [row["id"] for row in servers["servers"]]
assert servers["data_mode"] == "inventory", servers.get("data_mode")
assert observed == expected, (expected, observed)
print("inventory mode/order ok:", ",".join(observed))
PY

UNKNOWN_ID="hc-verify-$(date +%s)-$$"
HTTP_CODE="$(curl -sS -o "$VERIFY_TMP/unknown.json" -w '%{http_code}' \
  -X POST \
  -H "Host: $PUBLIC_HOST" \
  -H "Origin: $PUBLIC_ORIGIN" \
  -H 'X-Forwarded-User: fixed-proxy-operator' \
  -H "Cookie: $COOKIE" \
  -H "X-CSRF-Token: $CSRF" \
  -H 'Content-Type: application/json' \
  --data '{}' \
  "http://127.0.0.1:505/api/servers/$UNKNOWN_ID/rescan")"
test "$HTTP_CODE" = 404
python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["error"] == "UNKNOWN_SERVER"; print("UNKNOWN_SERVER non-mutating probe ok")' "$VERIFY_TMP/unknown.json"
rm -rf "$VERIFY_TMP"
```

A real rescan is separate and optional after the health probe succeeds. Use the dashboard button only when `/api/session` reports `can_rescan: true`; otherwise the request cannot pass the managed proxy/session contract.

## Rescan button troubleshooting

The browser rescan button requires a healthy managed proxy and a valid operator session. If the button is missing, disabled, or returns a write failure:

1. Confirm `storage-viz-proxy.service` and `storage-viz-dashboard.service` are active.
2. Confirm `/etc/storage-viz/proxy.env` public origin exactly matches `STORAGE_VIZ_ALLOWED_ORIGINS` in `/etc/storage-viz/dashboard.env`.
3. Confirm `STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator` and that operator appears in `STORAGE_VIZ_OPERATOR_ALLOWLIST`.
4. Request `/api/session` through port `505` and verify `can_rescan: true`, a session cookie, and a CSRF token.
5. Re-run `storage-dashboard-health-check.py`; it exercises the non-mutating authenticated POST path without starting a scan.

Do not troubleshoot the button by restarting the GPU application, changing remote scan timers, or triggering a real scan before session/proxy health passes.

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

### Isolated LAN manual rescans over HTTP

When the service is restricted to an isolated lab network, has no individual login system, and must expose manual rescans from the public dashboard URL, configure the loopback dashboard as a trusted proxy target with one fixed lab operator identity:

```bash
# /etc/storage-viz/dashboard.env
STORAGE_VIZ_TRUSTED_PROXY=1
STORAGE_VIZ_ALLOWED_ORIGINS=http://storage.internal:505
STORAGE_VIZ_OPERATOR_ALLOWLIST=lan-operator
STORAGE_VIZ_SESSION_COOKIE_SECURE=0
```

Run the bundled public proxy with the matching identity and exact origin:

```bash
STORAGE_VIZ_PROXY_BIND=0.0.0.0 \
STORAGE_VIZ_PROXY_PORT=505 \
STORAGE_VIZ_PROXY_UPSTREAM_HOST=127.0.0.1 \
STORAGE_VIZ_PROXY_UPSTREAM_PORT=8088 \
STORAGE_VIZ_PROXY_OPERATOR=lan-operator \
STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://storage.internal:505 \
python3 deploy/direct_proxy.py
```

Both `STORAGE_VIZ_PROXY_OPERATOR` and `STORAGE_VIZ_PROXY_PUBLIC_ORIGIN` are required to enable writes. Without both, the proxy remains GET/HEAD-only. With both, it overwrites inbound identity headers, accepts only the exact same-origin per-server rescan route, requires a bounded non-empty request containing only `{}`, forwards the session cookie and CSRF header, and rejects every other POST/PUT/PATCH/DELETE request.

`STORAGE_VIZ_SESSION_COOKIE_SECURE=0` is an explicit exception for isolated HTTP-only networks. Keep the default secure cookie for HTTPS deployments. Never expose this fixed-operator HTTP mode to an untrusted network.

### SSH-tunnel-only manual rescans

When the dashboard is reachable only through a local SSH tunnel and the central host is trusted, direct loopback rescans can be enabled without opening POST access on the LAN proxy:

```bash
STORAGE_VIZ_TRUSTED_PROXY=0
STORAGE_VIZ_DIRECT_LOOPBACK_RESCAN=1
STORAGE_VIZ_ALLOWED_ORIGINS=http://127.0.0.1:8088,http://localhost:8088
STORAGE_VIZ_OPERATOR_ALLOWLIST=direct-viewer
```

Keep `STORAGE_VIZ_BIND=127.0.0.1`. This mode rejects sample data, non-loopback binds, non-loopback or malformed origins, Host/Origin mismatches, and configurations without production inventory. It still requires the signed session cookie and CSRF token. Any LAN-facing proxy must remain GET/HEAD-only and forward the browser's original `Host` header so `/api/session` reports `can_rescan: false` outside the SSH tunnel.

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
