# GitHub CI/CD bootstrap guard

This repository is ready to publish source, pull-request CI, and the trusted-team GPU deployment workflow, but live cutover remains deliberately delayed until the workflow and server-side release path are implemented and verified. The guardrail is `scripts/check_deploy_prerequisites.py`, a read-only checker that reports `READY`, `BLOCKED`, or `UNKNOWN` for repository protection, CODEOWNER enforcement, runner availability, and server reachability. It never changes GitHub settings, registers runners, copies artifacts, restarts services, or writes to the production server; malformed metadata fails closed and never falls back to live inspection.

## Current private-plan limitation

`IRCVLab/GPU_monitor` is a private repository on the current GitHub plan, and `main` branch protection is unavailable/not configured for that private-plan state. That limitation remains explicit: the compensating checks below are not equivalent to branch protection against a malicious authorized writer. They prevent accidental direct-push deployment inside the trusted team model, where only authorized team members have write access.

Required live check:

```bash
python3.12 scripts/check_deploy_prerequisites.py --repo IRCVLab/GPU_monitor
```

The default process exit status is the production `cutover` status, so missing or blocked server reachability can never produce exit `0`. Use `--stage runner` only when evaluating runner-registration readiness. `--stage publication` reports protected-CI readiness; it is not a gate for the repository's first source-only push.

Expected current live result: `BLOCKED` for `protected_main`, with evidence equivalent to `private-plan branch protection unavailable or not configured for main`. Missing branch-protection evidence is `UNKNOWN` rather than `READY`; full branch-protection readiness still requires explicit evidence that administrator enforcement is enabled, force pushes are disabled, and administrator bypass is disabled. Runner enumeration is advisory for historical self-hosted planning only; pull-request and deployment workflows use GitHub-hosted runners.

## Desired `main` protection when the plan allows it

When the GitHub plan allows enforceable branch protection, configure `main` with all of these settings:

1. Require pull request before merging.
2. Require at least one approving review.
3. Require review from Code Owners.
4. Require status checks to pass before merging.
5. Include the exact required check: `ci/required`.
6. Apply the rule to administrators/operators unless an emergency exception is documented out of band.
7. Disable force pushes.
8. Keep `.github/CODEOWNERS`, `.github/workflows/`, deployment controls, prerequisite checker, and operation docs operator-owned.

A protected `main` with `ci/required`, at least one approving review, code-owner review, explicit administrator enforcement, explicit no force pushes, and explicit no administrator bypass is `READY` for protected-main planning. If any of those fields are unavailable from metadata or the API contract, the checker reports `UNKNOWN` and exits nonzero. Any `UNKNOWN` prerequisite exits nonzero and is never treated as `READY`. It is not the same as production cutover approval.

## Runner and deployment credential policy

Pull-request and deployment workflows use GitHub-hosted runners. The deployment credential is environment-scoped and accepted by a server-side forced-command wrapper that cannot execute arbitrary repository-provided shell. Self-hosted production runners remain disabled while branch protection is unavailable.

The trusted-team deployment workflow must verify merged-PR provenance, effective approval, and a fresh successful `ci/required` result for the resulting `main` SHA before it builds or deploys. A direct push to `main` may run CI, but it must not satisfy the deployment condition. Production labels such as `prod`, `production`, `prd`, or `prod-runner` remain denied because production jobs do not use self-hosted runners.

## Initial publication commands

Local pre-publication verification:

```bash
make verify
```

The first publication is source-only and does not require the deployment checker to return `READY`:

```bash
git push origin HEAD
```

Do not register a self-hosted runner, create deployment secrets, restart services, or write to the server as part of source publication. Environment-scoped deployment credentials are created only as part of the reviewed deployment workflow and must target the server-side forced-command wrapper.

## Action and secret policy

- GitHub Actions must use least-privilege permissions; `write-all` is not allowed.
- Third-party and first-party `uses:` actions must be pinned to full lowercase 40-character SHAs.
- `pull_request_target` is not allowed for repository CI.
- Pull-request and deployment jobs must not run on self-hosted or dynamically selected production runners.
- Production deployment secrets must be environment-scoped and accepted only by the server-side forced-command wrapper.
- Store secret values only in GitHub or server secret stores; never commit them or include them in runbooks, logs, or reports.

## Phase 4 artifact and release expectations

Phase 4 deployment must use immutable artifacts and atomic release activation:

1. Build each application artifact from the exact successful main SHA after merged-PR provenance, effective approval, and fresh `ci/required` verification.
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

## Storage agents remain manual/tagged

Storage agents remain manual and tagged. They must not auto-deploy from `main`. Operator rollout requires an explicit reviewed tag or manual dispatch, host allowlisting, exact artifact identity, and a rollback note for every target host.

## Server reachability and SSH timeout

Server reachability is a cutover blocker, not a source-publication blocker. The known production SSH route is `166.104.167.11:2200`. Bounded read-only SSH failures, including the previously observed timeout class and the current non-interactive permission-denied result from this environment, block production cutover and live service changes. They do not block publishing source, opening pull requests, or running GitHub-hosted CI.

Only run a live host check when explicitly requested:

```bash
python3.12 scripts/check_deploy_prerequisites.py \
  --repo IRCVLab/GPU_monitor \
  --check-host 166.104.167.11:2200
```

Without `--check-host`, the checker reports server reachability as `UNKNOWN`, exits nonzero for the default `cutover` stage, and does not contact the server. With `--check-host`, it validates the host target, rejects option-injection or malformed user/host/port values, and runs only `ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 ... true` with a subprocess timeout as a second bound; it does not request credentials or mutate the server. When `--metadata-file` and `--check-host` are supplied together, the fresh bounded probe overrides any stale `serverReachability` value in the metadata file.

## Fail-closed metadata handling

- `CODEOWNERS` live fetch treats only `HTTP 404` as absence. Authentication, authorization, rate-limit, malformed response, and 5xx failures are `UNKNOWN` and fail closed.
- The checker copies supplied metadata dictionaries before deriving fields so caller-owned fixtures and API payloads are not mutated by evaluation.
