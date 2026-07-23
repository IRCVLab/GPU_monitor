# GitHub CI/CD bootstrap guard

This repository is ready to publish source and pull-request CI, but production deployment remains deliberately guarded. The guardrail is `scripts/check_deploy_prerequisites.py`, a read-only checker that reports `READY`, `BLOCKED`, or `UNKNOWN` for repository protection, CODEOWNER enforcement, runner availability, and server reachability. It never changes GitHub settings, registers runners, copies artifacts, restarts services, or writes to the production server; malformed metadata fails closed and never falls back to live inspection.

## Current blocker

`IRCVLab/GPU_monitor` is a private repository on the current GitHub plan, and `main` branch protection is unavailable/not configured for that private-plan state. Until `main` has branch protection, deployment bootstrap is blocked even though source publication and pull-request CI files can be prepared locally.

Required live check:

```bash
python3.12 scripts/check_deploy_prerequisites.py --repo IRCVLab/GPU_monitor
```

Expected current live result: `BLOCKED` for `protected_main`, with evidence equivalent to `private-plan branch protection unavailable or not configured for main`. Missing branch-protection evidence is `UNKNOWN` rather than `READY`; `READY` requires explicit evidence that administrator enforcement is enabled, force pushes are disabled, and administrator bypass is disabled. If runner enumeration cannot read the repo/org runner APIs, `runner_availability` must be `UNKNOWN`, not treated as accepted.

## Required `main` protection before runner registration

Before registering or authorizing any production runner, configure `main` with all of these settings:

1. Require pull request before merging.
2. Require at least one approving review.
3. Require review from Code Owners.
4. Require status checks to pass before merging.
5. Include the exact required check: `ci/required`.
6. Apply the rule to administrators/operators unless an emergency exception is documented out of band.
7. Disable force pushes.
8. Keep `.github/CODEOWNERS`, `.github/workflows/`, deployment controls, prerequisite checker, and operation docs operator-owned.

A protected `main` with `ci/required`, at least one approving review, code-owner review, explicit administrator enforcement, explicit no force pushes, and explicit no administrator bypass is `READY` for runner-registration planning. If any of those fields are unavailable from metadata or the API contract, the checker reports `UNKNOWN` and exits nonzero. Any `UNKNOWN` prerequisite exits nonzero and is never treated as `READY`. It is not the same as production cutover approval.

## Why the production runner is not installed yet

The production runner is intentionally not installed because a self-hosted runner connected before branch protection and CODEOWNER enforcement would create a deployment path that is stronger than the repository's review controls. Runner installation may start only after the checker reports `READY` for protected `main`, CODEOWNER enforcement, and runner availability. Runner availability is based on actual repo/org runner enumeration and requires at least one online eligible runner; runner-group API readability or permission to inspect runner groups alone is not sufficient for `READY`. Permission or API uncertainty remains `UNKNOWN`.

Pull-request CI must continue to use GitHub-hosted runners. Production labels such as `prod`, `production`, `prd`, or `prod-runner` are reserved for deployment jobs and remain denied for normal PR jobs by workflow policy validation.

## Initial publication commands

Local pre-publication verification:

```bash
make verify
python3.12 scripts/check_deploy_prerequisites.py --repo IRCVLab/GPU_monitor
```

Publication is source-only until the prerequisite report is ready:

```bash
git push origin HEAD
```

Do not register a self-hosted runner, create deployment secrets, restart services, or write to the server as part of source publication.

## Action and secret policy

- GitHub Actions must use least-privilege permissions; `write-all` is not allowed.
- Third-party and first-party `uses:` actions must be pinned to full lowercase 40-character SHAs.
- `pull_request_target` is not allowed for repository CI.
- Pull-request jobs must not run on self-hosted or dynamically selected production runners.
- Production secrets must not be created until branch protection, CODEOWNER review, runner authorization, and cutover authority are all documented.
- Store secret values only in GitHub or server secret stores; never commit them or include them in runbooks, logs, or reports.

## Phase 4 artifact and release expectations

Phase 4 deployment must use immutable artifacts and atomic release activation:

1. Build each application artifact from a reviewed commit SHA.
2. Name or address artifacts by commit SHA/content digest.
3. Upload artifacts without modifying the active release.
4. Validate checksums and application-local smoke checks.
5. Flip the active release atomically with a symlink or equivalent release pointer.
6. Keep the previous release available for rollback.

## GPU and Storage concurrency and rollback

GPU and Storage dashboards are independent products. Their deployment jobs may run concurrently only when they target separate release directories, service units, health checks, and rollback pointers. A failed GPU deployment must not roll back Storage, and a failed Storage deployment must not roll back GPU.

Rollback must be product-local:

- GPU dashboard/backend: restore the previous GPU release pointer and restart only GPU services.
- Storage dashboard: restore the previous Storage dashboard release pointer and restart only dashboard services.
- Shared repository governance changes are not a runtime rollback mechanism.

## Storage agent rollout remains manual/tagged

The Storage agent rollout remains manual and tagged. It must not auto-deploy from `main`. Operator rollout requires an explicit reviewed tag or manual dispatch, host allowlisting, exact artifact identity, and a rollback note for every target host.

## Server reachability and SSH timeout

Server reachability is a cutover blocker, not a source-publication blocker. The known production SSH route is `166.104.167.11:2200`. Bounded read-only SSH failures, including the previously observed timeout class and the current non-interactive permission-denied result from this environment, block production cutover and live service changes. They do not block publishing source, opening pull requests, or running GitHub-hosted CI.

Only run a live host check when explicitly requested:

```bash
python3.12 scripts/check_deploy_prerequisites.py \
  --repo IRCVLab/GPU_monitor \
  --check-host 166.104.167.11:2200 \
  --require-host-for-cutover
```

Without `--check-host`, the checker reports server reachability as `UNKNOWN` and does not contact the server. With `--check-host`, it validates the host target, rejects option-injection or malformed user/host/port values, and runs only `ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 ... true` with a subprocess timeout as a second bound; it does not request credentials or mutate the server. When `--metadata-file` and `--check-host` are supplied together, the fresh bounded probe overrides any stale `serverReachability` value in the metadata file.

## Fail-closed metadata handling

- `CODEOWNERS` live fetch treats only `HTTP 404` as absence. Authentication, authorization, rate-limit, malformed response, and 5xx failures are `UNKNOWN` and fail closed.
- The checker copies supplied metadata dictionaries before deriving fields so caller-owned fixtures and API payloads are not mutated by evaluation.
