# GitHub CI/CD bootstrap guard

This repository publishes source with pull-request CI and runs the live deployment workflow from successful same-repository `main` pushes.

`scripts/check_deploy_prerequisites.py` is the legacy branch-protected/self-hosted readiness model. It reports `READY`, `BLOCKED`, or `UNKNOWN` for repository protection, CODEOWNER enforcement, runner availability, and server reachability, then merges `protected_main`, `codeowner_enforcement`, `runner_availability`, and `server_reachability` into its default `cutover` status. It never changes GitHub settings, registers runners, copies artifacts, restarts services, or writes to the production server; malformed metadata fails closed and never falls back to live inspection. This checker is not the authorization gate for live deployment in the current contract, and it must be re-scoped for other planning models.

## Legacy prerequisite checker (not a live authorization gate)

`IRCVLab/GPU_monitor` is a private repository on the current GitHub plan, and `main` branch protection is unavailable/not configured for that private-plan state. This section is retained as historical operational context; it is not current authorization logic.

Legacy checker command (informational only):

```bash
python3.12 scripts/check_deploy_prerequisites.py --repo IRCVLab/GPU_monitor
```

The default process exit status is the legacy production `cutover` status, so missing or blocked branch protection, runner availability, or server reachability can never produce exit `0`. Use `--stage runner` only when evaluating the legacy runner-registration readiness model. `--stage publication` reports protected-CI readiness; it is not a gate for the repository's first source-only push.

Legacy cutover states are informational in this environment. In the absence of enforceable branch-protection metadata, checker output commonly remains `BLOCKED`/`UNKNOWN` for protected-main readiness. That status is expected for the legacy model and does not gate source publication or the current live deployment contract described below.

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

Pull-request and deployment workflows use GitHub-hosted runners. The deployment credential is environment-scoped and accepted by a server-side forced-command wrapper that cannot execute arbitrary repository-provided shell. Self-hosted production runners remain disabled.

## Current GitHub-hosted live authorization

The trusted-team deployment workflow authorizes the exact push SHA directly from `ci` provenance. Direct-main automatic deployment is a trusted-team policy: trusted writers can modify candidate code, CI, workflows, and the authorizer, so these repository-side checks reduce accidents but are not protection against malicious or compromised trusted writers. Branch protection with required review is the stronger future control when the plan allows it. A direct push to `main` is valid if:

- the workflow is `ci`;
- event `push`;
- `main` branch;
- workflow `status: completed` and `conclusion: success`;
- `head_repository.full_name` equals `IRCVLab/GPU_monitor`;
- workflow `path: .github/workflows/ci.yml`;
- latest `ci/required` check is successful for the same `head_sha`;
- the workflow SHA is still the current `main` head when the authorizer runs.

Pull requests and manual review are optional for current operator flow; they are not required deployment gate criteria.

Legacy readiness checks are retained for historical and planning context only. The current live authorization path is the GitHub-hosted workflow criteria above and does not use `scripts/check_deploy_prerequisites.py` as a gate.

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

1. Build each application artifact from the exact successful main SHA after provenance and fresh `ci/required` verification.
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

Server reachability is a legacy checker cutover input, not a source-publication blocker. The known production SSH route is `166.104.167.11:2200`. Bounded read-only SSH failures, including the previously observed timeout class and the non-interactive permission-denied result from this environment, are legacy status data and do not block publishing source, opening pull requests, or running GitHub-hosted CI.

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

## Task 6 GPU deployment workflows

Only one supported GitHub-hosted GPU deployment lane exists (`gpu-live`). It builds the immutable GPU release artifact from the exact SHA being deployed and sends only the closed forced-command protocol to the server:

```text
upload live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
status live
```

The workflow validates SHA, digest, host, user, port, and final `user@host` target before invoking SSH. SSH runs with `BatchMode=yes`, `StrictHostKeyChecking=yes`, an environment-scoped `UserKnownHostsFile`, `IdentitiesOnly=yes`, the environment-scoped key, and the validated port. The checkout ref, build SHA, upload SHA, activation SHA, and status SHA source are the same resolved SHA for the workflow run.

Configure these exact GitHub environment secrets on `gpu-live`:

```text
GPU_DEPLOY_HOST
GPU_DEPLOY_PORT
GPU_DEPLOY_USER
GPU_DEPLOY_SSH_KEY
GPU_DEPLOY_KNOWN_HOSTS
```

`GPU_DEPLOY_KNOWN_HOSTS` must contain the expected host key line for `GPU_DEPLOY_HOST`/`GPU_DEPLOY_PORT`. Do not store these values in repository files, logs, artifacts, or comments.

### Live deployment (`gpu-live`)

`.github/workflows/deploy-gpu-live.yml` is triggered by a completed `ci` `workflow_run`; it then fails closed unless the event and authorizer evidence satisfy:

```text
workflow name ci
event push
branch main
status completed
conclusion success
head repository IRCVLab/GPU_monitor
workflow path .github/workflows/ci.yml
latest ci/required check successful for the exact head SHA
current main head still equals the exact head SHA at authorization time
```

The workflow uses `scripts/authorize_gpu_release.py` to re-validate that provenance before build/deploy and compares the workflow SHA with the current `main` head immediately before activation. There is a narrow race between this immediate current-main recheck and activation: if `main` advances after the recheck but before activation, the just-deployed SHA may no longer be current, but the deployed SHA still passed `main` CI and the closed forced-command deployment path.

Live authorization and secret access are separate jobs. The `authorize` job has no deployment environment and no deployment secrets; it uses repository checkout and the GitHub token context only as needed to run `python3.12 scripts/authorize_gpu_release.py`. The secret-bearing deploy job has `needs: authorize`, `environment: gpu-live`, `concurrency.group: gpu-live`, and `cancel-in-progress: false`, so an in-progress live deployment is never cancelled by a newer run. It checks out `github.event.workflow_run.head_sha`, builds that exact SHA, and deploys only that SHA.

### Status and rollback

Each successful deployment records remote `status live` through the forced-command wrapper. For manual inspection, use the matching deploy identity and forced command configured on the server. Rollback remains local to the GPU live slot:

```text
rollback live
```

The server-side forced command enforces the live rollback boundary.

> Historical evidence: the shared GPU development lane was retired by
> `docs/superpowers/specs/2026-07-25-local-development-live-release-design.md`.
> The following records describe the completed rehearsal and are not current
> operating instructions.

## Development-slot rehearsal evidence

Rehearsal date: 2026-07-24 KST.

- Preserved live baseline: `f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce` on the existing live checkout, with ports `5173`, `8001`, and `8000` healthy.
- Preserved Storage isolation: `storage-viz-dashboard.service` remained active and port `8088` remained healthy.
- Managed development activation: `4caf92ff9fea2eff7047d89e5f9a5eb7cd15b751` activated successfully in `/srv/gpu-monitor/dev`.
- Browser-facing verification: frontend `/`, proxied `/api/health`, proxied `/api/servers`, and the end-to-end `/ws/metrics` upgrade health check passed through port `5174`; the backend remained loopback-only on `8101`.
- Service ownership: `gpu-monitor-backend@dev.service` and `gpu-monitor-frontend@dev.service` are active, and the legacy development tmux sessions are absent.
- A first activation attempt exposed a systemd symlink-entrypoint defect. The release failed health and the legacy tmux development service was restored before the corrected candidate was activated. Regression coverage now executes `server.mjs` through a `current` directory symlink.
- Rollback rehearsal: documentation-only candidate `21d9d8c4c55af1f28330d2b8735da16e5b7a09d3` activated successfully with `4caf92ff9fea2eff7047d89e5f9a5eb7cd15b751` as `previous`; `rollback dev` then restored `4caf92ff9fea2eff7047d89e5f9a5eb7cd15b751` as `current`. Backend, frontend root, proxied API, proxied WebSocket health, live SHA/ports, and Storage remained healthy after rollback.
- GitHub `gpu-dev` environment secrets and workflow dispatch remain intentionally pending because this feature branch has not been pushed and no same-repository pull request exists yet. No GitHub secret, branch push, pull request, or live activation was performed during this server-local rehearsal.
