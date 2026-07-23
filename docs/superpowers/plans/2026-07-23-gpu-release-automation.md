# GPU Release Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reviewed-merge-only GPU deployment pipeline that stages an explicit pull-request commit on the shared development slot, deploys the successfully revalidated `main` SHA to live without a second approval, and preserves independent Storage operation.

**Architecture:** GitHub-hosted runners perform CI, provenance checks, and release packaging. A dedicated SSH key reaches a server-owned forced-command wrapper that accepts only validated GPU release upload, activation, status, and rollback operations; it cannot open an arbitrary shell. Development and live use separate release roots, locks, ports, process names, state, and rollback pointers. The current tmux runtime remains untouched until a development-slot rehearsal proves the new release tooling.

**Tech Stack:** GitHub Actions, Python 3.12 standard library, Bash, OpenSSH, systemd/tmux compatibility scripts, SvelteKit, Node.js 22/24, npm, FastAPI, unittest.

## Global Constraints

- Only authorized organization team members can push, but GitHub branch protection is not currently enforceable for this private repository.
- `main` is the only long-lived active branch; ordinary work uses short-lived `feature/*`, `fix/*`, and `docs/*` branches.
- Pull-request CI always runs on GitHub-hosted runners.
- A direct push to `main` must never activate GPU live deployment.
- The live gate must fail closed unless the exact `main` SHA has successful `ci/required`, merged-pull-request provenance targeting `main`, and at least one effective approval from a reviewer other than the pull-request author.
- The shared development slot deploys an exact pull-request head SHA. The later GitHub merge may create a different `main` SHA; that `main` SHA must pass fresh CI and is the only live artifact identity.
- GPU development and live releases use separate directories, locks, ports, processes, configuration, databases, health checks, and rollback pointers.
- GPU and Storage deployment and rollback remain independent.
- Storage scanner/agent rollout remains manual and tag-driven.
- Application secrets and runtime data remain server-local and outside immutable release directories.
- No workflow uses `pull_request_target`, mutable action tags, write-all permissions, or a self-hosted runner for pull-request code.
- The first live cutover is not performed until development activation and rollback have both been rehearsed successfully.
- Remote `archive/*` branches are not deleted until every exact tip is preserved by a verified annotated tag.

---

### Task 1: Reconcile the release policy and SHA contract

**Files:**
- Modify: `docs/superpowers/specs/2026-07-23-development-release-workflow-design.md`
- Modify: `docs/operations/github-cicd.md`
- Modify: `docs/development.md`
- Modify: `CONTRIBUTING.md`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Consumes: the approved workflow design plus the verified server inventory from 2026-07-23.
- Produces: one non-contradictory policy for PR-head development validation, main-SHA live validation, GitHub-hosted deployment, and delayed live cutover.

- [ ] **Step 1: Add failing repository-policy assertions**

Add tests that require the policy documents to state all of these literal contracts:

```python
self.assertIn("PR head SHA", workflow_design)
self.assertIn("main SHA", workflow_design)
self.assertIn("direct push", workflow_design.lower())
self.assertIn("GitHub-hosted", github_cicd)
self.assertIn("forced-command", github_cicd)
self.assertIn("Storage agents remain manual", github_cicd)
```

- [ ] **Step 2: Run the focused test and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_repository_layout -v
```

Expected: failure because the current documents still require exact PR/main SHA identity and still prohibit deployment credentials until branch protection exists.

- [ ] **Step 3: Correct the design contract**

Document this exact sequence:

```text
PR head SHA -> CI -> shared dev validation
GitHub merge -> resulting main SHA -> fresh ci/required
merged-PR provenance + effective approval verification
build and deploy the exact successful main SHA
```

State that same-tree or same-SHA equivalence between the PR head and resulting `main` commit is not assumed. State that the reviewed merge authorizes release, while post-merge CI validates the final release identity.

- [ ] **Step 4: Reconcile the runner and secret policy**

Replace the self-hosted-runner prerequisite with:

```text
Pull-request and deployment workflows use GitHub-hosted runners.
The deployment credential is environment-scoped and accepted by a server-side
forced-command wrapper that cannot execute arbitrary repository-provided shell.
Self-hosted production runners remain disabled while branch protection is unavailable.
```

Keep the current private-plan limitation explicit. Do not claim the compensating checks are equivalent to branch protection against a malicious authorized writer; they prevent accidental direct-push deployment inside the trusted team model.

- [ ] **Step 5: Run policy verification**

```bash
make layout-test
make deploy-readiness-test
git diff --check
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-07-23-development-release-workflow-design.md \
  docs/operations/github-cicd.md docs/development.md CONTRIBUTING.md \
  tests/test_repository_layout.py
git commit -m "docs: reconcile gpu release trust model"
```

### Task 2: Implement deterministic release authorization

**Files:**
- Create: `scripts/authorize_gpu_release.py`
- Create: `tests/test_authorize_gpu_release.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: JSON files representing a GitHub workflow-run event, associated pull requests, reviews, and check runs; live mode fetches the same resources with `gh api`.
- Produces: JSON containing `authorized`, `sha`, `pr_number`, `reason`, and `reviewer`; process exit `0` only when authorized.

- [ ] **Step 1: Write failing authorization tests**

Create these exact unittest methods:

```text
test_authorizes_successful_main_ci_for_reviewed_merged_pr
test_rejects_direct_push_without_associated_pr
test_rejects_pr_targeting_non_main_branch
test_rejects_pending_or_failed_required_check
test_rejects_author_only_approval
test_uses_latest_effective_review_per_reviewer
test_fails_closed_on_multiple_ambiguous_merged_prs
test_rejects_workflow_run_from_different_repository
```

Each method builds complete dictionaries locally, calls `authorize_release`,
and asserts both the boolean decision and the stable reason code. The first
asserts `authorized is True`; every other method asserts `authorized is False`.

Fixtures must use a final `main` SHA that may differ from the PR head SHA.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_authorize_gpu_release -v
```

Expected: import failure because `scripts.authorize_gpu_release` does not exist.

- [ ] **Step 3: Implement the pure authorization core**

Expose this exact interface:

```python
@dataclass(frozen=True)
class Authorization:
    authorized: bool
    sha: str
    pr_number: int | None
    reason: str
    reviewer: str | None

def authorize_release(
    workflow_run: dict[str, object],
    pull_requests: list[dict[str, object]],
    reviews: list[dict[str, object]],
    check_runs: list[dict[str, object]],
    *,
    repository: str,
    required_check: str = "ci/required",
) -> Authorization
```

The function must require:

```text
workflow_run.event == "push"
workflow_run.head_branch == "main"
workflow_run.conclusion == "success"
workflow_run.head_repository.full_name == repository
exactly one merged PR with base.ref == "main"
latest effective review state includes APPROVED by a non-author
ci/required for workflow_run.head_sha has conclusion == "success"
```

Any malformed or missing field returns a denied authorization without a traceback.

- [ ] **Step 4: Implement deterministic-file and live GitHub modes**

Support:

```bash
python3.12 scripts/authorize_gpu_release.py \
  --repository IRCVLab/GPU_monitor \
  --workflow-run-file event.json \
  --pulls-file pulls.json \
  --reviews-file reviews.json \
  --checks-file checks.json

python3.12 scripts/authorize_gpu_release.py \
  --repository IRCVLab/GPU_monitor \
  --workflow-run-file "$GITHUB_EVENT_PATH" \
  --live
```

Live mode uses `gh api` with read-only permissions and fails closed on command, HTTP, JSON, or schema errors.

- [ ] **Step 5: Add root verification**

Add `release-auth-test` to `make test`.

- [ ] **Step 6: Run focused and root tests**

```bash
make release-auth-test
make test
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add scripts/authorize_gpu_release.py tests/test_authorize_gpu_release.py Makefile
git commit -m "ci: verify reviewed gpu release provenance"
```

### Task 3: Harden workflow policy for deployment workflows

**Files:**
- Modify: `scripts/validate_workflows.py`
- Modify: `tests/test_workflow_policy.py`

**Interfaces:**
- Consumes: workflow YAML under `.github/workflows`.
- Produces: fail-closed violations for unsafe deployment environments, event types, runners, permissions, and missing provenance gates.

- [ ] **Step 1: Add failing policy tests**

Add fixtures proving rejection of:

```text
an environment-bearing job that evades checks by being named release-gpu
a live deployment workflow triggered directly by pull_request
a live deployment workflow without workflow_run main/push provenance conditions
a deployment job using self-hosted
a workflow using secrets in a pull-request job
```

Add fixtures proving acceptance of a SHA-pinned `workflow_run` deployment workflow whose deployment job uses `environment: gpu-live`, a GitHub-hosted runner, and an explicit authorization step.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy -v
```

Expected: at least the environment-bearing `release-gpu` evasion fixture passes incorrectly.

- [ ] **Step 3: Implement semantic deployment-job detection**

Treat a job as deployment-sensitive when any of these is true:

```python
job.job_id in {"deploy", "release", "activate", "rollback"}
direct_job_value(job, "environment") is not None
any(token in normalized_name for token in ("deploy", "release", "activate", "rollback"))
```

For `workflow_run`, accept a fail-closed compound guard that checks the completed CI workflow's event, branch, conclusion, and repository. Preserve current rejection of PR jobs on self-hosted runners.

- [ ] **Step 4: Run policy and root verification**

```bash
make policy-test
make test
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/validate_workflows.py tests/test_workflow_policy.py
git commit -m "ci: harden deployment workflow policy"
```

### Task 4: Build immutable GPU release artifacts

**Files:**
- Create: `apps/gpu-monitor/deploy/build-release.sh`
- Create: `apps/gpu-monitor/deploy/test_release_scripts.sh`
- Create: `apps/gpu-monitor/deploy/README.md`
- Modify: `Makefile`

**Interfaces:**
- Consumes: a clean repository checkout at an explicit 40-character commit SHA.
- Produces: `gpu-monitor-<sha>.tar.gz`, `gpu-monitor-<sha>.sha256`, and `release-manifest.json`.

- [ ] **Step 1: Write failing shell contract tests**

The test must prove:

```text
dirty source is rejected
invalid/non-HEAD SHA is rejected
runtime secrets, .env, databases, node_modules, .venv, caches, and build leftovers are excluded
backend source and requirements are included
fresh frontend build is included
manifest SHA equals the requested Git SHA
checksum verifies
two builds from unchanged input have identical file lists
```

- [ ] **Step 2: Run the release-script test and confirm failure**

```bash
bash apps/gpu-monitor/deploy/test_release_scripts.sh
```

Expected: failure because `build-release.sh` does not exist.

- [ ] **Step 3: Implement the builder**

Required CLI:

```bash
apps/gpu-monitor/deploy/build-release.sh \
  --sha "$(git rev-parse HEAD)" \
  --output-dir /tmp/gpu-release-output
```

Use a temporary staging directory, `npm ci`, `npm run check`, `npm run build`, and a normalized tar file order/mtime. Package only the GPU application source required at runtime plus the generated frontend build and package lock. Emit a JSON manifest with:

```json
{
  "application": "gpu-monitor",
  "git_sha": "<40 hex>",
  "artifact": "gpu-monitor-<sha>.tar.gz",
  "sha256": "<64 hex>",
  "schema": 1
}
```

- [ ] **Step 4: Add Make targets**

Add:

```make
release-script-test:
	bash apps/gpu-monitor/deploy/test_release_scripts.sh

build-gpu-release:
	apps/gpu-monitor/deploy/build-release.sh --sha "$$(git rev-parse HEAD)" --output-dir "$${OUTPUT_DIR:-apps/gpu-monitor/dist/releases}"
```

Include `release-script-test` in `make test`.

- [ ] **Step 5: Run verification**

```bash
make release-script-test
make build-gpu-release OUTPUT_DIR=/tmp/gpu-release-plan-check
sha256sum -c /tmp/gpu-release-plan-check/gpu-monitor-*.sha256
```

Expected: all commands pass.

- [ ] **Step 6: Commit**

```bash
git add apps/gpu-monitor/deploy Makefile
git commit -m "build: package immutable gpu releases"
```

### Task 5: Implement server-side forced deployment commands

**Files:**
- Create: `apps/gpu-monitor/deploy/server/gpu-monitor-deploy-command`
- Create: `apps/gpu-monitor/deploy/server/activate-release.sh`
- Create: `apps/gpu-monitor/deploy/server/health-check.sh`
- Create: `apps/gpu-monitor/deploy/server/install-deployer.sh`
- Create: `apps/gpu-monitor/deploy/server/systemd/gpu-monitor-backend@.service`
- Create: `apps/gpu-monitor/deploy/server/systemd/gpu-monitor-frontend@.service`
- Create: `apps/gpu-monitor/deploy/server/systemd/gpu-monitor-bridge@.service`
- Extend: `apps/gpu-monitor/deploy/test_release_scripts.sh`

**Interfaces:**
- Consumes: `upload <dev|live> <sha> <sha256>`, `activate <dev|live> <sha> <sha256>`, `status <dev|live>`, or `rollback <dev|live>` through `SSH_ORIGINAL_COMMAND`.
- Produces: immutable release directories and atomic `current`/`previous` pointers under `/srv/gpu-monitor/<environment>`.

- [ ] **Step 1: Add failing forced-command and activation tests**

Use a temporary prefix and fake `systemctl`, `curl`, `python`, `npm`, and `flock` commands. Prove:

```text
arbitrary commands and unsafe SHA/environment values are rejected
uploads are size-bounded and checksum-verified
activation never edits an existing release
dev and live roots never overlap
current is changed atomically and previous is retained
failed health restores the previous pointer
only gpu-monitor units for the selected environment are restarted
separate server-side flock files serialize dev and live independently
last three successful releases are retained
```

- [ ] **Step 2: Run the shell tests and confirm failure**

```bash
bash apps/gpu-monitor/deploy/test_release_scripts.sh
```

Expected: failure because the server scripts do not exist.

- [ ] **Step 3: Implement the forced-command wrapper**

Accept only:

```text
upload dev <sha> <sha256>
upload live <sha> <sha256>
activate dev <sha> <sha256>
activate live <sha> <sha256>
status dev
status live
rollback dev
rollback live
```

Parse with a closed `case` statement, validate exact hexadecimal lengths, set a minimal `PATH`, clear inherited environment variables, and never use `eval`.

- [ ] **Step 4: Implement activation and rollback**

Activation order:

```text
acquire environment-specific flock
verify incoming artifact checksum and manifest
extract into a new temporary release directory
create release-local Python virtual environment and install locked requirements
install frontend runtime dependencies from package-lock
move temporary release to releases/<sha>
atomically set previous and current pointers
restart only the selected GPU units
run backend/frontend/bridge health checks
rollback pointers and restart if any check fails
append JSON-line deployment state
retain the latest three successful releases
```

The scripts must support `PREFIX=<tempdir>` and fake command overrides for tests.

- [ ] **Step 5: Implement installer and unit templates**

The installer creates a dedicated `gpu-deploy` user, `/srv/gpu-monitor/{dev,live}`, `/etc/gpu-monitor/{dev,live}.env`, systemd templates, and an `authorized_keys` forced-command entry using:

```text
restrict,command="/usr/local/libexec/gpu-monitor-deploy-command"
```

It must support `--dry-run` and `--prefix`; real installation requires root. It must not start or replace live services.

- [ ] **Step 6: Run all release tests**

```bash
make release-script-test
make test
```

Expected: all tests pass without root or server access.

- [ ] **Step 7: Commit**

```bash
git add apps/gpu-monitor/deploy
git commit -m "feat: add isolated gpu release activation"
```

### Task 6: Add shared-development and live deployment workflows

**Files:**
- Create: `.github/workflows/deploy-gpu-dev.yml`
- Create: `.github/workflows/deploy-gpu-live.yml`
- Modify: `tests/test_repository_layout.py`
- Modify: `docs/operations/github-cicd.md`
- Modify: `docs/development.md`

**Interfaces:**
- Dev consumes a manually selected pull-request number and deploys its exact successful head SHA to `gpu-dev`.
- Live consumes a successful completed `ci` workflow for `main`, authorizes the final SHA, and deploys it to `gpu-live`.

- [ ] **Step 1: Add failing workflow contract tests**

Require:

```text
dev workflow is workflow_dispatch only
dev resolves PR head SHA through GitHub API and requires ci/required success
live workflow is workflow_run on completed ci
live runs authorize_gpu_release.py before any secret-bearing step
both workflows use GitHub-hosted runners and SHA-pinned actions
live and dev use separate environments and concurrency groups
live cancel-in-progress is false
Storage paths and services are absent from both workflows
```

- [ ] **Step 2: Run policy tests and confirm failure**

```bash
make layout-test
make policy-test
```

Expected: failure because the workflows do not exist.

- [ ] **Step 3: Implement the development workflow**

Use `workflow_dispatch` input `pr_number`. Resolve and verify an open same-repository PR, its head SHA, and successful `ci/required`. Build the release, then stream it over SSH:

```bash
ssh "$target" "upload dev $sha $digest" < "$artifact"
ssh "$target" "activate dev $sha $digest"
ssh "$target" "status dev"
```

Use `environment: gpu-dev`, `concurrency.group: gpu-dev`, and `cancel-in-progress: true`.

- [ ] **Step 4: Implement the live workflow**

Use:

```yaml
on:
  workflow_run:
    workflows: ["ci"]
    types: [completed]
```

The non-secret authorization job runs `scripts/authorize_gpu_release.py`. The deployment job depends on authorization, uses `environment: gpu-live`, `concurrency.group: gpu-live`, `cancel-in-progress: false`, checks out `workflow_run.head_sha`, builds the artifact, uploads, activates, and records status.

- [ ] **Step 5: Document secrets and operator commands**

Document exact environment secrets:

```text
GPU_DEPLOY_HOST
GPU_DEPLOY_PORT
GPU_DEPLOY_USER
GPU_DEPLOY_SSH_KEY
GPU_DEPLOY_KNOWN_HOSTS
```

Document dev dispatch, live behavior, direct-push denial, status, and rollback.

- [ ] **Step 6: Run full local verification**

```bash
make policy-test
make verify
git diff --check
```

Expected: all checks pass.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/deploy-gpu-dev.yml \
  .github/workflows/deploy-gpu-live.yml \
  tests/test_repository_layout.py docs/operations/github-cicd.md \
  docs/development.md
git commit -m "ci: add gated gpu deployment workflows"
```

### Task 7: Bootstrap and rehearse the shared development slot

**Files:**
- Modify only if rehearsal exposes defects.

**Interfaces:**
- Consumes: verified repository scripts, a newly generated deployment key, and SSH access to `166.104.167.11:2200`.
- Produces: an isolated `/srv/gpu-monitor/dev` release slot with successful activation and rollback; live remains unchanged.

- [ ] **Step 1: Capture preflight evidence**

Record:

```bash
git status --short --branch
ssh -p 2200 ircv@166.104.167.11 \
  'tmux list-sessions; ss -ltn; git -C ~/workspace/monitoring_v2 rev-parse HEAD; git -C ~/workspace/monitoring_v2_dev rev-parse HEAD'
```

Expected verified baseline:

```text
live f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce
dev  64c4b838d6e1293daf52ab0039084a2b9f84bc59
live ports 5173/8001/8000
dev ports 5174/8101
storage service remains independent
```

- [ ] **Step 2: Generate and install a dedicated key**

Generate an Ed25519 key dedicated to GitHub deployment. Run `install-deployer.sh --dry-run`, inspect output, then install the server wrapper and public key. Do not add GitHub secrets yet.

- [ ] **Step 3: Create dev server-local configuration**

Copy only the existing development environment and database into `/etc/gpu-monitor/dev.env` and `/srv/gpu-monitor/dev/shared/`. Do not read secret values into logs. Keep live configuration untouched.

- [ ] **Step 4: Build and upload the current branch to dev**

```bash
make build-gpu-release OUTPUT_DIR=/tmp/gpu-release-rehearsal
ssh "$dev_target" "upload dev $sha $digest" < "$artifact"
ssh "$dev_target" "activate dev $sha $digest"
ssh "$dev_target" "status dev"
```

- [ ] **Step 5: Verify dev health and isolation**

Verify backend, frontend, WebSocket/API proxy behavior, visible SHA metadata, and unchanged live/storage ports and process identities.

- [ ] **Step 6: Rehearse rollback**

Deploy a second harmless committed candidate or use the previous dev release, invoke `rollback dev`, and prove `current` returns to the prior SHA while live and Storage remain unchanged.

- [ ] **Step 7: Add GitHub development environment secrets**

Only after both activation and rollback pass, create the `gpu-dev` environment secrets. Dispatch the development workflow for a known pull request and verify the exact PR SHA is visible in server state.

- [ ] **Step 8: Record rehearsal evidence**

Append redacted SHA, timestamps, health results, rollback result, and remaining live-cutover blockers to `docs/operations/github-cicd.md`. Commit documentation-only changes.

### Task 8: Preserve archive refs and prepare branch cleanup

**Files:**
- Create: `scripts/preserve_archive_refs.py`
- Create: `tests/test_preserve_archive_refs.py`
- Modify: `docs/history-migration.md`
- Modify: `Makefile`

**Interfaces:**
- Consumes: the exact remote `archive/*` branch inventory.
- Produces: annotated `archive/branch/*` tags, an exact verification report, and lease-protected deletion commands; deletion requires a separate explicit `--delete-verified-branches`.

- [ ] **Step 1: Write failing deterministic tests**

Prove the tool:

```text
maps all 10 known archive branches to collision-free archive/branch/* tags
creates annotated tags rather than lightweight tags
verifies peeled remote tag targets equal recorded branch OIDs
refuses deletion if any branch moved or any tag is missing/mismatched
uses explicit force-with-lease expected OIDs for deletion
does nothing destructive without --delete-verified-branches
```

- [ ] **Step 2: Implement dry-run, tag, verify, and guarded-delete modes**

Required commands:

```bash
python3.12 scripts/preserve_archive_refs.py --remote origin --dry-run
python3.12 scripts/preserve_archive_refs.py --remote origin --create-tags
python3.12 scripts/preserve_archive_refs.py --remote origin --verify
python3.12 scripts/preserve_archive_refs.py --remote origin --delete-verified-branches
```

- [ ] **Step 3: Run tests and create/push tags**

```bash
make archive-ref-test
python3.12 scripts/preserve_archive_refs.py --remote origin --dry-run
python3.12 scripts/preserve_archive_refs.py --remote origin --create-tags
python3.12 scripts/preserve_archive_refs.py --remote origin --verify
```

Expected: all 10 annotated tags exist remotely and peel to the recorded branch tips.

- [ ] **Step 4: Record verification**

Update `docs/history-migration.md` with branch, original OID, tag, peeled OID, reachable commit count, and verification timestamp.

- [ ] **Step 5: Commit**

```bash
git add scripts/preserve_archive_refs.py tests/test_preserve_archive_refs.py \
  docs/history-migration.md Makefile
git commit -m "chore: preserve archive branches as verified tags"
```

- [ ] **Step 6: Delete only after separate operational confirmation**

Run:

```bash
python3.12 scripts/preserve_archive_refs.py \
  --remote origin \
  --delete-verified-branches
```

Then prove `git ls-remote --heads origin 'archive/*'` is empty and all `archive/branch/*` tags remain. This is the only destructive step in the plan and is intentionally separated from tag preservation.

### Task 9: Whole-branch review and live-cutover readiness verdict

**Files:**
- Modify only if review finds defects.

**Interfaces:**
- Consumes: all repository changes and development rehearsal evidence.
- Produces: a reviewed feature branch plus an explicit READY/BLOCKED live-cutover report.

- [ ] **Step 1: Run full verification**

```bash
make verify
git diff --check
git fsck --strict --full
git status --short --branch
```

- [ ] **Step 2: Obtain independent reviews**

Run a whole-branch Codex review and a Kimi K3 read-only review. Resolve every Critical/Important finding and rerun affected tests.

- [ ] **Step 3: Verify GitHub workflow behavior**

Push the feature branch, open a PR, and confirm `ci/required`. Exercise only the dev deployment workflow. Confirm a synthetic/direct-push fixture is denied by authorization tests; do not direct-push `main` to test production.

- [ ] **Step 4: Produce the live-cutover verdict**

Live is READY only when:

```text
development activation passed
development rollback passed
forced-command key cannot open an arbitrary shell
ci/required passed for the final branch
workflow policy passed
server live baseline and rollback source are recorded
GPU deployment does not touch Storage
GitHub gpu-live environment secrets are configured
```

Otherwise report BLOCKED with exact missing evidence. Do not activate live merely because repository tests pass.
