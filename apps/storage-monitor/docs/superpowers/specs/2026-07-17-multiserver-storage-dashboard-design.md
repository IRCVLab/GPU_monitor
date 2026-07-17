# Multi-Server Storage Dashboard Design

**Date:** 2026-07-17

**Status:** Proposed for implementation

**Repository:** `storage-viz`

## 1. Outcome

Build a storage-visibility service for internal lab servers that answers, at a glance:

- Which server or local mount is running out of space?
- Who is consuming that space?
- Which directories and files are largest or stale?
- What read-only inspection or cleanup command should a researcher run over SSH?

The product is a separate application from GPU Monitor. It may run on the same central machine, but it must not share code, processes, databases, server configuration, deployment workflows, ports, or runtime state with GPU Monitor.

## 2. Confirmed Product Decisions

- One central Storage Dashboard web server.
- A separate product and repository from GPU Monitor.
- A root-owned, read-only scanner runs locally on every monitored internal server.
- Automatic scan every six hours plus per-server manual rescan.
- Root filesystem policy: scan `/home` only.
- Other local physical mounts: scan the complete mount.
- Network and virtual mounts are excluded.
- AI Advisor and every AI-specific endpoint, control, badge, model integration, cache, document, and test are removed.
- The browser never deletes or moves files.
- Users may select safe paths and copy inspection or cleanup commands for later execution over SSH.
- The multi-server landing page uses an overview-first dense server list. Clicking a server opens its detailed treemap workspace.
- Server display order follows the configured inventory and never changes automatically because of capacity, freshness, or status.

## 3. Non-Goals

- No integration with GPU Monitor's UI, backend, database, SSH credentials, server registry, or deployment process.
- No real-time filesystem watch or event stream.
- No incremental filesystem index in the first release.
- No browser-side or server-side file deletion, movement, deduplication, archival, chmod, or chown.
- No scanning of NFS, NFSv4, CIFS/SMB, SSHFS, CephFS, 9p, GlusterFS, or other network filesystems.
- No central SSH session that remains open for the duration of a full filesystem scan.
- No global "rescan every server now" action in the first release.
- No automatic reordering that moves urgent servers to the top; urgency is shown in place.

## 4. System Architecture

```text
Internal server A                Internal server B
┌──────────────────────┐        ┌──────────────────────┐
│ hstscan (root)       │        │ hstscan (root)       │
│ systemd service      │        │ systemd service      │
│ six-hour timer       │        │ six-hour timer       │
│ atomic local JSON    │        │ atomic local JSON    │
└──────────┬───────────┘        └──────────┬───────────┘
           │ short SSH/SFTP pull                      │
           └──────────────────┬───────────────────────┘
                              ▼
                 Central Storage Dashboard host
                 ┌──────────────────────────────┐
                 │ collector scheduler          │
                 │ snapshot validation/storage  │
                 │ manual scan orchestration    │
                 │ static/API web server        │
                 └──────────────┬───────────────┘
                                ▼
                       Internal browser users
```

### 4.1 Why local scan plus central pull

The scanner must walk local filesystems where they are mounted. Running it locally avoids sending filesystem traversal over SSH and allows the six-hour scan to finish even if the central dashboard is temporarily unavailable. The central service opens only short-lived SSH connections to read status, trigger an approved service, or fetch a completed JSON snapshot.

This design avoids storing a dashboard API token on every monitored server and avoids long-running remote SSH scan jobs.

## 5. Monitored Server Runtime

Each monitored server receives these independent components:

- `/usr/local/bin/hstscan`
- `/etc/storage-viz/scanner.yaml`
- `storage-viz-scan.service`
- `storage-viz-scan.timer`
- `/var/lib/storage-viz/snapshots/<generation>.json`
- `/var/lib/storage-viz/scan-status.json`
- a lock file under `/run/storage-viz/`

The scanner service runs as root because complete ownership and directory totals require access across user homes and data mounts. "Read-only" means it never modifies scanned filesystems; its only writes are bounded snapshot, status, temporary, and lock files under `/var/lib/storage-viz/` and `/run/storage-viz/`.

Runtime ownership and permissions are fixed:

- `/etc/storage-viz/scanner.yaml` is `root:root` with mode `0644`, contains no secret, and defines the stable server id plus conservative scanner resource limits.
- `/var/lib/storage-viz/`, `/var/lib/storage-viz/snapshots/`, and `/run/storage-viz/` are owned by `root:storage-viz-collector` with mode `0750`.
- completed snapshot and status files are `root:storage-viz-collector` with mode `0640`.
- temporary output is created with mode `0640` under the same directory and atomically renamed.
- the dedicated SSH collector account is a non-login or command-restricted account in the `storage-viz-collector` group; it receives no general sudo access.
- scanner logs at normal verbosity contain host id, durations, counts, and bounded error codes, not full user paths or usernames. Path-bearing debug logs are local-only, opt-in, and never returned to the browser.

The systemd service runs with `User=root`, `Group=storage-viz-collector`, and `UMask=0027`, so new files are created directly as `root:storage-viz-collector` without `chown` or an extra ownership capability. It also uses `NoNewPrivileges=yes`, a minimal capability set sufficient for read traversal, `ProtectSystem=strict`, `ProtectHome=read-only`, `ReadWritePaths=/var/lib/storage-viz /run/storage-viz`, `PrivateTmp=yes`, `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`, `ProtectControlGroups=yes`, and `RestrictAddressFamilies=AF_UNIX`. Exact directives are verified on the oldest supported systemd version and relaxed only when a tested host requires it; any relaxation is documented per host.

### 5.1 Scheduling and load control

- Timer cadence: every six hours.
- Add a per-host randomized delay of up to 30 minutes so all servers do not scan simultaneously.
- Run with `Nice=19`.
- Run with `IOSchedulingClass=idle` where supported.
- Use `flock` or an equivalent systemd lock so a second scheduled or manual scan cannot start while one is active.
- Scanner thread count is configurable and defaults conservatively.
- A failed scan never replaces the previous successful snapshot.
- The timer uses `Persistent=true` and `RandomizedDelaySec=30m` so a missed run is recovered without synchronizing every host after boot.
- A successful scanner run writes and validates a temporary file, atomically renames it to an immutable generation-addressed snapshot, then atomically replaces `scan-status.json` with the generation filename, byte size, SHA-256 digest, completion timestamp, server id, and scanner-config digest. At least the current and previous successful generations are retained.

### 5.2 Mount discovery and scan scope

Mount discovery uses `/proc/self/mountinfo` and filesystem metadata from the monitored server. The parser records mount id, parent id, major/minor device id, mount root, mountpoint, mount options, optional fields, filesystem type, source, and superblock options before choosing scan roots.

Rules:

1. Identify the mount entry for `/`.
2. If `/home` is on the root filesystem, scan `/home` with same-filesystem traversal. No other path on the root filesystem is selected.
3. If `/home` is a separate eligible local filesystem, do not cross into it from the root filesystem; include its mountpoint once as a complete non-root mount.
4. Include non-root persistent local filesystems with known local types such as `ext2`, `ext3`, `ext4`, `xfs`, `btrfs`, `zfs`, `f2fs`, `jfs`, `reiserfs`, `nilfs2`, `vfat`, `exfat`, `ntfs`, and `ntfs3`. LVM and mdraid are included through the filesystem mounted from their block devices rather than classified by volume-manager name.
5. Reject a mount when its filesystem type is in the non-bypassable network/virtual/container denylist, its mount options declare `_netdev`, its source is a loop/image device, or its source syntax is recognizably remote such as `host:/path` or `//host/share`.
6. FUSE filesystems are excluded by default because locality cannot be inferred safely. A future explicitly supported local FUSE type requires a code-and-test change, not an inventory override.
7. Treat a scan identity as `(major:minor, filesystem type, mount root, source)`. When multiple mount entries share an identity, choose exactly one by lowest mount id, then shortest normalized mountpoint, then lexical mountpoint as deterministic tie-breakers. Record every skipped duplicate with the chosen mount id and a bounded duplicate reason. This normally prefers the original mount over later bind aliases without relying on an unreliable bind label. Distinct btrfs subvolumes or ZFS datasets remain distinct identities and may be scanned separately.
8. Never traverse from one selected scan root into another mounted filesystem. Nested eligible mounts are scanned independently, so their bytes are not double-counted in their parent.
9. Version one has no per-server mount include/exclude override. Every eligible local persistent filesystem is scanned, the root filesystem remains limited to `/home`, and prohibited or unsupported families cannot be re-enabled through configuration.
10. Unsupported or ambiguous mounts are reported as skipped with a bounded reason code rather than guessed to be local. Supporting one requires a scanner code change, fixtures, and review.

Default excluded filesystem families include:

- virtual and ephemeral: `proc`, `sysfs`, `devtmpfs`, `tmpfs`, `cgroup`, `cgroup2`, `debugfs`, `tracefs`, `securityfs`, `pstore`, `mqueue`, `hugetlbfs`, `configfs`, `fusectl`, `rpc_pipefs`
- container and image: `overlay`, `squashfs`, `aufs`
- network and distributed: `nfs`, `nfs4`, `cifs`, `smb3`, `fuse.sshfs`, `fuse.ceph`, `fuse.rclone`, `fuse.davfs`, `9p`, `ceph`, `glusterfs`, `lustre`, `gpfs`

New filesystem families require fixtures and a code review before entering the default local allowlist or non-bypassable denylist.

The local `/etc/storage-viz/scanner.yaml` is authoritative for an autonomous scheduled scan. The central inventory contains the desired copy of the same server id and resource-limit settings; mount classification remains versioned scanner policy rather than mutable inventory. Installation or an explicit operator deployment command validates the desired config, writes the local file atomically, and records its digest; the browser cannot change it. Every status and snapshot carries that digest. A collector mismatch marks the server as configuration drift and does not silently reinterpret the snapshot. Local emergency resource-limit changes remain effective but visible until the central desired config is reconciled.

### 5.3 Limited central access

Storage Dashboard uses its own dedicated SSH identity and server inventory. It does not reuse GPU Monitor credentials.

The remote account is limited to:

- reading the completed snapshot and status files,
- reading the current service state,
- starting `storage-viz-scan.service` through a fixed `sudo -n` rule.

It cannot run arbitrary root shell commands. SSH host keys are pinned and strict host-key checking is enabled.

## 6. Central Collector

The central collector owns a separate inventory file, for example `config/servers.yaml`, containing:

- stable server id,
- display label,
- configured display order,
- host and SSH port,
- expected host key reference,
- desired scanner resource limits,
- enabled/disabled state.

Secrets and private keys live outside the repository under an operator-managed directory such as `/etc/storage-viz/`.

### 6.1 Snapshot pull

- Poll every monitored server for a completed snapshot on a lightweight interval, such as 10–15 minutes.
- Read the small status record first and compare its scan generation, completion timestamp, size, digest, and config digest. Download the full snapshot only when that immutable generation differs from the last validated central copy.
- Download the exact generation-addressed filename from the status record to a temporary central file; never fetch a mutable `latest` path.
- Validate that downloaded byte size and SHA-256 digest match the status tuple, then validate JSON, schema version, configured server identity, embedded generation, config digest, timestamp sanity, mount uniqueness, and non-negative numeric values. Any mismatch discards the temporary file and retries on a later poll.
- Atomically replace only that server's previous central snapshot after validation succeeds.
- Retain the last successful snapshot when the server is unreachable or the new payload is invalid.
- Store collection state separately from the measurement snapshot so transport errors do not corrupt storage data.

### 6.2 Manual rescan

The browser requests a rescan for one server. The central backend:

1. verifies the server id against inventory,
2. checks whether a scan is already active,
3. starts only the fixed `storage-viz-scan.service`,
4. reports requested/running/succeeded/failed job state,
5. fetches and validates the new snapshot after successful completion.

The browser never sends a filesystem path or shell command to the rescan endpoint.

Manual rescan is an operator action, not a general viewer action. Production deployment binds the application to loopback and places it behind an authenticated reverse proxy. Authentication may use the lab's SSO when available or separately managed reverse-proxy basic credentials for the first release. The proxy strips inbound identity headers, injects the authenticated identity, and forwards only to loopback. A configured operator allowlist grants rescan permission; all authenticated lab users receive read-only viewer access. Direct unauthenticated LAN binding is development-only and manual rescan remains disabled in that mode.

State-changing requests require an exact allowed `Origin`, a same-site session, and a per-session CSRF token sent in a request header. The backend enforces one active job per server, a configurable cooldown that defaults to 15 minutes after a manual trigger, and a bounded global concurrency limit. Every accepted or rejected trigger records timestamp, authenticated actor, server id, result code, and job id without shell output or sensitive path data.

## 7. Snapshot and State Contracts

The existing schema-v1 measurement contract in [`docs/schema-v1.md`](../../schema-v1.md) remains the scanner-to-viewer source of truth and is extended additively. It contains server identity, scan timing, mount capacity, pruned trees, per-user totals, top files, stale files, and blocked paths.

The multi-server release requires these additive fields:

- top level: `server_id`, `scan_finished_unix`, `scan_generation`, and `selected_roots[]`;
- each selected root: stable `mount_id`, `major_minor`, `mount_source`, `mount_root`, `mountpoint`, `scan_root`, `fstype`, and `status`;
- selected-root status: `complete`, `partial`, `failed`, or `skipped`, plus bounded `error_code` and numeric blocked/error counts;
- every successful `mounts[]` entry references its `mount_id` and `scan_root` so command-path validation never infers trust from display text;
- skipped or failed selected roots remain represented in `selected_roots[]` even when they cannot provide a tree.
- every selectable tree node and table row has `kind` with one of `directory`, `file`, `symlink`, or `other`; missing or unknown kinds are display-only and cannot produce a destructive command. Directory tree nodes normally use `directory`, `top_files[]` and `stale[]` rows use `file`, and future non-regular entries must preserve their actual kind.

Schema fixtures and validation define required types, numeric ranges, unique identities, timestamp ordering, and tree byte invariants. Unknown major schema versions are rejected; additive fields within schema v1 remain forward-compatible.

Multi-server collection adds a separate central state record per server:

```json
{
  "server_id": "hinton",
  "last_pull_attempt_at": "2026-07-17T12:00:00Z",
  "last_successful_fetch_at": "2026-07-17T12:00:00Z",
  "snapshot_scan_finished_at": "2026-07-17T11:42:00Z",
  "snapshot_availability": "available",
  "freshness": "fresh",
  "latest_pull_status": "succeeded",
  "latest_scan_result": "complete",
  "configuration_sync": "in_sync",
  "active_job": null,
  "last_error_code": null,
  "last_error_message": null
}
```

The retained snapshot, its age, the latest pull attempt, the latest scan result, and any active manual job are independent state domains:

- `snapshot_availability`: `available` or `absent`;
- `freshness`: `fresh`, `stale`, or `unknown`;
- `latest_pull_status`: `succeeded`, `unreachable`, `invalid_snapshot`, or `not_installed`;
- `latest_scan_result`: `complete`, `partial`, or `failed`;
- `configuration_sync`: `in_sync`, `drifted`, or `unknown` by comparison with the desired central scanner-config digest.

Manual scan job state is one of `requested`, `running`, `succeeded`, or `failed`; `active_job` is `null` when no job is active. A job record includes job id, server id, actor, request/start/finish timestamps, and a bounded result code. Capacity pressure is separately derived per mount as `normal`, `warning`, or `critical`; it is not a transport, freshness, measurement, or job state. The frontend derives one concise display treatment from these domains by documented precedence instead of storing a conflated `healthy` flag.

A partial scan is a valid snapshot only when at least one selected root completed. Its per-root `partial` or `failed` states and bounded error codes remain visible. A total scan failure never replaces the prior successful snapshot.

The frontend renders known codes into concise Korean copy and does not display raw stack traces or SSH output.

## 8. Web Product and Information Architecture

### 8.1 Overview-first landing page

The first page is a dense, stable list in configured server order. Every row shows:

- server name,
- local mount count,
- compact mount utilization bars,
- total available bytes,
- semantic pressure state,
- exceptional freshness, pull, scan, or configuration-drift status only.

Normal six-hour cadence does not produce noisy second-by-second copy. Freshness appears prominently only when stale, failed, or unreachable.

Capacity thresholds use both percentage and remaining bytes. A nearly full large disk and a small disk with almost no absolute free space must both be detectable. Exact thresholds belong in centralized configuration and tests.

Color is semantic and secondary to text and shape:

- neutral/default for healthy capacity,
- amber for warning,
- red for critical or failed state.

The list does not reorder itself when status changes. Users can trust spatial memory.

### 8.2 Server detail workspace

Clicking a row opens a server detail page that preserves the valuable parts of the current storage-viz product:

- mount selector,
- byte-proportional treemap with drill-down,
- user totals,
- top files,
- stale files,
- blocked-path visibility,
- selection and cleanup-command panel,
- per-server manual rescan.

AI controls, AI status, AI tabs, AI badges, provider labels, LLM scan toggles, advisor details, exclusions, and AI-generated recommendations do not exist.

### 8.3 Cleanup command workflow

Users may select a path from treemap or tables. The dashboard creates copyable commands but never executes them.

The panel separates:

- inspection commands, shown first,
- destructive cleanup commands, hidden behind an explicit reveal and warning.

Every path comes from a validated snapshot selection, is treated as an opaque string, and is POSIX-shell-quoted. Commands are rejected for `/`, selected scan roots, mount roots, one-segment system roots, relative paths, control-character-containing paths, or paths outside the selected snapshot's scanned roots. Globs, leading dashes, whitespace, quotes, and Unicode are preserved only through quoting and `--` argument termination. A snapshot path is never re-resolved by the web service and the UI warns that the live path may have changed since the scan.

The first release has a fixed command allowlist:

- size inspection: `sudo du -shx -- <path>`;
- largest descendants: a fixed `sudo find <path> -xdev ...` template with no user-supplied flags; selected paths are absolute, so they cannot be parsed as options;
- metadata inspection: fixed `sudo stat -- <path>` and modification-time listing templates;
- file removal: `sudo rm -i -- <path>` only for a snapshot node typed as a file;
- directory removal: `sudo rm -ri --one-file-system -- <path>` only for a snapshot node typed as a directory.

Destructive templates are never shown by default. They require a separate explicit reveal after inspection commands, retain interactive confirmation, never use `-f`, and cannot be combined into multi-path or shell-pipeline commands. The browser offers copy only; the backend has no command-execution endpoint.

Example command groups may include:

- verify size and owner,
- list largest descendants,
- inspect modification times,
- remove a confirmed file or directory.

The UI states that copied commands must be reviewed and run manually over SSH.

## 9. Failure Behavior

- **Server unreachable:** retain the last valid snapshot, preserve its availability, compute freshness from its age, and set `latest_pull_status=unreachable`.
- **Snapshot old:** retain data and set `freshness=stale` without inventing current capacity.
- **Scan failed:** retain the last successful snapshot and set `latest_scan_result=failed` with a bounded failure reason.
- **Invalid JSON/schema/digest:** discard the new payload, retain good data, and set `latest_pull_status=invalid_snapshot`.
- **Manual scan already active:** return a conflict response and show current progress.
- **Central collector restart:** resume from persisted state and snapshots.
- **Missing server installation:** show `not_installed` with an operator-facing setup hint.
- **Partial mount failure:** preserve successful mounts and expose blocked/error metadata for the failed mount.

## 10. Security and Privacy

- Bind the central dashboard to loopback by default and expose it through an authenticated internal reverse proxy. A direct internal-network bind is development-only and disables state-changing actions.
- Storage paths, usernames, project names, and activity timestamps are sensitive internal metadata and never leave the lab network.
- AI and external model calls are removed.
- Use dedicated SSH credentials, strict host-key verification, and least-privilege sudo rules.
- Do not serve `.env`, SSH keys, raw collector logs, or arbitrary files from the web root.
- No endpoint accepts arbitrary shell commands.
- No endpoint deletes, moves, archives, chmods, or chowns files.
- Generated snapshots and collector state remain ignored by Git.
- Manual rescan authorization, CSRF validation, per-server cooldown, global concurrency, and bounded audit events are mandatory production controls.

## 11. Separation from GPU Monitor

The Storage Dashboard has its own:

- repository and release history,
- source directory,
- service account and SSH identity,
- server inventory,
- systemd services,
- frontend and backend process,
- port or internal hostname,
- data and state directories,
- deployment workflow,
- health checks and rollback path.

No Storage Dashboard implementation task modifies or restarts GPU Monitor LIVE or DEV services. The only shared resource is the central host machine. Storage scanning happens on monitored servers under low CPU and I/O priority.

## 12. Testing Strategy

### 12.1 Scanner

- Linux build with warnings enabled.
- Existing hardlink, symlink, unreadable path, non-UTF8, low-fd, and byte-accuracy tests.
- Mountinfo fixtures for root `/home` selection.
- Fixtures for local data mounts.
- Fixtures for `/home` on root and `/home` as a separate filesystem.
- Fixtures for ext4/xfs on plain block, LVM, and mdraid devices; btrfs subvolumes; ZFS datasets; bind-mount duplicates; and nested eligible mounts.
- Fixtures proving NFS/NFSv4, CIFS/SMB, SSHFS, Ceph variants, GlusterFS, Lustre, GPFS, 9p, `_netdev`, generic FUSE, virtual, overlay, and container mounts are excluded or reported as unsupported.
- Configuration tests proving inventory has no mount-selection override and cannot include `/`, scan root filesystem paths outside `/home`, or bypass network/virtual/FUSE exclusions.
- Atomic output and lock behavior.
- Six-hour timer and randomized-delay unit verification.
- Service permission, umask, read/write-boundary, capability, no-network, and systemd hardening verification.

### 12.2 Collector and backend

- Fake SSH transport boundary; tests must not require real servers.
- Inventory validation and stable order.
- Successful pull, unreachable server, timeout, invalid JSON, wrong host, old timestamp, and schema mismatch.
- Previous-good-snapshot retention.
- Manual scan state machine and duplicate-trigger conflict.
- Read-only viewer versus operator authorization, trusted-proxy header stripping, exact-origin/CSRF enforcement, cooldown, global concurrency, and bounded audit-record tests.
- Endpoint allowlist and path traversal tests.
- AI routes return not found because AI is removed, not merely disabled.

### 12.3 Viewer

- Overview rows preserve inventory order.
- Healthy, warning, critical, stale, unreachable, and scan-failed states.
- Mount bars and free-byte labels match snapshot facts.
- Server detail drill-down preserves existing treemap accuracy.
- Cleanup selection accepts only safe scanned paths.
- Command-template snapshots cover required and unknown `kind`, file/directory type, symlink/other rejection, quotes, whitespace, globs, leading dashes, Unicode, control-character rejection, stale snapshot warning, inspection-first ordering, destructive reveal, and mount/scan-root rejection.
- No AI controls, scripts, tabs, requests, or copy remain.
- Responsive and keyboard-accessible behavior.

### 12.4 End-to-end and operational

- Playwright smoke flow: overview → server → mount → path selection → command copy.
- Manual rescan flow against a fake or isolated test host.
- Linux installer dry-run and systemd unit verification.
- Pilot scan performance measurement using elapsed time, CPU, I/O pressure, and application workload observation.
- Separation check records GPU Monitor service definitions, process ids, listening ports, repository status, and health before and after Storage Dashboard deployment; values must remain unchanged except for unrelated process-id churn explicitly explained by the operator.
- Static checks reject GPU Monitor paths, credentials, registry imports, environment names, service names, and deployment scripts from Storage Dashboard source and configuration.

## 13. Rollout

1. Remove AI and restore a clean single-server baseline with regression tests.
2. Add mount-policy discovery and tests.
3. Add central inventory, collector state, and fake-SSH tests.
4. Implement overview-first UI with synthetic multi-server fixtures.
5. Add manual scan orchestration and least-privilege remote installation assets.
6. Deploy central service without touching GPU Monitor.
7. Pilot one internal server, preferably a representative host with multiple local mounts.
8. Verify scan duration and workload impact under `nice`/`ionice`.
9. Add remaining servers with staggered timers.
10. Keep a documented uninstall and rollback procedure for scanner units and central service.

## 14. Acceptance Criteria

- A researcher can identify a pressured server and mount from the first page without opening every host.
- Server order remains configured and stable.
- Clicking a server exposes accurate treemap, user, top-file, stale-file, and blocked-path detail.
- Root filesystem scans only `/home`.
- Every other local physical mount is scanned in full.
- Network and virtual mounts are excluded by tested policy.
- Automatic scans run every six hours with low scheduling priority and no overlap.
- Manual rescan operates per server through a bounded service action.
- Only an authenticated configured operator can request manual rescan; unauthorized, cross-origin, duplicate, too-frequent, and over-concurrency requests are rejected and audited.
- Central fetch failure never destroys the last valid snapshot.
- Cleanup commands are copy-only and shell-quoted; the web service has no delete endpoint.
- No AI control, route, model integration, cache, recommendation, badge, document, or runtime dependency remains.
- GPU Monitor code, configuration, processes, ports, databases, and deployment remain unchanged.
- Deployment evidence confirms Storage Dashboard uses distinct repository paths, service names, credentials, inventory, state directories, port/hostname, health checks, and rollback actions, and that GPU Monitor health remains unchanged after pilot rollout.
