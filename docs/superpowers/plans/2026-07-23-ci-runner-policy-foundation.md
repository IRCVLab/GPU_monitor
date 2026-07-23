# CI and Runner Policy Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the preserved monitoring-platform monorepo with an always-running, path-aware GitHub CI gate while proving that pull-request code cannot target a production runner.

**Architecture:** A repository-local Python impact classifier converts a base/head diff into explicit GPU, Storage dashboard, Storage agent, shared, workflow, and documentation decisions. One GitHub Actions workflow always runs repository and policy checks, conditionally runs app-local checks, and collapses their results into a single `ci/required` status. Production deployment remains disabled until private-repository branch protection and the deploy-only runner trust boundary can be enforced.

**Tech Stack:** GitHub Actions, Python 3.12 standard library, Node.js 22, npm, SvelteKit, unittest, pytest, Bash, Make.

## Global Constraints

- GPU Monitor and Storage Monitor remain independently buildable, testable, deployable, and rollbackable.
- Pull-request code must never execute on a self-hosted or production runner.
- Every third-party GitHub Action reference is pinned to an immutable commit SHA.
- Workflow permissions default to read-only contents access.
- Documentation-only changes do not run application checks.
- Workflow, root build, shared, or deployment-control changes conservatively validate every affected application.
- Storage agent rollout remains a separate tag/manual concern and is not activated by ordinary dashboard changes.
- Production deployment remains disabled while the private repository cannot enforce protected-main and CODEOWNER review controls.
- No application behavior, UI, production secret, runtime data, or live service is modified by this plan.

---

### Task 1: Path-impact classifier

**Files:**
- Create: `scripts/ci_impact.py`
- Create: `tests/test_ci_impact.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: newline-delimited repository-relative paths from `--paths-file`, or a Git range from `--base` and `--head`.
- Produces: deterministic JSON on stdout and optional GitHub outputs named `gpu`, `storage_dashboard`, `storage_agent`, `shared`, `workflow`, `documentation`, and `apps_required`.

- [ ] **Step 1: Write failing unit tests**

Cover GPU-only, Storage dashboard-only, Storage agent-only, documentation-only, shared/root, workflow, empty diff, and rename path pairs. Assert that workflow/root control changes set both application decisions, while documentation-only changes set neither.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_ci_impact -v
```

Expected: failure because `scripts.ci_impact` does not exist.

- [ ] **Step 3: Implement the classifier**

Use only Python's standard library. Keep path rules in named tuples/constants, normalize `./`, reject absolute and parent-traversal paths, sort input paths, and emit lowercase `true`/`false` strings to `$GITHUB_OUTPUT`.

- [ ] **Step 4: Add the classifier test to root verification**

Make `make test` execute layout, history, impact, and workflow-policy tests.

- [ ] **Step 5: Run focused and root tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_ci_impact -v
make test
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add scripts/ci_impact.py tests/test_ci_impact.py Makefile
git commit -m "ci: classify affected monitoring targets"
```

### Task 2: Workflow trust-policy validator

**Files:**
- Create: `scripts/validate_workflows.py`
- Create: `tests/test_workflow_policy.py`
- Modify: `Makefile`

**Interfaces:**
- Consumes: `.github/workflows/*.yml` and `.github/workflows/*.yaml`.
- Produces: exit 0 with a concise success line, or exit 1 with file/job-specific violations.

- [ ] **Step 1: Write failing policy tests**

Fixtures must prove rejection of mutable `uses:` tags, write-all permissions, `pull_request_target`, PR jobs using `self-hosted`, deploy jobs without a main-branch guard, and non-deploy jobs using production labels. Fixtures must prove acceptance of SHA-pinned actions and GitHub-hosted PR jobs.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy -v
```

Expected: failure because the validator does not exist.

- [ ] **Step 3: Implement the validator**

Parse the constrained workflow subset without adding a dependency: scan indentation-aware job blocks and scalar/list forms needed by this repository. Report exact file, job, and rule. Require every `uses:` value to end in a 40-character lowercase hexadecimal SHA.

- [ ] **Step 4: Add `policy-test` to the Makefile**

`make policy-test` runs the policy unit tests and validates the real workflow directory.

- [ ] **Step 5: Run focused and root tests**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy -v
make test
```

Expected: all tests pass before a real workflow exists.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate_workflows.py tests/test_workflow_policy.py Makefile
git commit -m "ci: enforce workflow runner trust policy"
```

### Task 3: Always-running path-aware CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `tests/test_repository_layout.py`
- Modify: `README.md`
- Modify: `docs/development.md`

**Interfaces:**
- Consumes: pull-request and push events plus manual dispatch.
- Produces: an always-present `ci/required` job, plus conditional `ci/gpu` and `ci/storage` jobs.

- [ ] **Step 1: Extend repository contract tests**

Assert that `.github/workflows/ci.yml` exists, contains no `pull_request_target`, and that expected action SHAs and `ci/required` job naming are present.

- [ ] **Step 2: Run the contract test and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_repository_layout -v
```

Expected: failure because `.github/workflows/ci.yml` does not exist.

- [ ] **Step 3: Implement `.github/workflows/ci.yml`**

Use:

- `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683`
- `actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065`
- `actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020`

Workflow rules:

- events: `pull_request`, `push` to `main`, and `workflow_dispatch`;
- top-level `permissions: contents: read`;
- `impact` runs on `ubuntu-24.04`, fetches full history, computes event-specific base/head, and exports classifier outputs;
- `repository` always runs `make test`, `make diff-check`, and the real workflow validator;
- `gpu` runs only when `gpu == true`, performs `npm ci`, frontend check/build, installs backend requirements into a virtual environment, and runs backend tests;
- `storage` runs only when `storage_dashboard == true || storage_agent == true`, installs pytest, and runs the existing storage verification command on Linux;
- `required` uses `if: always()`, depends on every prior job, accepts only `success` or intentional `skipped` for conditional jobs, and fails otherwise;
- no job uses `self-hosted`.

- [ ] **Step 4: Document contributor behavior**

State that pushes to feature branches do not deploy, PRs receive `ci/required`, documentation-only changes skip app suites, and production deployment is intentionally disabled until repository protection is available.

- [ ] **Step 5: Run policy, contract, and full local verification**

```bash
make policy-test
make verify
```

Expected: all checks pass.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml tests/test_repository_layout.py README.md docs/development.md
git commit -m "ci: add required path-aware validation"
```

### Task 4: Deployment-readiness documentation and bootstrap guard

**Files:**
- Create: `docs/operations/github-cicd.md`
- Create: `scripts/check_deploy_prerequisites.py`
- Create: `tests/test_deploy_prerequisites.py`
- Modify: `README.md`
- Modify: `.github/CODEOWNERS`
- Modify: `Makefile`

**Interfaces:**
- Consumes: repository metadata JSON supplied through a file for tests or fetched through `gh api` in operator mode.
- Produces: an explicit READY/BLOCKED report for protected-main, CODEOWNER enforcement, runner availability, and server reachability; never mutates GitHub or the server.

- [ ] **Step 1: Write failing prerequisite tests**

Cover: private repository without branch protection is BLOCKED; protected main with required `ci/required` and code-owner review is READY for runner registration; missing server reachability blocks cutover but not CI publication; missing org runner-group permission is reported as unknown, not silently accepted.

- [ ] **Step 2: Run focused tests and confirm failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_deploy_prerequisites -v
```

Expected: failure because the checker does not exist.

- [ ] **Step 3: Implement the read-only checker**

Support `--metadata-file` for deterministic tests and `--repo IRCVLab/GPU_monitor` for live inspection through `gh api`. Do not request new credentials, alter settings, register a runner, or contact production unless `--check-host` is explicitly supplied.

- [ ] **Step 4: Write the operator runbook**

Document:

- current GitHub private-plan branch-protection blocker;
- exact `main` protection settings required before runner registration;
- why the production runner is not installed yet;
- initial publication commands;
- action/secret policy;
- immutable artifact and atomic release expectations for Phase 4;
- independent GPU and Storage dashboard concurrency/rollback;
- Storage agent rollout remains manual/tagged;
- server SSH timeout as a cutover blocker, not a source-publication blocker.

- [ ] **Step 5: Extend CODEOWNERS and verification**

Ensure CI, deployment controls, prerequisite checker, and operation docs are operator-owned. Add `deploy-readiness-test` to `make test`.

- [ ] **Step 6: Run focused and full verification**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_deploy_prerequisites -v
make verify
```

Expected: all local checks pass; live prerequisite mode reports BLOCKED with the branch-protection and SSH evidence.

- [ ] **Step 7: Commit**

```bash
git add docs/operations/github-cicd.md scripts/check_deploy_prerequisites.py tests/test_deploy_prerequisites.py README.md .github/CODEOWNERS Makefile
git commit -m "docs: define guarded deployment bootstrap"
```

### Task 5: Review, publish preserved history, and verify GitHub CI

**Files:**
- Modify only if review finds defects.

**Interfaces:**
- Consumes: clean reviewed feature branch and empty `IRCVLab/GPU_monitor`.
- Produces: published `main`, selected archive branches and checkpoint tags, and a successful GitHub Actions `ci/required` run.

- [ ] **Step 1: Run final local verification**

```bash
make verify
git diff --check
git fsck --strict --full
git status --short --branch
```

Expected: verification succeeds and the worktree is clean.

- [ ] **Step 2: Obtain independent reviews**

Run whole-branch Codex code review and a read-only Kimi K3 review. Kimi timeout/quota is recorded as an external validation gap but does not invalidate fresh local and Codex evidence.

- [ ] **Step 3: Fast-forward local `main`**

After clean review:

```bash
git switch main
git merge --ff-only feature/ci-cd-foundation
```

- [ ] **Step 4: Reconfirm the target is empty**

```bash
test -z "$(git ls-remote https://github.com/IRCVLab/GPU_monitor.git)"
```

Expected: exit 0.

- [ ] **Step 5: Atomically publish selected refs**

Push `main`, all `archive/gpu-dev/*`, `archive/gpu-live/*`, `archive/storage/*` source branches, and all `pre-monorepo-*` plus `archive/source-tag/*` tags. Do not publish `refs/heads/import/storage`.

- [ ] **Step 6: Configure the remote and verify refs**

```bash
git remote add origin https://github.com/IRCVLab/GPU_monitor.git
git branch --set-upstream-to=origin/main main
git ls-remote origin | sort
git ls-remote origin 'refs/heads/import/*'
```

Expected: selected refs exist and no import helper branch is present.

- [ ] **Step 7: Verify GitHub Actions**

Use `gh run list`, `gh run watch`, and `gh run view --log-failed` to prove the initial `ci/required` run passes. Fix any CI-only defects on the feature branch, rerun full local verification, fast-forward main, and push again.

- [ ] **Step 8: Run live deployment prerequisite check**

Expected current result: BLOCKED until protected-main controls are available and `166.104.167.11:2200` is reachable. Do not register a production runner or alter live services while blocked.

