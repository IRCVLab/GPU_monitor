# Development and Release Workflow Design

## Status

Approved design for the monitoring platform repository. This document defines
the intended branch model, shared development-server role, pull-request flow,
and GPU live-release boundary. It does not activate deployment by itself.

## Context

`IRCVLab/GPU_monitor` is a private organization repository. Only authorized
team members have write access, so external users cannot push directly. The
current GitHub plan does not provide enforceable branch protection for this
private repository, so repository policy and deployment checks must temporarily
compensate for the missing protection.

Verified server inventory from 2026-07-23 records the live GPU release at
`f2ea62f` and the development GPU candidate at `64c4b83`. SSH access works,
and the current GPU development and live runtimes are isolated tmux stacks.

The repository is a monorepo with two independently deployable products:

- `apps/gpu-monitor`
- `apps/storage-monitor`

The current GPU development implementation is expected to become the next live
version. The existing live history remains preserved under migration archive
refs until archive-branch cleanup is completed.

## Goals

- Keep day-to-day development simple for a small internal team.
- Make local clones the source of code changes.
- Use the shared development server only for integration validation.
- Validate the pull-request head on the shared development server, then deploy
  the separately validated resulting `main` commit.
- Make a successful reviewed `main` commit the GPU live-release source.
- Keep GPU and Storage deployment and rollback independent.
- Remove archive branches from the normal branch list without losing history.

## Non-goals

- A permanent `develop` branch.
- Editing uncommitted source directly on the shared development server.
- Automatically deploying Storage agents from `main`.
- Coupling GPU and Storage runtime releases.
- Deleting any archive ref before its exact object ID is preserved and verified.

## Branch and tag model

### Active branches

`main` is the only long-lived active branch.

Developers create short-lived branches such as:

```text
feature/<topic>
fix/<topic>
docs/<topic>
```

The branch is deleted after its pull request is merged or abandoned.

### Migration archives

The existing `archive/*` branches are migration evidence, not active
development branches. They should be converted to annotated tags and then
removed from the remote branch namespace.

The cleanup procedure is:

1. Record every archive branch name and exact tip object ID.
2. Create one annotated tag for every tip using a stable namespace such as:

   ```text
   archive/branch/gpu-dev/develop
   archive/branch/gpu-live/main
   archive/branch/storage/master
   ```

3. Verify every former branch tip is the exact target of a published tag.
4. Verify the tagged object retains the branch's reachable history.
5. Delete the remote `archive/*` branches.
6. Keep the migration inventory in `docs/history-migration.md`.

Existing duplicate checkpoint tags are not part of the initial cleanup. They
may be consolidated later only after a separate ref audit.

After cleanup, the normal remote branch list should contain only `main` plus
temporary pull-request branches.

## Developer workflow

### Local-first development

Each contributor:

1. Clones the repository locally.
2. Creates a short-lived feature or fix branch.
3. Changes only the owning application and shared governance files required by
   the task.
4. Runs app-local checks during development.
5. Runs the applicable root checks before requesting integration.
6. Pushes the branch and opens a pull request to `main`.

The shared development server is not a source-editing workspace. It must run
committed code identified by an immutable Git SHA.

### Pull-request validation

Every pull request runs:

- `ci/repository`
- path-selected GPU and Storage checks
- `ci/required`

The expected team policy is:

- no ordinary direct pushes to `main`;
- no force pushes to `main`;
- at least one teammate reviews application or deployment changes;
- `ci/required` must succeed before merge.

These are team rules until the repository plan supports enforceable protection.
Only authorized team members have write access, which limits the temporary
exposure to accidental or intentional actions by that trusted group.

## Shared development server

The development server is a single staging slot for integration testing against
real GPU, network, and internal-service conditions.

It must:

- deploy one explicit pull-request commit SHA at a time;
- display or log the deployed SHA, branch, author, and deployment time;
- reject deployment when the working tree is dirty;
- serialize deployments so two contributors cannot overwrite each other;
- preserve logs for the previous candidate;
- never modify the live release;
- allow the current candidate to be replaced by another committed candidate.

Local development remains the default. The shared server is used when behavior
depends on real GPUs, internal networking, SSH collection, or realistic runtime
integration.

## GPU release flow

The intended GPU SHA contract is:

```text
PR head SHA -> CI -> shared dev validation
GitHub merge -> resulting main SHA -> fresh ci/required
merged-PR provenance + effective approval verification
build and deploy the exact successful main SHA
```

Once this workflow is implemented, no separate human production approval is
required after the reviewed merge and successful `main` CI. The merge is the
release authorization point, while post-merge CI validates the final release
identity. Same-tree or same-SHA equivalence between the PR head and resulting
`main` commit is not assumed. Live cutover remains delayed until the
GitHub-hosted deployment workflow builds an immutable artifact from that exact
successful main SHA and atomically activates it on the server.

Because branch protection is not currently enforceable, the production
workflow must additionally verify:

1. The event targets `main`.
2. `ci/required` succeeded freshly for the exact resulting main SHA.
3. The main SHA is associated with a merged pull request targeting `main`.
4. The merged pull request has effective approval from a teammate.
5. The artifact SHA matches the deployed source SHA.
6. The live deployment uses an application-local release directory.

A direct push to `main` may run CI, but it must not satisfy the merged-PR
deployment condition.

## Deployment and rollback

GPU deployment uses immutable, SHA-addressed releases:

```text
releases/gpu/<commit-sha>/
current -> releases/gpu/<commit-sha>/
```

Deployment order:

1. Build from the exact successful main SHA.
2. Upload to a new release directory.
3. Validate checksums and configuration.
4. Run application-local pre-activation checks.
5. Atomically switch the `current` pointer.
6. Restart only GPU services.
7. Run live health checks.

If activation or health checks fail:

1. Restore the previous GPU release pointer.
2. Restart only GPU services.
3. Record the failed and restored SHAs.
4. Leave Storage services untouched.

## Storage release boundary

Storage dashboard releases may use the same reviewed-SHA and atomic-release
principles but remain a separate deployment target.

Storage scanner and agent rollout remains manual and tag-driven because it
changes multiple monitored hosts and has a different operational risk profile.
A GPU change must not trigger a Storage deployment, and a Storage dashboard
change must not restart GPU services.

## Concurrency and failure handling

- Dev deployments use a single concurrency group and cancel or queue older
  pending candidates according to operator policy.
- GPU live deployments serialize against other GPU live deployments.
- Storage deployments use separate concurrency groups.
- A failed CI run produces no artifact activation.
- A failed upload leaves the current release unchanged.
- A failed health check triggers GPU-local rollback.
- Deployment logs record source SHA, artifact digest, previous release, and
  final state.

## Required verification

Before implementing deployment:

- confirm the remote development server has no uncommitted changes;
- compare its deployed SHA with repository `main`;
- verify current live process, ports, service units, and rollback path;
- verify the development and live stacks remain isolated;
- verify GitHub credentials and server secrets are stored outside the
  repository;
- test deployment and rollback against the development environment first.

## Acceptance criteria

- Only `main` remains as a long-lived active remote branch.
- Every deleted archive branch tip is preserved by a verified annotated tag.
- Contributors can develop and test locally without using the shared server.
- The shared dev server runs only committed, identifiable SHAs.
- PR CI blocks integration by team policy.
- The PR head SHA validated on dev may differ from the resulting main SHA.
- The reviewed merge authorizes release, and fresh `main` CI validates the
  final release identity before deployment.
- Successful `main` CI can automatically deploy GPU live without another manual
  approval.
- Direct pushes to `main` cannot trigger live deployment.
- GPU rollback does not affect Storage.
- Storage agents never auto-deploy from `main`.
