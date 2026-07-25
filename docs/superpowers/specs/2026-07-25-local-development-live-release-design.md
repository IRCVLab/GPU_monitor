# Local Development and Live Release Design

**Date:** 2026-07-25  
**Status:** Approved direction

## Objective

Remove the always-on shared GPU development deployment from the supported
platform model. Developers run and validate changes on their own machines,
GitHub CI validates pull requests, and an authorized merge to `main` deploys
the exact successful commit to the existing live service.

## Rationale

The current GPU development service was created to support the active redesign
and release-path rehearsal. It is not a shared product requirement. Keeping it
as permanent infrastructure adds a second deployment identity, environment,
release slot, backend collector, systemd pair, and SSH tunnel that future
contributors do not need.

Local development is sufficient for ordinary frontend and backend work. CI
provides reproducible repository validation. The live release path already
builds immutable artifacts, validates provenance, performs browser-facing
health checks, and rolls back a failed activation.

## Supported Workflow

1. A contributor clones the repository and develops locally.
2. The contributor runs the documented application-local checks.
3. A pull request runs `ci/required` and path-aware application checks.
4. The pull request is reviewed and merged into `main`.
5. Successful `main` CI authorizes the exact merged SHA for live deployment.
6. The server activates the immutable live release.
7. Failed health checks restore the previous live release automatically.

There is no permanent shared development URL or development deployment
environment in the supported workflow.

## Repository Changes

- Remove `.github/workflows/deploy-gpu-dev.yml`.
- Remove `gpu-dev` GitHub Environment and its secrets if they exist.
- Remove documentation that treats shared development deployment as a required
  release stage.
- Keep local development, debug scenarios, and test instructions.
- Keep the live deployment workflow, immutable build, forced-command protocol,
  health checks, status records, and rollback behavior.
- Simplify workflow-policy and repository-contract tests so they require only
  the live deployment lane.
- Preserve historical development rehearsal evidence as historical evidence,
  clearly marked as retired rather than current operating procedure.

## Server Changes

Retire only development-lane resources:

- Stop and disable `gpu-monitor-backend@dev.service`.
- Stop and disable `gpu-monitor-frontend@dev.service`.
- Remove the development forced-command authorization.
- Retire the `gpu-deploy-dev` account and development release state only after
  verifying no live path references them.
- Remove the local `15174 -> 5174` SSH forwarding loop.

Do not modify:

- `gpu-monitor-backend@live.service` or the currently running legacy live units.
- Live ports `5173`, `8001`, or `8000`.
- Storage Monitor, port `8088`, or its service and data.
- The live release pointer, live credentials, or live rollback history.

The first server pass stops and disables development services but preserves the
development release files as a reversible checkpoint. Account and file deletion
is a later cleanup after the live-only workflow has been verified.

## GitHub Configuration

Create only the `gpu-live` Environment. Configure its five environment secrets:

- `GPU_DEPLOY_HOST`
- `GPU_DEPLOY_PORT`
- `GPU_DEPLOY_USER`
- `GPU_DEPLOY_SSH_KEY`
- `GPU_DEPLOY_KNOWN_HOSTS`

No environment-level manual approval is required. Live authorization remains
the reviewed merged-PR provenance check implemented by the repository.

Protect `main` with pull-request and `ci/required` requirements now that the
repository is public. Disable force pushes and branch deletion. The protection
rule is configured only after the workflow has been published and the required
check context exists.

## Local Development

Documentation must provide copy-paste-ready local commands for:

- GPU backend setup and tests.
- GPU frontend setup, checks, build, and development server.
- Local backend/frontend environment variables.
- Debug scenario use without affecting live state.
- Storage Monitor development as an independent application in the monorepo.

Machine-specific environment files, databases, SSH keys, build output, and scan
data remain ignored.

## Verification

Repository verification:

- Root contract tests pass.
- Workflow policy tests pass with one deployment lane.
- GPU backend tests pass.
- GPU frontend runtime tests, checks, and build pass.
- Storage tests and deploy-asset tests remain unchanged and pass.
- `make verify` passes from a clean worktree.

Server verification:

- Dev ports `5174` and `8101` no longer listen after retirement.
- Dev systemd units are inactive and disabled.
- Live frontend and backend health remain unchanged.
- Storage health and restart counters remain unchanged.
- The local `15174` tunnel is absent while the live and Storage tunnels remain.

## Rollback

Repository changes are reverted by restoring the removed workflow and policy
contracts from Git history.

The first server retirement pass does not delete development releases or
accounts. If needed, re-enable and start the two development units to restore
the previous development slot. This rollback does not touch live or Storage.

## Completion Criteria

The change is complete when:

- The repository documents local development as the only development mode.
- No supported GitHub workflow or secret references `gpu-dev`.
- The retired development services and local development tunnel are stopped.
- Live and Storage remain healthy.
- The branch passes full verification.
- Destructive development-account and release-file deletion remains explicitly
  deferred until after live-only deployment has been exercised successfully.
