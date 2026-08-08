# GPU Live Promotion and Automatic Deployment Design

**Date:** 2026-08-08  
**Status:** Approved for implementation

## Objective

Promote the current GPU Monitor development line into the supported production
application, clean development-only and generated clutter before release, and
restore the intended release contract:

```text
trusted push to main
  -> path-aware CI
  -> exact-SHA production build
  -> server-side authorization of successful CI
  -> candidate validation against preserved Live data
  -> atomic Live activation
  -> post-activation health and data checks
  -> automatic rollback on failure
```

Storage Monitor remains independent. Storage changes continue to run CI but do
not enter the GPU deployment path.

## Current State and Failure to Correct

The current restored Live service runs from
`/home/ircv/workspace/monitoring_v2` in three tmux sessions on ports 5173,
8001, and 8000. Its SQLite database contains nine registered servers.

The existing outbound puller and immutable-release implementation are sound in
principle, but the first cutover violated the product boundary in two ways:

1. The monorepo `main` GPU tree was the development line and had not been
   explicitly promoted as the production source.
2. The managed Live environment used a metrics database whose `servers` table
   was empty instead of the restored Live database containing the nine server
   registrations.

Automatic deployment stays disabled until both conditions are corrected and a
candidate release has passed production-data compatibility checks.

## Source and Branch Contract

- `main` is the production trunk.
- Trusted team members may push directly to `main`; pull requests remain
  optional.
- Local or feature branches are development space and never deploy.
- The current monorepo GPU application becomes production only through this
  explicit promotion change.
- Historical pre-promotion Live and development snapshots remain preserved by
  existing archive refs and tags.
- A GitHub `main` push may authorize GPU deployment only when that exact SHA is
  still current and `ci/required` succeeded for the same push workflow.

## Cleanup and Promotion Boundary

Cleanup is behavior-preserving unless a component is explicitly classified as
development-only.

### Remove or exclude

- Generated build output, caches, local databases, virtual environments,
  browser artifacts, and machine-local deployment keys.
- Production exposure of the `/debug` scenario route and development scenario
  controls.
- Obsolete launchers or documentation that describe a permanent shared Dev
  server.
- Duplicate or stale deployment documentation contradicted by the outbound
  puller design.

### Preserve

- Local debug scenarios behind an explicit local-development boundary when
  they remain useful for UI testing.
- The current Full and Compact product views, theme preferences, notes/holds,
  server administration, telemetry collection, Slack bridge, and runtime
  health behavior.
- App-local ownership: GPU and Storage must not import each other's code.
- Existing deployment hardening, immutable artifacts, exact-SHA authorization,
  locking, backoff, health checks, and automatic release-pointer rollback.

No new runtime dependency is added solely for cleanup.

## Data Preservation and Migration

The server-local SQLite database is mutable production state and is never part
of a release artifact. The managed Live environment must use a stable path
under `/var/lib/gpu-monitor/live/` and must not create an empty replacement DB
when a source database already exists.

Before the first managed cutover:

1. Stop no existing Live process while inspecting or copying state.
2. Create a SQLite online backup of the restored Live database and run
   `PRAGMA integrity_check` on the backup.
3. Record invariant counts for registered servers, notes, and other mutable
   configuration tables.
4. Run the promoted backend against a disposable copy of that backup with
   collectors and Slack disabled.
5. Allow startup schema synchronization on the disposable copy only.
6. Verify that all nine server registrations and existing notes survive and
   that the promoted API can list them.
7. Provision the managed Live database from the verified copy or an atomic
   same-filesystem publication of it.

The first activation must fail closed if the server-registration count becomes
zero, decreases unexpectedly, database integrity fails, or the API cannot list
the expected server identities. Metrics-table row counts may grow during normal
operation and are not exact rollback invariants.

## Release and Activation Flow

The existing outbound-only server puller remains the transport:

1. The timer polls public GitHub evidence for current `main`.
2. It requires a successful exact-SHA `ci/required` result.
3. A dedicated builder checks out and builds that SHA in a clean directory.
4. The release artifact excludes secrets, databases, local debug state, and
   generated source-tree residue.
5. A pre-activation data guard validates the configured Live DB and expected
   registered-server floor.
6. Activation publishes an immutable release and starts managed systemd units.
7. Health validation checks unit ownership, stable PIDs, exact listeners,
   browser-facing health, WebSocket behavior, and server-list invariants.
8. Any failure restores the prior release pointer and managed runtime.

The legacy tmux stack is the one-time migration fallback, not the steady-state
release target. It remains available until the first promoted release and one
subsequent no-op puller cycle are verified.

## Deployment Selectivity

- A GPU application or GPU deploy-path change runs GPU CI and may produce a new
  GPU Live release.
- A Storage-only change runs Storage CI and must not restart or replace GPU
  Live.
- A documentation-only change may advance `main` but must not restart GPU Live
  when the GPU release payload is unchanged.
- Shared CI or release-policy changes run the required repository checks; they
  do not bypass GPU application validation.

To avoid needless Live restarts, the puller records both the authorized Git SHA
and a deterministic GPU release digest. If a newer successful `main` commit has
the same GPU release digest as the active release, it updates deployment state
without restarting Live.

## Failure Handling and Rollback

- Failed CI: no build and no deployment.
- Stale SHA: no deployment.
- Build or artifact validation failure: current Live remains untouched.
- DB backup, integrity, schema, or invariant failure: no cutover.
- Activation or health failure: restore the previous immutable release.
- First managed cutover failure: stop failed managed units and restore the
  legacy tmux stack on the original ports.
- Puller failures retain bounded exponential backoff and are visible in
  systemd status and deployment JSONL state.

No deployment step modifies Storage Monitor, its service, port 8088, or its
data.

## Testing Strategy

### Repository tests

- Lock current product behavior before cleanup.
- Assert production artifacts do not expose the debug route or include local
  debug-state modules unnecessarily.
- Test legacy Live DB compatibility using a privacy-safe fixture with registered
  servers and legacy note rows.
- Test database invariant validation and fail-closed behavior.
- Test path-selective deployment and no-op deployment for unchanged GPU digest.
- Retain full backend, frontend, release-script, puller, workflow-policy, and
  Storage regression suites.

### Candidate verification

- Build the exact promotion SHA from a clean checkout.
- Run backend/frontend/bridge on non-production candidate ports.
- Use a disposable online backup of the real Live DB.
- Verify health, WebSocket connection, nine server identities, notes, and
  administration read paths.
- Confirm collectors and Slack are disabled in candidate mode.

### Production verification

- Snapshot Live and Storage service identities, PIDs, listeners, and health.
- Perform one guarded cutover.
- Verify external frontend health and nine server registrations.
- Verify collector freshness after one normal collection interval.
- Verify Slack bridge health without sending test messages.
- Verify Storage PID/start time and HTTP 200 remain unchanged.
- Verify the next puller cycle is a no-op.

## Completion Criteria

- The promoted GPU app is explicitly documented as production code.
- Production artifacts do not expose development-only debug scenarios.
- Full repository verification passes from a clean worktree.
- A copied real Live DB passes integrity, schema, and nine-server compatibility
  validation before cutover.
- The first managed release serves the expected external endpoints and all nine
  registered servers.
- Failed releases roll back without requiring an operator.
- Successful future GPU changes on `main` automatically deploy after CI.
- Storage-only and documentation-only changes do not restart GPU Live.
- Storage Monitor remains healthy and unchanged during the migration.

