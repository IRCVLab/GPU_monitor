# storage-viz operations

This project has two independent runtime surfaces:

- **Central dashboard:** `storage-viz-dashboard.service` runs `viewer/serve.py` on the operator host, polls configured remote servers, stores pulled snapshots, and serves the multi-server UI.
- **Per-server agent.** `deploy/bootstrap-agent.sh` installs the hourly timer, scheduled scan service, and manual scan service.

The central installer and docs are intentionally separate from any unrelated monitoring stack. Storage Dashboard uses its own service names, paths, state, and loopback port; do not reuse unrelated services, ports, credentials, or paths, and do not restart or modify unrelated monitoring services for Storage Dashboard work.

## Storage Live automatic deployment

Storage Live is released from the shared monorepo described in the root CI/CD runbook. It uses the same deployment approval condition as GPU Live: the server must observe the exact current `main` SHA, a successful `ci.yml` push run for that SHA, and a successful `ci/required` check run for that SHA. There is no approval step after CI succeeds.

The Storage release path is independent from the GPU application. Storage uses its own service names, users, paths, state files, ports, and health gate:

| Surface | Storage value | Notes |
| --- | --- | --- |
| Puller timer | `storage-monitor-release-puller.timer` | Five-minute outbound polling cadence with persistence and jitter. |
| Puller service | `storage-monitor-release-puller.service` | Root orchestration with systemd hardening; builds as `storage-viz-builder`. |
| Dashboard service | `storage-viz-dashboard.service` | Serves the dashboard on `127.0.0.1:8088`. |
| Public proxy service | `storage-viz-proxy.service` | Owns the configured internal listener after bootstrap; the public origin remains `:505` and may reach it through NAT/port forwarding. |
| Runtime symlink | `/opt/storage-viz-dashboard` | Points at the active immutable release after cutover. |
| Release root | `/srv/storage-viz-dashboard/releases/<sha>/storage-monitor` | Immutable central dashboard release content. |
| Puller state | `/var/lib/storage-viz-dashboard/puller` | `current-live-sha`, `puller-state.json`, and `failed-release.json`. |
| Activation state | `/var/lib/storage-viz-dashboard/activation-state.json` | Active, previous, digest, and rollback metadata. |

Expected Live convergence is roughly CI/build time plus one five-minute puller cadence. A timer run that finds `current-live-sha` already equal to the current `main` SHA exits `already-current` after only reading the GitHub main ref; it does not build, restart, or touch dashboard/proxy services. If a built artifact digest already matches the active release digest, the puller records the new SHA and clears failed-release state without activation.

A failed checkout, build, authorization, activation, or health check writes `/var/lib/storage-viz-dashboard/puller/failed-release.json` and backs off retries from 15 minutes up to 6 hours. A newer `main` SHA clears that failed-SHA backoff automatically. To intentionally retry the same failed SHA after fixing local server conditions, remove only `failed-release.json` and start one puller run. Rerunning the installer removes `failed-release.json` too, because new tools can make the same SHA succeed. Do not remove `current-live-sha`, except to roll forward after a manual rollback.

Automatic central deployment never changes the GPU application and never changes remote Storage scan agents, inventories, SSH keys, scan data, or `storage-viz-scan.service`/`storage-viz-scan.timer` on storage servers. The timer and manual scan service installed on each remote agent control remote collection.

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

The health checker probes the routes of the release that `/opt/storage-viz-dashboard` links to, so it also checks a rolled-back release from before the redesign.

## Deploy-tool changes

After changes to `deploy/server/`, rerun `deploy/server/install-dashboard-deployer.sh` from a main checkout.

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

If the current proxy script is outside `/opt/storage-viz-dashboard`, pass its exact process path explicitly. The current central host uses:

```bash
sudo env LEGACY_PROXY_PATH=/home/ircv/workspace/storage-viz-direct/proxy.py \
  ./deploy/server/install-dashboard-deployer.sh \
  --bootstrap-cutover \
  --candidate-sha <40-lowercase-hex-main-sha> \
  --expected-digest <64-lowercase-hex-sha256> \
  --artifact /path/to/storage-monitor-dashboard-<sha>.tar.gz \
  --metadata /path/to/storage-monitor-dashboard-<sha>.sha256.json
```

Bootstrap preserves the existing live dashboard on `127.0.0.1:8088` and the configured existing proxy listener until a candidate topology passes the non-mutating health probe. The external origin remains `http://<public-host>:505`, but `STORAGE_VIZ_PROXY_BIND` and `STORAGE_VIZ_PROXY_PORT` describe the actual listener on the central host and may differ when NAT or port forwarding is used. The candidate dashboard listens only on `127.0.0.1:18088`, uses isolated temporary data/state, and runs in preflight mode. The candidate proxy listens only on `127.0.0.1:1505` and forwards to the candidate dashboard while preserving the configured public Host/Origin.

Only after the candidate `can_rescan`, inventory, and `UNKNOWN_SERVER` probe passes does bootstrap identify the exact processes that own the configured proxy bind/port and `127.0.0.1:8088`. Before stopping either process, it copies the validated legacy proxy script into `/var/lib/storage-viz-dashboard/legacy-proxy/<sha256>/direct_proxy.py`, fixes that copy to root ownership with the Storage runtime group and non-writable mode, verifies that its digest still matches the exact running process source, and keeps the recovery identity in the cutover process without publishing an authoritative rollback state. Bootstrap then stops the validated owners, activates the prepared release, and starts `storage-viz-dashboard.service` plus `storage-viz-proxy.service`; deployment state is written only after that post-stop transition succeeds or an actual rollback restores the legacy service. Listener ownership is matched by both local address and port, so a NAT deployment may safely use the same numeric port on different local addresses. If cutover fails after live owners are stopped, the managed rollback path restores the protected legacy dashboard and executes only the checksum-verified managed proxy copy, then requires previous dashboard/inventory health before reporting rollback success. The puller timer is enabled only after the first managed release passes production health.

This branch documents the bootstrap procedure; it does not claim Storage Live has already been bootstrapped.

## Post-bootstrap verification

After a successful bootstrap, verify the managed central surface from the Storage host. The bundled health checker performs the required non-mutating session, inventory, cookie, CSRF, and `UNKNOWN_SERVER` readiness checks through the configured internal proxy listener while preserving the external `:505` Host/Origin contract:

```bash
sudo /usr/local/libexec/storage-dashboard-health-check.py
```

Use this expanded probe when you need visible evidence for each contract. It must return `can_rescan: true`, an inventory rollout, enabled server IDs in `/etc/storage-viz/servers.json` order, and HTTP `404` with `UNKNOWN_SERVER` for a guaranteed absent server id. The final POST sends `{}` with the authenticated session cookie, CSRF token, exact `Host`, and exact `Origin`; it does not start a real scan.

```bash
PUBLIC_ORIGIN="$(awk -F= '$1 == "STORAGE_VIZ_PROXY_PUBLIC_ORIGIN" { print substr($0, index($0, "=") + 1) }' /etc/storage-viz/proxy.env)"
PUBLIC_HOST="${PUBLIC_ORIGIN#http://}"
PROXY_BIND="$(awk -F= '$1 == "STORAGE_VIZ_PROXY_BIND" { print substr($0, index($0, "=") + 1) }' /etc/storage-viz/proxy.env)"
PROXY_PORT="$(awk -F= '$1 == "STORAGE_VIZ_PROXY_PORT" { print substr($0, index($0, "=") + 1) }' /etc/storage-viz/proxy.env)"
PROXY_CONNECT_HOST="$PROXY_BIND"
[[ "$PROXY_CONNECT_HOST" != 0.0.0.0 ]] || PROXY_CONNECT_HOST="${PUBLIC_HOST%:505}"
PROXY_ENDPOINT="http://${PROXY_CONNECT_HOST}:${PROXY_PORT}"
VERIFY_TMP="$(mktemp -d)"

curl -sS -D "$VERIFY_TMP/session.headers" \
  -H "Host: $PUBLIC_HOST" \
  -H 'X-Forwarded-User: fixed-proxy-operator' \
  "$PROXY_ENDPOINT/api/v2/session" \
  -o "$VERIFY_TMP/session.json"
python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["can_rescan"] is True; assert isinstance(data["csrf_token"], str) and data["csrf_token"]; print("can_rescan=true")' "$VERIFY_TMP/session.json"

COOKIE="$(awk 'BEGIN{IGNORECASE=1} /^Set-Cookie:/ { sub(/^Set-Cookie:[[:space:]]*/, ""); sub(/;.*/, ""); print; exit }' "$VERIFY_TMP/session.headers")"
CSRF="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["csrf_token"])' "$VERIFY_TMP/session.json")"

curl -sS \
  -H "Host: $PUBLIC_HOST" \
  -H 'X-Forwarded-User: fixed-proxy-operator' \
  -H "Cookie: $COOKIE" \
  "$PROXY_ENDPOINT/api/v2/fleet" \
  -o "$VERIFY_TMP/servers.json"
python3 - /etc/storage-viz/servers.json "$VERIFY_TMP/servers.json" <<'PY'
import json, sys
inventory = json.load(open(sys.argv[1]))
servers = json.load(open(sys.argv[2]))
expected = [row["id"] for row in inventory["servers"] if row.get("enabled", True) is True]
observed = [row["server_id"] for row in servers["servers"]]
assert isinstance(servers["rollout"], dict)
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
  "$PROXY_ENDPOINT/api/v2/servers/$UNKNOWN_ID/rescan")"
test "$HTTP_CODE" = 404
python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["error"]["code"] == "UNKNOWN_SERVER"; print("UNKNOWN_SERVER non-mutating probe ok")' "$VERIFY_TMP/unknown.json"
rm -rf "$VERIFY_TMP"
```

A real rescan is separate and optional after the health probe succeeds. Use the dashboard button only when `/api/v2/session` reports `can_rescan: true`; otherwise the request cannot pass the managed proxy/session contract.

## Rescan button troubleshooting

The browser rescan button requires a healthy managed proxy and a valid operator session. If the button is missing, disabled, or returns a write failure:

1. Confirm `storage-viz-proxy.service` and `storage-viz-dashboard.service` are active.
2. Confirm `/etc/storage-viz/proxy.env` public origin exactly matches `STORAGE_VIZ_ALLOWED_ORIGINS` in `/etc/storage-viz/dashboard.env`.
3. Confirm `STORAGE_VIZ_PROXY_OPERATOR=fixed-proxy-operator` and that operator appears in `STORAGE_VIZ_OPERATOR_ALLOWLIST`.
4. Request `/api/v2/session` through the configured proxy bind/port with the external `:505` Host header and verify `can_rescan: true`, a session cookie, and a CSRF token.
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

`install.sh` installs only the central dashboard. It does not install scanner agents, restart unrelated monitoring services, or modify any unrelated monitoring path, service, port, or credential; use `deploy/bootstrap-agent.sh` for remote agent bootstrap.

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

When external `:505` is forwarded to another local endpoint, set the bind and port to that real listener instead. For example, an external `http://storage.example:505` mapped to `192.168.0.3:8088` uses `STORAGE_VIZ_PROXY_BIND=192.168.0.3` and `STORAGE_VIZ_PROXY_PORT=8088` while keeping `STORAGE_VIZ_PROXY_PUBLIC_ORIGIN=http://storage.example:505`.

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

Keep `STORAGE_VIZ_BIND=127.0.0.1`. This mode rejects sample data, non-loopback binds, non-loopback or malformed origins, Host/Origin mismatches, and configurations without production inventory. It still requires the signed session cookie and CSRF token. Any LAN-facing proxy must remain GET/HEAD-only and forward the browser's original `Host` header so `/api/v2/session` reports `can_rescan: false` outside the SSH tunnel.

## Local development sample mode

Run the deterministic sample dashboard from the repository root when you need a browser demo without production inventory:

```bash
cd apps/storage-monitor
STORAGE_VIZ_BIND=127.0.0.1 \
STORAGE_VIZ_PORT=8088 \
python3 -m api.server --fixtures tests/fixtures/fleet
```

Use `http://127.0.0.1:8088`. In this mode `/api/v2/fleet` reports `rollout: null`, the UI shows the sample marker, and `tests/fixtures/fleet/hosts.json` keeps the four generated sample servers in this exact order: `hinton`, `atlas`, `orion`, `zeus`. This mode is read-only sample data for local development. Production inventory mode is separate, reports a rollout object, and does not imply production storage is sample data.

## Nexus network card

The overview page shows a throughput card for the Nexus link, read from the storage switch's RouterOS API rather than from inside Nexus. The dashboard polls the switch's byte counters every 2 seconds over a persistent API connection and keeps 1-minute average and maximum buckets for 24 hours. The buckets are written to `network.json` in `STORAGE_VIZ_STATE_DIR` once a minute, so a release restart keeps the graph.

Set all of these in `/etc/storage-viz/dashboard.env`. With none set the card is off, and a partial or malformed setting stops the dashboard at startup with a message naming the variable.

```bash
STORAGE_VIZ_NETWORK_SWITCH=192.168.0.250:8728
STORAGE_VIZ_NETWORK_SECRET_FILE=/etc/storage-viz/switch-api.secret
STORAGE_VIZ_NETWORK_USER=storage-viz
STORAGE_VIZ_NETWORK_LINKS=nexus:bond_nas:200000
```

- `STORAGE_VIZ_NETWORK_SWITCH` is `host` or `host:port` (default port 8728, the plain API; `api-ssl` is not supported).
- `STORAGE_VIZ_NETWORK_SECRET_FILE` holds the API password. The file must be readable by the dashboard user and is read again at each reconnect. The password is never logged or returned.
- `STORAGE_VIZ_NETWORK_USER` defaults to `storage-viz`. Use a read-only account. The dashboard only sends `/login` and `/interface/print`.
- `STORAGE_VIZ_NETWORK_LINKS` is a comma-separated list of `server_id:interface:capacity_mbps`. `bond_nas` carries 2 × 100 Gb/s, so its capacity is `200000`.

`GET /api/v2/network` returns `{"links":[{"server_id","interface","capacity_bps","now","peak","buckets","state"}],"sample_seconds":2,"generated_unix":N}`, or `{"links":[]}` when the card is off. Rates are from the server's side: `rx` is traffic sent to the server (the switch interface's tx counter) and `tx` is traffic the server sends. Each bucket is `[minute_unix, rx_avg, rx_max, tx_avg, tx_max]` in bits per second. `peak` is the 24-hour maximum of the per-minute maxima, stamped with its minute. `state` is `ok`, `stale` (no sample for 30 seconds) or `error` (the switch is unreachable or the interface is missing). A counter reset or a gap over 10 seconds leaves a hole in the graph and no rate is made up.

The dashboard unit does not restrict outbound TCP, so no unit change is needed. Sample mode (`--fixtures`) serves a synthetic Nexus day and never connects to a switch.

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

Use `deploy/bootstrap-agent.sh` with the administrator SSH identity and signed bundle. Bootstrap restricts the monitoring key to the storage gate.

## TrueNAS host (Nexus)

Nexus is a TrueNAS SCALE host. TrueNAS keeps `/` and `/usr` read-only and rebuilds `/etc` on boot and on update, so the standard bootstrap's units, sudoers and `/etc/storage-viz` files would not survive. On Nexus everything lives in the pool dataset `tank/storage-viz`, mounted at `/mnt/tank/storage-viz`:

| Path | Contents |
| --- | --- |
| `opt/releases/<sha>`, `opt/current` | agent releases and the active one |
| `data/` | manifest, captures, `requests/` (setgid `2770`), `update/` |
| `run/` | `scan.lock`, `progress.json` |
| `bootstrap/` | `gate.py`, `update.py`, `allowed_signers` |
| `agent.yaml` | `host_profile: truenas`, `volume_aliases` and `volume_media: ssd` for `/mnt/tank/datasets` and `/mnt/tank/workspace` |
| `home/` | home of `monitoring`; the middleware writes `.ssh/authorized_keys` |

The differences from a standard host:

- A TrueNAS cron job runs the agent as root every 5 minutes with `--scheduled`. It also serves rescans: the gate only writes `data/requests/<id>`, and the next cron run consumes it. There are no systemd units and no sudoers rule.
- `monitoring` is a middleware user in the group `storage-viz-collector`, with shell `/usr/bin/sh`. Its key runs `gate.py --root /mnt/tank/storage-viz --no-units`. The gate reports `scheduler: "cron"` and no units, and refuses `stage-update`, `activate-update` and `rollback-update` with `UPDATES_MANUAL`.
- The `truenas` profile scans only the data-pool datasets under `/mnt`. It skips the boot pool, the pool root, the `.system` datasets and the dataset that holds the agent's data.
- All files there are owned by `nobody` because of the NFS `mapall`, so the per-user breakdown shows one owner. The folder tree, top files and stale files are still accurate.

### Set up

Run the setup tool from the central host. It connects as `truenas_admin` with that account's key. Give it the sudo password through the environment or stdin; never on the command line.

```bash
cd ~/GPU_monitor/apps/storage-monitor
read -rs STORAGE_VIZ_SUDO_PASSWORD && export STORAGE_VIZ_SUDO_PASSWORD
./deploy/bootstrap-truenas.py --host 192.168.0.200 --admin truenas_admin --admin-key ~/.ssh/<admin-key> \
  --monitoring-key /etc/storage-viz/keys/nexus_ed25519.pub
unset STORAGE_VIZ_SUDO_PASSWORD
```

The tool prints every command it sends. It verifies the bundle under `/opt/storage-viz-dashboard/agent` against `agent/bootstrap/allowed_signers` before it contacts the host. The host's `update.py` checks the signature again, extracts the release, self-tests it and switches `opt/current`. The tool checks that `home/.ssh/authorized_keys` holds exactly the forced-command line, and it stops with an error if the middleware changed it. Expect the last line to report `observe through the gate: scan idle, release <sha>, health ok`. The tool is idempotent: a rerun changes only what differs.

Then add Nexus to the inventory with `transport: gate` and `agent_updates: manual`, as in the disabled `nexus` entry in `config/servers.example.yaml`, and restart the dashboard. The canary must be another server: the inventory refuses a manual canary.

### Upgrade the agent

The update channel never stages a release on a server with `agent_updates: manual`. The dashboard shows its release with `update: "manual"`. After the dashboard release carries a newer bundle, install it by hand:

```bash
./deploy/bootstrap-truenas.py --upgrade --host 192.168.0.200 --admin-key ~/.ssh/<admin-key> \
  --monitoring-key /etc/storage-viz/keys/nexus_ed25519.pub
```

If a scan holds the lock, the tool says so and the request stays queued. Rerun it after the scan. The previous release stays in `opt/releases`. To roll back to it, run on Nexus as root: `echo "activate <previous-sha>" > /mnt/tank/storage-viz/data/update/incoming/activate`, then `python3 -I /mnt/tank/storage-viz/bootstrap/update.py --root /mnt/tank/storage-viz`. Use `rollback <sha>` instead of `activate` to also mark the current release bad.

### Revert

`./deploy/bootstrap-truenas.py --revert --host 192.168.0.200 --admin-key ~/.ssh/<admin-key>` removes the cron job and the `monitoring` user. It keeps the dataset and the group. Add `--purge` to delete them too. Remove the server from the inventory first.

## Collection and manual rescan

`storage-viz-scan.timer` starts `storage-viz-scheduled-scan.service` at the start of each UTC hour with up to six minutes of scheduling delay. The agent scans resolved SSD roots every hour and HDD, mixed, and unresolved roots every third hourly slot. A persistent timer catches up after downtime. A new disk group, an incomplete scan, or a missing cached result triggers a fresh scan at the next tick. The shared agent lock prevents concurrent scans.

Agents publish a manifest and immutable per-volume captures under `/var/lib/storage-viz`. A manual rescan sends a request ID through the gate. The dashboard follows its receipt and the captures it names. Ubuntu hosts receive signed bundles through the canary rollout.

## Cleanup workflow

The UI may prepare copy-only cleanup command snippets for humans to review. It must not execute delete, move, chmod, chown, or arbitrary shell actions. Operators should copy commands into their own terminal only after reviewing the selected paths and policy.

## Live scan status

`GET /api/v2/servers/<inventory-id>/activity` serves the latest gate observation. The host lock and `run.json` identify a running or interrupted scan. Counters come from `progress.json` when its run ID matches. The collector polls idle hosts about once a minute and running scans about every five seconds.

Each actor reads a changed manifest and fetches its new captures by volume ID and digest. Across the fleet, two transfers can run at once. The dashboard continues to serve the previous accepted capture during a transfer.
