# Local Development and Live Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the permanent GPU development deployment and automatically deploy the newest exact successful same-repository `main` push to Live without requiring a pull request or review.

**Architecture:** Contributors develop locally and GitHub CI remains the validation boundary. The Live workflow consumes only a completed successful `ci` workflow-run for `main`, independently verifies the exact `ci/required` check SHA, builds that SHA, and sends the immutable artifact through the existing forced-command SSH protocol. The obsolete Dev workflow and runtime are retired without deleting Dev release files or accounts during the first pass.

**Tech Stack:** GitHub Actions, Python 3.12, `unittest`, custom workflow-policy parser, Bash deployment scripts, systemd, OpenSSH forced commands, SvelteKit, FastAPI

## Global Constraints

- Pull requests and reviews are optional and must not be Live deployment prerequisites.
- Only a completed successful `ci` workflow run caused by a same-repository `main` push may authorize deployment.
- A delayed or rerun workflow whose SHA is no longer current `main` must not deploy.
- The workflow SHA, required-check SHA, checkout SHA, artifact SHA, upload SHA, and activation SHA must be identical.
- Keep the immutable release, bounded upload, strict host verification, health check, environment isolation, and rollback contracts unchanged.
- Remove the permanent `gpu-dev` workflow and supported operating procedure.
- Do not modify Live ports `5173`, `8001`, or `8000`.
- Do not modify Storage Monitor, port `8088`, its service, data, or deployment process.
- Stop and disable Dev services before deleting nothing; preserve `/srv/gpu-monitor/dev`, the Dev account, and rollback state.
- Add no dependencies.
- Use TDD: observe focused test failure before changing each implementation boundary.

---

## File Map

- `scripts/authorize_gpu_release.py`: validates successful same-repository `main` CI and the exact required check.
- `tests/test_authorize_gpu_release.py`: unit and GitHub API pagination contract for authorization.
- `.github/workflows/deploy-gpu-live.yml`: builds and deploys the authorized workflow SHA.
- `.github/workflows/deploy-gpu-dev.yml`: obsolete workflow to delete.
- `scripts/validate_workflows.py`: static GitHub Actions trust policy; rejects retired Dev deployments and validates Live.
- `tests/test_workflow_policy.py`: adversarial policy-validator coverage.
- `tests/test_repository_layout.py`: repository-level workflow and documentation contracts.
- `CONTRIBUTING.md`: contributor workflow.
- `README.md`: repository-level CI and deployment summary.
- `docs/development.md`: local development and verification commands.
- `docs/operations/github-cicd.md`: current GitHub and Live operating procedure.
- `docs/history-migration.md`: historical evidence; retain rehearsal facts and mark the lane retired.
- `docs/superpowers/specs/2026-07-23-development-release-workflow-design.md`: historical design; add a superseded notice rather than rewriting history.
- `apps/gpu-monitor/deploy/README.md`: current server installer and forced-command operating contract.
- `apps/gpu-monitor/deploy/server/install-deployer.sh`: reconciles Live-only authorization and revokes Dev SSH authorization.
- `apps/gpu-monitor/deploy/test_release_scripts.sh`: installer retirement regression coverage.

---

### Task 1: Replace PR Review Authorization with Exact Main-CI Authorization

**Files:**
- Modify: `tests/test_authorize_gpu_release.py`
- Modify: `scripts/authorize_gpu_release.py`

**Interfaces:**
- Consumes: GitHub `workflow_run` payload and paginated `/repos/{repository}/commits/{sha}/check-runs`.
- Produces: `authorize_release(workflow_run, check_runs, *, current_main_sha, repository, required_check="ci/required") -> Authorization`.
- Produces: JSON containing `authorized`, `sha`, and `reason`; exit `0` only when authorized.

- [ ] **Step 1: Replace PR-centric fixtures with the direct-main authorization contract**

Use a workflow fixture that includes the exact workflow identity:

```python
def workflow_run(self, **overrides):
    data = {
        "name": "ci",
        "event": "push",
        "head_branch": "main",
        "conclusion": "success",
        "status": "completed",
        "head_sha": FINAL_SHA,
        "head_repository": {"full_name": REPOSITORY},
    }
    data.update(overrides)
    return data

def authorize(self, workflow_run=None, check_runs=None):
    return authorize_release(
        workflow_run or self.workflow_run(),
        check_runs if check_runs is not None else [self.required_check()],
        current_main_sha=FINAL_SHA,
        repository=REPOSITORY,
    )
```

Add these acceptance and rejection tests:

```python
def test_authorizes_successful_same_repository_main_push(self):
    authorization = self.authorize()
    self.assertIs(authorization.authorized, True)
    self.assertEqual(authorization.sha, FINAL_SHA)
    self.assertEqual(authorization.reason, "authorized")

def test_rejects_untrusted_workflow_provenance(self):
    cases = (
        ({"name": "other"}, "workflow_name_mismatch"),
        ({"event": "pull_request"}, "workflow_event_not_push"),
        ({"head_branch": "feature"}, "workflow_branch_not_main"),
        ({"status": "in_progress"}, "workflow_status_not_completed"),
        ({"conclusion": "failure"}, "workflow_conclusion_not_success"),
        ({"head_repository": {"full_name": "IRCVLab/fork"}}, "workflow_repository_mismatch"),
    )
    for overrides, reason in cases:
        with self.subTest(overrides=overrides):
            result = self.authorize(self.workflow_run(**overrides))
            self.assertIs(result.authorized, False)
            self.assertEqual(result.reason, reason)

def test_rejects_workflow_sha_that_is_no_longer_current_main(self):
    result = authorize_release(
        self.workflow_run(),
        [self.required_check()],
        current_main_sha="e" * 40,
        repository=REPOSITORY,
    )
    self.assertIs(result.authorized, False)
    self.assertEqual(result.reason, "workflow_sha_not_current_main")
```

Keep the existing duplicate-check ordering and malformed timestamp tests, adapted to the two-argument `authorize_release` signature. Replace PR/review pagination tests with:

```python
def test_live_fetch_reads_paginated_checks_and_current_main(self):
    calls = []
    def fake_run(argv, **_kwargs):
        calls.append(argv)
        path = argv[-1]
        if path.endswith(f"/commits/{FINAL_SHA}/check-runs"):
            payload = [{"check_runs": [self.required_check()]}]
        elif path.endswith("/git/ref/heads/main"):
            payload = {"object": {"sha": FINAL_SHA}}
        else:
            self.fail(f"unexpected gh path {path}")
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
    with patch("scripts.authorize_gpu_release.subprocess.run", fake_run):
        checks, current_main_sha = fetch_live_evidence(REPOSITORY, self.workflow_run())
    self.assertEqual(checks, [self.required_check()])
    self.assertEqual(current_main_sha, FINAL_SHA)
    self.assertEqual(len(calls), 2)
```

- [ ] **Step 2: Run the authorization tests and observe the expected interface failure**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_authorize_gpu_release -v
```

Expected: FAIL because `authorize_release` still requires pull requests and reviews and `fetch_live_evidence` does not exist.

- [ ] **Step 3: Remove all PR and review parsing from the authorization implementation**

Reduce the result type and authorization signature:

```python
@dataclass(frozen=True)
class Authorization:
    authorized: bool
    sha: str
    reason: str

def denied(reason: str, sha: str = "") -> Authorization:
    return Authorization(False, sha, reason)

def authorize_release(
    workflow_run: dict[str, object],
    check_runs: list[dict[str, object]],
    *,
    current_main_sha: str,
    repository: str,
    required_check: str = "ci/required",
) -> Authorization:
```

Validate in this fail-closed order:

```python
if string_field(workflow_run, "name") != "ci":
    return denied("workflow_name_mismatch", sha)
if string_field(workflow_run, "event") != "push":
    return denied("workflow_event_not_push", sha)
if string_field(workflow_run, "head_branch") != "main":
    return denied("workflow_branch_not_main", sha)
if string_field(workflow_run, "status") != "completed":
    return denied("workflow_status_not_completed", sha)
if string_field(workflow_run, "conclusion") != "success":
    return denied("workflow_conclusion_not_success", sha)
if string_field(workflow_run, "head_repository", "full_name") != repository:
    return denied("workflow_repository_mismatch", sha)
validate_sha(current_main_sha)
if sha != current_main_sha:
    return denied("workflow_sha_not_current_main", sha)
if not required_check_is_successful(check_runs, sha=sha, required_check=required_check):
    return denied("required_check_not_successful", sha)
return Authorization(True, sha, "authorized")
```

Delete timestamp/review helpers used only for pull requests and reviews. Retain RFC3339 parsing used to order duplicate check runs.

Replace `fetch_live_inputs` with a two-call evidence fetch. Add a non-paginated
`gh_api_object(path)` helper with the same fail-closed subprocess behavior as
`gh_api_paginated`, then implement:

```python
def fetch_live_evidence(
    repository: str,
    workflow_run: dict[str, object],
) -> tuple[list[dict[str, object]], str]:
    validate_repository(repository)
    sha = string_field(workflow_run, "head_sha") if isinstance(workflow_run, dict) else None
    if sha is None:
        raise ValueError("workflow run is missing head_sha")
    validate_sha(sha)
    checks = flatten_object_pages(
        gh_api_paginated(f"/repos/{repository}/commits/{sha}/check-runs"),
        "check_runs",
    )
    main_ref = gh_api_object(f"/repos/{repository}/git/ref/heads/main")
    current_main_sha = string_field(main_ref, "object", "sha")
    if current_main_sha is None:
        raise ValueError("main ref is missing object.sha")
    validate_sha(current_main_sha)
    return checks, current_main_sha
```

Keep `--live` as the workflow-facing switch, but make it fetch checks plus the
current `main` ref. Without `--live`, require `--checks-file` and
`--current-main-sha`; remove `--pulls-file` and `--reviews-file`.

- [ ] **Step 4: Run focused authorization tests**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_authorize_gpu_release -v
```

Expected: all authorization tests PASS and no GitHub pulls/reviews API path is requested.

- [ ] **Step 5: Commit the authorization boundary**

```bash
git add scripts/authorize_gpu_release.py tests/test_authorize_gpu_release.py
git commit -m "ci: authorize successful main pushes"
```

---

### Task 2: Retire the GPU Development Workflow and Tighten Live Policy

**Files:**
- Delete: `.github/workflows/deploy-gpu-dev.yml`
- Modify: `.github/workflows/deploy-gpu-live.yml`
- Modify: `tests/test_repository_layout.py`
- Modify: `tests/test_workflow_policy.py`
- Modify: `scripts/validate_workflows.py`

**Interfaces:**
- Consumes: repository workflow files.
- Produces: one GPU deployment lane, `gpu-live`.
- Produces: validator violation `retired-gpu-dev-deployment` for any deployment workflow that references the retired lane.

- [ ] **Step 1: Change repository contracts to require only Live**

Replace the two-lane repository test with assertions equivalent to:

```python
def test_gpu_deployment_workflow_is_live_only(self):
    self.assertFalse(Path(".github/workflows/deploy-gpu-dev.yml").exists())
    live = workflow_text(".github/workflows/deploy-gpu-live.yml")
    self.assertIn("workflow_run:", live)
    self.assertIn('workflows: ["ci"]', live)
    self.assertIn("types: [completed]", live)
    self.assertIn("environment: gpu-live", live)
    self.assertIn("group: gpu-live", live)
    self.assertIn("cancel-in-progress: false", live)
    self.assertIn("github.event.workflow_run.head_sha", live)
    self.assertNotIn("pull-requests: read", live)
    self.assertNotIn("gpu-dev", live)
    self.assertIn("/git/ref/heads/main", live)
    self.assertIn('[[ "$current_main_sha" == "$sha" ]]', live)
```

- [ ] **Step 2: Add a policy test that rejects reintroducing Dev deployment**

```python
def deployment_workflow_with(retired):
    top_level = "  group: gpu-dev" if retired == "group: gpu-dev" else "  group: gpu-live"
    job_line = "    environment: gpu-live" if retired == "group: gpu-dev" else f"    {retired}"
    return f"""
name: retired-dev
on: workflow_run
concurrency:
{top_level}
jobs:
  deploy:
    runs-on: ubuntu-24.04
{job_line}
    steps:
      - run: echo deploy
"""

def test_rejects_retired_gpu_dev_deployment(self):
    for retired in (
        "group: gpu-dev",
        "environment: gpu-dev",
        'run: echo "upload dev $sha $digest"',
        'run: echo "activate dev $sha $digest"',
        'run: echo "status dev"',
        'run: echo "rollback dev"',
    ):
        with self.subTest(retired=retired):
            body = deployment_workflow_with(retired)
            self.assert_policy_violation(
                body,
                "retired-gpu-dev-deployment",
                "deploy",
                "retired-dev.yml",
            )
```

Retain all generic tests for pinned actions, secret isolation, GitHub-hosted runners, exact live workflow-run guards, and canonical forced SSH commands. Delete tests whose only purpose is permitting or validating the old `workflow_dispatch` Dev deployment.

- [ ] **Step 3: Run the focused contracts and observe failure**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest \
  tests.test_repository_layout \
  tests.test_workflow_policy -v
```

Expected: FAIL because the Dev workflow still exists and the validator still permits guarded Dev deployment.

- [ ] **Step 4: Delete the Dev workflow and PR permission**

Delete `.github/workflows/deploy-gpu-dev.yml`.

In `.github/workflows/deploy-gpu-live.yml`, change:

```yaml
permissions:
  contents: read
  checks: read
```

Keep both exact job-level `if` guards, exact checkout/build/deploy SHA
references, `gpu-live` environment, non-cancelling concurrency, pinned actions,
and forced SSH protocol. The only deployment-command addition is the explicit
current-main check described below.

In the deploy step, add the built-in token and re-check current `main` after the
artifact upload but immediately before activation:

```yaml
          GH_TOKEN: ${{ github.token }}
```

```bash
          ssh "${ssh_opts[@]}" "$target" "upload live $sha $digest" < "$artifact"
          current_main_sha=$(gh api \
            -H "Accept: application/vnd.github+json" \
            "/repos/$GITHUB_REPOSITORY/git/ref/heads/main" \
            --jq .object.sha)
          [[ "$current_main_sha" =~ ^[0-9a-f]{40}$ ]] || { echo "invalid current main SHA" >&2; exit 1; }
          [[ "$current_main_sha" == "$sha" ]] || { echo "workflow SHA is no longer current main" >&2; exit 1; }
          ssh "${ssh_opts[@]}" "$target" "activate live $sha $digest"
          ssh "${ssh_opts[@]}" "$target" "status live"
```

This permits a stale job to upload an unused content-addressed artifact but
prevents it from activating over a newer Live release.

- [ ] **Step 5: Replace Dev allowlisting with an explicit retired-lane violation**

Remove `has_gpu_dev_dispatch_guard` and helpers used only by that guard. Add:

```python
def workflow_mentions_retired_gpu_dev(lines: list[SourceLine]) -> bool:
    retired = ("gpu-dev", "upload dev ", "activate dev ", "status dev", "rollback dev")
    return any(token in line.text for line in lines for token in retired)
```

Scan the complete workflow, including top-level concurrency, whenever it has a
deployment job:

```python
if deploy_job and workflow_mentions_retired_gpu_dev(lines):
    violations.append(
        Violation(
            path,
            "retired-gpu-dev-deployment",
            "permanent GPU development deployment is retired; develop locally and deploy only gpu-live",
            job.job_id,
        )
    )
```

Do not weaken `has_split_live_authorization`, workflow-run provenance guards, pinned-action checks, runner restrictions, secret restrictions, or canonical deployment command matching.

Update `canonical_deploy_lines("live")` and `has_forced_deploy_step` to require
the exact `GH_TOKEN` binding and the current-main API/check lines above. Add a
mutation test that removes each current-main line in turn and expects the
existing canonical-deploy violation.

- [ ] **Step 6: Run focused policy and workflow validation**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest \
  tests.test_repository_layout \
  tests.test_workflow_policy -v
python3.12 scripts/validate_workflows.py .github/workflows
```

Expected: all tests PASS and the validator reports exactly the remaining workflow files without a Dev deployment violation.

- [ ] **Step 7: Commit the live-only workflow**

```bash
git add .github/workflows scripts/validate_workflows.py \
  tests/test_repository_layout.py tests/test_workflow_policy.py
git commit -m "ci: retire shared gpu development deployment"
```

---

### Task 3: Make Local Development the Current Operator Contract

**Files:**
- Modify: `CONTRIBUTING.md`
- Modify: `README.md`
- Modify: `docs/development.md`
- Modify: `docs/operations/github-cicd.md`
- Modify: `docs/history-migration.md`
- Modify: `docs/superpowers/specs/2026-07-23-development-release-workflow-design.md`
- Modify: `tests/test_repository_layout.py`

**Interfaces:**
- Consumes: approved design `docs/superpowers/specs/2026-07-25-local-development-live-release-design.md`.
- Produces: one unambiguous current workflow and clearly labeled historical evidence.

- [ ] **Step 1: Add failing documentation-contract assertions before editing prose**

In `tests/test_repository_layout.py`, require current documentation to include these exact concepts:

```python
self.assertIn("Pull requests are optional", combined)
self.assertIn("successful same-repository `main` push", combined)
self.assertIn("local development", combined.lower())
self.assertNotIn("run `deploy-gpu-dev`", combined)
self.assertNotIn("`pr_number`", combined)
```

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest \
  tests.test_repository_layout.RepositoryLayoutTest.test_gpu_deployment_documentation_covers_operator_contracts -v
```

Expected: FAIL until current docs describe the approved policy.

- [ ] **Step 2: Update contributor and development documentation**

Document in both `CONTRIBUTING.md` and `README.md`:

```text
local development -> optional PR or direct main push -> main CI -> exact successful SHA live deployment
```

Remove the manual `deploy-gpu-dev` instructions. Preserve application-local setup, test, debug-scenario, and build commands. State that failed `main` CI leaves the current Live release unchanged even though the failed commit remains in Git history.

- [ ] **Step 3: Rewrite the current GitHub operations section**

Document only `gpu-live` secrets and remove current `gpu-dev` environment instructions. Replace merged-PR/reviewer authorization with:

```text
workflow name ci
event push
branch main
status completed
conclusion success
head repository IRCVLab/GPU_monitor
latest ci/required check successful for the exact head SHA
```

Keep strict SSH host verification, environment-scoped secrets, forced-command grammar, status, rollback, and live health checks.

- [ ] **Step 4: Preserve history without presenting it as current procedure**

Add a retirement note before Dev rehearsal records:

```markdown
> Historical evidence: the shared GPU development lane was retired by
> `docs/superpowers/specs/2026-07-25-local-development-live-release-design.md`.
> The following records describe the completed rehearsal and are not current
> operating instructions.
```

Mark the 2026-07-23 workflow design as superseded for current release policy by the 2026-07-25 design. Do not delete historical SHA, health, or rollback evidence.

- [ ] **Step 5: Run documentation and layout contracts**

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_repository_layout -v
git diff --check
```

Expected: PASS with no current operator instruction requiring `gpu-dev`, PR provenance, or review approval.

- [ ] **Step 6: Commit documentation**

```bash
git add CONTRIBUTING.md README.md docs tests/test_repository_layout.py
git commit -m "docs: make local development the default"
```

---

### Task 4: Add Explicit Live-Only Installer Reconciliation

**Files:**
- Modify: `apps/gpu-monitor/deploy/README.md`
- Modify: `apps/gpu-monitor/deploy/server/install-deployer.sh`
- Modify: `apps/gpu-monitor/deploy/test_release_scripts.sh`

**Interfaces:**
- Consumes: a distinct Ed25519 Live public key.
- Produces: `install-deployer.sh --retire-dev --live-public-key KEY`.
- Preserves: Dev account, Dev release files, Dev state, Live account, and Live release state.
- Revokes: `/home/gpu-deploy-dev/.ssh/authorized_keys`.

- [ ] **Step 1: Add a failing installer retirement test**

Extend the installer isolation test with:

```bash
"$INSTALLER_SCRIPT" --dry-run --prefix "$prefix" \
  --retire-dev --live-public-key "$livekey" > "$tmp/retire-dev.out"
[[ ! -e "$dev_auth" ]] || fail "live-only reconciliation preserved dev SSH authorization"
grep -Fq "$livekey_blob" "$live_auth" ||
  fail "live-only reconciliation did not preserve the requested live key"
[[ -d "$prefix/srv/gpu-monitor/dev/releases" ]] ||
  fail "live-only reconciliation deleted preserved dev releases"
[[ -f "$prefix/etc/gpu-monitor/dev.env" ]] ||
  fail "live-only reconciliation deleted preserved dev configuration"
```

Add rejection tests for:

```bash
--retire-dev without --live-public-key
--retire-dev together with --dev-public-key
--live-public-key alone without --retire-dev or --dev-public-key
```

The last rejection preserves the existing install/rotation contract and makes
Dev revocation an explicit operator action rather than an accidental omission.

- [ ] **Step 2: Run the installer test and observe failure**

```bash
bash apps/gpu-monitor/deploy/test_release_scripts.sh
```

Expected: FAIL because `--retire-dev` is not recognized.

- [ ] **Step 3: Implement the explicit retirement mode**

Update usage:

```bash
printf 'Usage: %s [--dry-run] [--prefix ABSOLUTE] [--retire-dev] [--dev-public-key KEY] [--live-public-key KEY] [--node-prefix ABSOLUTE]\n' "$0" >&2
```

Parse `--retire-dev` into `retire_dev=false|true`. Enforce:

```bash
if [[ "$retire_dev" == true ]]; then
  [[ -z "$dev_key" ]] || fail "--retire-dev cannot be combined with --dev-public-key"
  [[ -n "$live_key" ]] || fail "--retire-dev requires --live-public-key"
else
  [[ -n "$dev_key" ]] || fail "--dev-public-key is required unless --retire-dev is used"
fi
```

After the existing atomic `write_authorized_key` helper:

```bash
if [[ "$retire_dev" == true ]]; then
  rm -f "$prefix/home/gpu-deploy-dev/.ssh/authorized_keys"
else
  write_authorized_key dev "$dev_key"
fi
[[ -z "$live_key" ]] || write_authorized_key live "$live_key"
```

Keep key normalization, distinct-key checks, root gates, managed Node runtime,
identity validation, sudoers validation, and no-service-start contract.

- [ ] **Step 4: Run release-script regression coverage**

Before running the suite, update `apps/gpu-monitor/deploy/README.md` so its
current installation command uses:

```bash
sudo apps/gpu-monitor/deploy/server/install-deployer.sh \
  --retire-dev \
  --live-public-key "$(cat /path/to/live_deploy.pub)"
```

Move Dev protocol examples into a clearly labeled historical compatibility
note; do not present `upload dev`, `activate dev`, `status dev`, or
`rollback dev` as current operator commands.

```bash
bash apps/gpu-monitor/deploy/test_release_scripts.sh
```

Expected: all release-script tests PASS, including explicit Dev authorization
revocation with preserved files.

- [ ] **Step 5: Commit installer reconciliation**

```bash
git add apps/gpu-monitor/deploy/README.md \
  apps/gpu-monitor/deploy/server/install-deployer.sh \
  apps/gpu-monitor/deploy/test_release_scripts.sh
git commit -m "deploy: support live-only authorization"
```

---

### Task 5: Verify and Review the Repository Change

**Files:**
- Modify only if verification identifies a defect.

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: clean, independently reviewed branch.

- [ ] **Step 1: Run focused tests**

```bash
make release-auth-test
make policy-test
make layout-test
python3.12 scripts/validate_workflows.py .github/workflows
```

Expected: PASS.

- [ ] **Step 2: Run full repository verification**

```bash
make verify
git diff --check
git fsck --strict --full
git status --short --branch
```

Expected: `make verify` and `git diff --check` exit `0`; `git status` is clean. Existing unreachable-object notices from `git fsck` are informational only if its exit status is `0`.

- [ ] **Step 3: Perform independent security review**

Review these claims:

- A PR, review, or GitHub pull API is not required.
- A pull-request workflow cannot receive deployment secrets.
- A failed, incomplete, non-main, differently named, or cross-repository workflow cannot deploy.
- A successful check for a different SHA cannot deploy.
- A delayed or rerun workflow whose SHA is no longer current `main` cannot activate.
- Dev workflow or Dev environment deployment cannot be reintroduced without policy failure.
- Live-only installer reconciliation removes Dev SSH authorization without deleting Dev state.
- Live and Storage deployment boundaries are unchanged.

- [ ] **Step 4: Fix findings with focused regression tests and rerun full verification**

For every accepted finding, first add a failing test, apply the minimal fix, rerun its focused test, then rerun `make verify`.

- [ ] **Step 5: Commit review fixes**

```bash
git add -A
git commit -m "fix: close live-only release review gaps"
```

Skip this commit if review produces no changes.

---

### Task 6: Retire the Running Development Slot Reversibly

**Files:**
- No repository files.
- Runtime resources: local SSH forwarding process and remote systemd Dev units.

**Interfaces:**
- Consumes: verified repository change.
- Produces: no local `15174` tunnel and no listeners on remote `5174` or `8101`.

- [ ] **Step 1: Capture a read-only baseline**

```bash
ssh -o BatchMode=yes -p 2200 ircv@166.104.167.11 '
  systemctl show -p Id -p ActiveState -p SubState -p NRestarts \
    gpu-monitor-backend@dev.service \
    gpu-monitor-frontend@dev.service \
    storage-viz-dashboard.service
  curl -fsS http://127.0.0.1:5173/ >/dev/null
  curl -fsS http://127.0.0.1:8001/health
  curl -fsS http://127.0.0.1:8088/ >/dev/null
'
```

Record the Live and Storage status before stopping Dev.

- [ ] **Step 2: Stop the local Dev tunnel loop**

Identify the parent loop containing:

```text
-L 127.0.0.1:15174:127.0.0.1:5174
```

Send `TERM` to the parent loop and its current SSH child. Verify:

```bash
lsof -nP -iTCP:15174 -sTCP:LISTEN
```

Expected: no output. Keep the `15173 -> 5173` Live and `8088 -> 8088` Storage tunnels.

- [ ] **Step 3: Stop and disable only Dev systemd units**

Connect with the existing server administrator account:

```bash
ssh -tt -p 2200 shchoi@166.104.167.11
```

Then run:

```bash
sudo systemctl disable --now \
  gpu-monitor-backend@dev.service \
  gpu-monitor-frontend@dev.service
```

Do not delete units, accounts, `/srv/gpu-monitor/dev`, `/var/lib/gpu-monitor/dev`, or `/etc/gpu-monitor/dev.env`.

- [ ] **Step 4: Verify retirement and isolation**

```bash
systemctl is-enabled gpu-monitor-backend@dev.service gpu-monitor-frontend@dev.service
systemctl is-active gpu-monitor-backend@dev.service gpu-monitor-frontend@dev.service
ss -ltn | grep -E ':(5174|8101)\b' && exit 1 || true
curl -fsS http://127.0.0.1:5173/ >/dev/null
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:8088/ >/dev/null
```

Expected: Dev units disabled/inactive, no Dev listeners, Live and Storage healthy.

- [ ] **Step 5: Record runtime retirement evidence**

Append a dated retirement record to `docs/operations/github-cicd.md` only if the server stop and isolation verification completed. If administrator credentials are unavailable, report the exact blocked command and do not falsely document the service as retired.

- [ ] **Step 6: Verify and commit runtime evidence**

If Step 5 changed documentation:

```bash
PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_repository_layout -v
git diff --check
git add docs/operations/github-cicd.md
git commit -m "docs: record gpu development retirement"
```

Expected: layout tests PASS and the branch is clean before publication.

---

### Task 7: Publish and Activate the Live-Only GitHub Workflow

**Files:**
- GitHub repository configuration and server authorization state.

**Interfaces:**
- Consumes: clean verified branch, installed `gpu-deploy-live` forced-command key, and five `gpu-live` secrets.
- Produces: automatic deployment of every successful same-repository `main` push.

- [ ] **Step 1: Audit the public repository history for credentials**

Scan all refs before publishing additional history. At minimum reject tracked private keys, `.env` files other than examples, passwords, tokens, and cloud credentials. Rotate any confirmed credential before proceeding.

- [ ] **Step 2: Generate the dedicated Live key and verify the host key**

```bash
umask 077
test -e .omx/keys/gpu-monitor-github-live ||
  ssh-keygen -t ed25519 -a 64 -N '' \
    -C gpu-monitor-github-live \
    -f .omx/keys/gpu-monitor-github-live
ssh-keyscan -p 2200 -t ed25519 166.104.167.11 \
  > /tmp/gpu-monitor-known-hosts
ssh-keygen -lf /tmp/gpu-monitor-known-hosts
```

Expected host fingerprint:

```text
SHA256:R35VBvJptW4nmu7xA3nan+epgxm77n48AgAFNmUepDU
```

Abort if the scanned fingerprint differs. Verify the Dev and Live public-key
fingerprints are distinct.

- [ ] **Step 3: Remove any stale Dev environment and create only Live**

Inspect the environment list:

```bash
gh api repos/IRCVLab/GPU_monitor/environments \
  --jq '.environments[].name'
```

If and only if `gpu-dev` is listed, delete it and its environment-scoped
secrets:

```bash
gh api --method DELETE repos/IRCVLab/GPU_monitor/environments/gpu-dev
```

Create Live:

```bash
gh api --method PUT repos/IRCVLab/GPU_monitor/environments/gpu-live
```

Verify the final list contains `gpu-live` and not `gpu-dev`.

- [ ] **Step 4: Install the distinct Live forced-command public key**

Package only the verified public installer assets and copy them with the public
key to the administrator account:

```bash
rm -rf /tmp/gpu-monitor-live-installer
mkdir -p /tmp/gpu-monitor-live-installer
cp -R apps/gpu-monitor/deploy/server /tmp/gpu-monitor-live-installer/
cp .omx/keys/gpu-monitor-github-live.pub /tmp/gpu-monitor-live-installer/
tar -C /tmp -czf /tmp/gpu-monitor-live-installer.tar.gz \
  gpu-monitor-live-installer
scp -P 2200 /tmp/gpu-monitor-live-installer.tar.gz \
  shchoi@166.104.167.11:/tmp/
```

On the server, extract the bundle in `/tmp` and use the verified installer in
explicit Live-only reconciliation mode:

```bash
cd /tmp
tar -xzf gpu-monitor-live-installer.tar.gz
sudo /tmp/gpu-monitor-live-installer/server/install-deployer.sh \
  --retire-dev \
  --live-public-key "$(cat /tmp/gpu-monitor-live-installer/gpu-monitor-github-live.pub)"
```

Verify `/home/gpu-deploy-dev/.ssh/authorized_keys` is absent, the Live key
fingerprint is installed, and `/srv/gpu-monitor/dev` still exists. Never copy a
private key to the server. Remove the server and local `/tmp` installer bundles
after verification.

- [ ] **Step 5: Register the five `gpu-live` environment secrets**

Run from the repository worktree:

```bash
REPO=IRCVLab/GPU_monitor
ENV=gpu-live

printf '%s' '166.104.167.11' |
  gh secret set GPU_DEPLOY_HOST -R "$REPO" -e "$ENV"
printf '%s' '2200' |
  gh secret set GPU_DEPLOY_PORT -R "$REPO" -e "$ENV"
printf '%s' 'gpu-deploy-live' |
  gh secret set GPU_DEPLOY_USER -R "$REPO" -e "$ENV"
gh secret set GPU_DEPLOY_SSH_KEY -R "$REPO" -e "$ENV" \
  < .omx/keys/gpu-monitor-github-live
gh secret set GPU_DEPLOY_KNOWN_HOSTS -R "$REPO" -e "$ENV" \
  < /tmp/gpu-monitor-known-hosts
```

Verify only secret names and update timestamps with:

```bash
gh secret list -R IRCVLab/GPU_monitor -e gpu-live
```

- [ ] **Step 6: Protect main without requiring PRs**

Configure a branch rule that denies force pushes and deletion, does not require
a pull request, and permits normal pushes by trusted repository writers:

```bash
gh api --method PUT \
  repos/IRCVLab/GPU_monitor/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": false,
  "lock_branch": false,
  "allow_fork_syncing": false
}
JSON
```

Do not configure a pre-push required-check rule that would prevent a new direct
commit from entering `main`; CI is the post-push deployment gate. Read the rule
back through the API and verify PR review and required-check fields are null,
while force pushes and deletion are disabled.

- [ ] **Step 7: Push the feature branch and verify GitHub CI**

```bash
git push -u origin feature/ci-cd-foundation
```

Verify `ci/required` succeeds for the pushed SHA. This feature-branch push must not deploy.

- [ ] **Step 8: Update main only after credentials and rollback readiness are confirmed**

Push or merge the verified branch into `main`. Observe:

1. `ci` runs for the exact new `main` SHA.
2. `deploy-gpu-live` starts only after successful completion.
3. Authorization reports the same SHA.
4. Upload, activation, health check, and status complete.
5. Live serves the new SHA and Storage remains healthy.

- [ ] **Step 9: Exercise rollback evidence without fabricating failure**

Confirm the remote status records include the new current SHA and prior previous SHA. Do not deliberately break Live. Use the already-tested automatic rollback path for real activation failures and retain the manual `rollback live` command for recovery.
