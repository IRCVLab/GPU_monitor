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

`storage-viz-proxy.service` replaces tmux ownership of port 505. It executes the active release's `deploy/direct_proxy.py` and reads `/etc/storage-viz/proxy.env`.

The proxy remains independent from GPU Monitor and accepts only GET/HEAD plus the exact bounded manual-rescan POST contract already implemented. Other writes remain blocked.

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
3. `/api/servers` reports `data_mode: inventory`;
4. enabled server IDs from `/etc/storage-viz/servers.json` are present in the API response in inventory order;
5. no sample-only server list has replaced production inventory.

This health gate directly covers the recurring manual-rescan-disabled and stale-server-list failures.

## Deployment timing

“Commit immediately deploys” means no human approval or manual copy is required. Deployment begins only after CI succeeds. With a five-minute timer and bounded jitter, normal Live convergence is within several minutes of `ci/required` success while keeping unauthenticated GitHub API usage below practical rate limits.

## Testing

- artifact determinism, allowlist, secret/runtime exclusion, and manifest validation;
- puller evidence, SHA drift, locking, backoff, unchanged-artifact, and activation state machine;
- activator archive traversal/type/size rejection, pointer switch, rollback, and state persistence;
- systemd/install asset contracts and strict Storage/GPU path isolation;
- health-check rejection of read-only sessions, sample mode, missing/reordered servers, and dead services;
- full `make test`, `make test-storage`, workflow validation, and diff checks.

## Initial rollout

The repository change can be merged and CI-validated without touching Live. Installing the puller on the Storage host is a separate one-time privileged bootstrap. The bootstrap inspects and preserves the existing runtime/configuration, replaces the tmux proxy with systemd, activates the approved `main` release, verifies port 505 and a real bounded rescan request, then enables the timer.
