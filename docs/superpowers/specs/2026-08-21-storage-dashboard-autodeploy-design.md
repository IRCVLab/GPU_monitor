# Storage Dashboard Automatic Deployment Design

## Goal

Every successful push to `main` must become the Storage Dashboard Live release automatically, without changing GPU Monitor, remote storage agents, inventory, SSH keys, scan state, or collected snapshots. A failed build or unhealthy release must leave the previous Storage Live release serving traffic.

## Why the problem kept recurring

Storage had CI but no deployment contract. `ci/storage` proved repository behavior, but nothing compared the approved `main` SHA with the code serving port 505. The public proxy was also an ad-hoc process outside the dashboard service lifecycle. Consequently:

1. a code fix could pass tests and reach `main` while Live kept old files;
2. the proxy and backend could run incompatible authorization/cookie settings;
3. a tmux/process restart could silently restore an older proxy command;
4. no health gate asserted that `/api/session` reported `can_rescan: true` through the public proxy;
5. status reports incorrectly conflated CI success with deployment success.

The durable invariant is: **Storage Live is healthy only when its active release is the latest CI-approved `main` SHA and the public proxy exposes the expected operator capability.**

## Considered approaches

### A. GitHub Actions connects to the server over SSH

Rejected. The campus network blocks inbound GitHub-hosted runner access. Reintroducing this path would recreate the GPU deployment failure mode.

### B. Generalize the GPU deployer into a shared framework

Deferred. This could reduce duplication, but it changes the already-working GPU Live security and activation path. The immediate Storage fix must not create GPU regression risk.

### C. Independent Storage server puller modeled on GPU Live

Selected. The Storage host initiates outbound HTTPS, verifies the exact `main` SHA and successful `ci/required` evidence, builds as an unprivileged user, validates a deterministic artifact, and activates only Storage Dashboard services.

## Architecture

### Release poller

`storage-monitor-release-puller.service` runs as root from a five-minute persistent timer with randomized delay. It performs only these privileged orchestration operations:

1. read the current public GitHub `main` SHA;
2. verify the matching successful `ci.yml` push run and `ci/required` check;
3. clone the exact SHA and build under `storage-viz-builder` with an empty environment;
4. validate artifact checksum and manifest;
5. repeat GitHub authorization immediately before activation;
6. stream the artifact to the installed activator;
7. record the active SHA only after activation and health checks pass.

Failed SHAs receive bounded exponential backoff. A newer `main` SHA clears the old failure backoff. Concurrent runs are prevented by a file lock.

Bootstrap installs a Storage-owned copy of the repository's existing parameterized CI evidence checker (`scripts/authorize_gpu_release.py`) as `/usr/local/libexec/storage-release-authorizer.py`. The Storage puller never assumes GPU deployment files are installed on the Storage host. Future changes to the shared checker are classified as shared and therefore run both application CI paths before either puller can authorize them.

### Release artifact

The deterministic artifact contains only central runtime code:

- `viewer/`
- required `collector/` Python modules
- `config/` schema/example files needed by imports and validation
- `deploy/direct_proxy.py`
- runtime documentation/license metadata where needed

It excludes inventories, private keys, known-hosts files, dashboard environment files, collected snapshots, state, caches, tests, sample runtime selection, generated output, and remote-agent executables.

The manifest binds application name, schema version, exact Git SHA, artifact filename, and SHA-256 digest.

### Activation and rollback

Immutable releases live under `/srv/storage-viz-dashboard/releases/<sha>/storage-monitor`.

The existing `/opt/storage-viz-dashboard` runtime path becomes a symlink to the active immutable release. This preserves the current dashboard systemd unit path and avoids coupling deployment to GPU paths. The first activation safely retains the previous `/opt/storage-viz-dashboard` directory as a legacy rollback target.

For the first activation, a pre-existing real `/opt/storage-viz-dashboard` directory is atomically renamed on the same filesystem to `/opt/storage-viz-dashboard.legacy.<timestamp>`. Deployment state records it as `legacy_backup`, not as a release SHA. The existing proxy may be a separate script outside `/opt`; bootstrap therefore validates the exact live process path and snapshots that script before stopping Live into `/var/lib/storage-viz-dashboard/legacy-proxy/<sha256>/direct_proxy.py`. The snapshot is root-owned, group-readable by the Storage runtime, non-writable, and bound to its recorded digest. The new `/opt/storage-viz-dashboard` symlink is then installed. If restart or health validation fails, the symlink is removed, the legacy dashboard directory is atomically restored, and systemd executes only the checksum-verified proxy snapshot rather than the mutable original path. After a successful migration the legacy directory and proxy snapshot remain protected until explicit documented cleanup after a later release and rollback rehearsal.

Activation:

1. reads a bounded artifact from stdin;
2. verifies digest, manifest, archive member types, paths, file count, and expanded size;
3. extracts into a private temporary directory and atomically publishes the immutable release;
4. atomically switches `/opt/storage-viz-dashboard` to the new release;
5. restarts `storage-viz-dashboard.service` and `storage-viz-proxy.service`;
6. runs the health contract;
7. records current/previous release metadata and prunes only unreferenced old releases.

If restart or health validation fails, the active pointer is restored and both services are restarted on the previous release. The failed release remains non-active for diagnosis and cannot replace Live without a future successful activation.

### Managed public proxy

`storage-viz-proxy.service` replaces the legacy proxy process. The service runs as the unprivileged Storage identity and executes an installed root-owned launcher with no caller-selected script argument. The launcher resolves the proxy from deployment state: either the active immutable release's `deploy/direct_proxy.py` or the checksum-bound managed legacy snapshot under `/var/lib/storage-viz-dashboard/legacy-proxy`. The unit grants only `CAP_NET_BIND_SERVICE` through its bounding/ambient capability sets for deployments that bind a privileged port and denies every broader capability; the launcher and proxy never run as root.

The proxy remains independent from GPU Monitor and accepts only GET/HEAD plus the exact bounded manual-rescan POST contract already implemented. Other writes remain blocked.

Bootstrap and every activation fail closed unless `/etc/storage-viz/proxy.env` and `/etc/storage-viz/dashboard.env` form one coherent deployment: proxy port `505`, upstream `127.0.0.1:8088`, an exact HTTP public origin whose authority equals the browser Host, a fixed proxy operator present in the dashboard operator allowlist, trusted-proxy mode enabled, non-secure session cookies explicitly enabled for the internal HTTP origin, and development sample mode absent. Missing, duplicate, malformed, or conflicting keys reject activation rather than falling back to defaults.

The first proxy cutover does not discard the legacy fallback before proving the replacement. With the legacy dashboard still on `127.0.0.1:8088` and its proxy still serving the configured internal listener, bootstrap starts the candidate dashboard on `127.0.0.1:18088` and candidate proxy on `127.0.0.1:1505`. Temporary validated proxy settings target 18088 while requests still carry the real configured public Host/Origin. Candidate dashboard preflight mode is loopback-only, requires production inventory, uses isolated temporary data/state, disables background polling and real rescan job execution, and exists only to exercise candidate startup, routing, session, CSRF, inventory, and bounded POST authorization without touching Live or remote servers. Only after that topology passes does bootstrap validate both live owners, snapshot and verify the exact legacy proxy, stop the validated processes, switch the release, and start the managed services. The snapshot identity remains in process memory until the post-stop activation or a real rollback writes authoritative deployment state. If the managed proxy cannot bind or post-cutover health fails, the legacy dashboard directory is restored, the launcher selects the managed legacy proxy snapshot, and rollback verifies the previous dashboard and inventory through the configured listener. The old tmux session itself is not recreated.

### Preserved state

Automatic deployment never writes or replaces:

- `/etc/storage-viz/servers.json`
- `/etc/storage-viz/dashboard.env`
- `/etc/storage-viz/proxy.env`
- `/etc/storage-viz/keys/`
- `/etc/storage-viz/known_hosts`
- `/var/lib/storage-viz-dashboard/data/`
- `/var/lib/storage-viz-dashboard/state/`
- any remote `storage-viz-scan.service` or timer
- any GPU Monitor path, service, release, user, or port

## Health contract

Activation succeeds only when all of the following hold:

1. dashboard and proxy services are active;
2. public proxy `/api/session` returns valid JSON and `can_rescan: true`;
3. the health checker reads the validated inventory, generates a bounded random server ID matching the backend/proxy grammar, verifies that ID is absent from inventory, retains the session cookie and CSRF token, then sends `{}` through port 505 with the exact configured Host and Origin; health requires backend `404 {"error":"UNKNOWN_SERVER"}`, proving the proxy POST allowlist, fixed identity, cookie, CSRF, Origin, and backend authorization without starting a scan;
4. `/api/servers` reports `data_mode: inventory`;
5. enabled server IDs from `/etc/storage-viz/servers.json` are present in the API response in inventory order;
6. no sample-only server list has replaced production inventory.

This health gate directly covers the recurring manual-rescan-disabled and stale-server-list failures.

## Deployment timing

“Commit immediately deploys” means no human approval or manual copy is required. Deployment begins only after CI succeeds. With a five-minute timer and bounded jitter, normal Live convergence is within several minutes of `ci/required` success while keeping unauthenticated GitHub API usage below practical rate limits.

## Testing

- artifact determinism, allowlist, secret/runtime exclusion, and manifest validation;
- puller evidence, SHA drift, locking, backoff, unchanged-artifact, and activation state machine;
- activator archive traversal/type/size rejection, pointer switch, rollback, and state persistence;
- systemd/install asset contracts and strict Storage/GPU path isolation;
- health-check rejection of read-only sessions, sample mode, missing/reordered servers, and dead services;
- environment-pair rejection for incomplete or conflicting proxy/dashboard settings;
- full `make test`, `make test-storage`, workflow validation, and diff checks.

Deployment assets live under `apps/storage-monitor/deploy/server/` plus `apps/storage-monitor/deploy/build-dashboard-release.py`, `apps/storage-monitor/deploy/direct_proxy.py`, and their tests. `scripts/ci_impact.py` explicitly classifies each of those exact app-local paths as `storage_dashboard`, with regression tests for every path, so `ci/storage` and `ci/required` cannot approve an untested Storage deployment-code change.

## Initial rollout

The repository change can be merged and CI-validated without touching Live. Installing the puller on the Storage host is a separate one-time privileged bootstrap. The bootstrap inspects and preserves the existing runtime/configuration, performs the preflight and rollback-safe tmux-to-systemd proxy cutover, activates the approved `main` release, verifies port 505 with the non-mutating authenticated `UNKNOWN_SERVER` POST readiness probe, then enables the timer. A real scan is never started by deployment health checks; an operator may separately request one after deployment if operationally desired.
