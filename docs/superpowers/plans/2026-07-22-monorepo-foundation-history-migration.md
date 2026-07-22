# Monitoring Platform Foundation and History Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Produce a green, history-preserving hybrid monorepo containing GPU Monitor and Storage Monitor under independent application directories without modifying either live runtime.

**Architecture:** Fix the known source-baseline defects first, inventory and checkpoint every source ref, then rewrite only temporary clones into application prefixes and merge them into a new isolated integration repository. Original GPU live, GPU development, and Storage repositories remain unchanged and are retained as rollback sources.

**Tech Stack:** Git and git-filter-repo in an isolated tooling environment, Svelte 5/SvelteKit/Vite, Python 3.12/FastAPI/SQLAlchemy, Python unittest, shell verification, GitHub private repository.

---

## Scope and workspaces

Source repositories are read-only inputs during migration:

- GPU development source: ssh://ircv@166.104.167.11:2200/home/ircv/workspace/monitoring_v2_dev
- GPU live source: ssh://ircv@166.104.167.11:2200/home/ircv/workspace/monitoring_v2
- Storage source: /Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard
- Planning worktree: /Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
- Target GitHub repository: https://github.com/IRCVLab/GPU_monitor.git

No task may restart tmux, change a live port, write into either remote GPU working tree, deploy Storage agents, or modify production environment files.

## Planned repository structure

    apps/
      gpu-monitor/
        backend/
        frontend/
        docs/
        scripts/
        .env.example
      storage-monitor/
        agent/
        collector/
        scanner/
        viewer/
        config/
        data/
        deploy/
        docs/
    scripts/
      affected.py
      history_inventory.py
      verify_repository.py
    tests/
      test_affected.py
      test_history_inventory.py
      test_repository_layout.py
    docs/
      architecture.md
      development.md
      history-migration.md
      operations.md
      superpowers/
    .github/
      CODEOWNERS
    CONTRIBUTING.md
    Makefile
    README.md
    SECURITY.md

The application code remains framework-local. This plan does not extract shared UI components or change product behavior.

### Task 1: Repair the GPU frontend reproducible-install baseline

**Files:**
- Modify: frontend/package.json:12-23
- Modify: frontend/package-lock.json
- Verify: frontend/src/**/*.test.mjs
- Verify: frontend/src/**/*.test.js

- [ ] **Step 1: Record the existing clean-install failure**

Run:

~~~bash
cd /Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration/frontend
rm -rf node_modules
npm ci
~~~

Expected before the fix: FAIL with an ERESOLVE conflict between Vite 6 and @sveltejs/vite-plugin-svelte 4.

- [ ] **Step 2: Align the Svelte Vite plugin with Vite 6**

Change the development dependency to a Vite-6-compatible major version:

~~~json
"@sveltejs/vite-plugin-svelte": "^5.0.0"
~~~

Do not modify application source code in this task.

- [ ] **Step 3: Regenerate the lockfile from a clean state**

Run:

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
cd "$planning/frontend"
rm -rf node_modules package-lock.json
npm install
npm ci
~~~

Expected: both commands exit 0 without legacy-peer-deps or force.

- [ ] **Step 4: Verify frontend behavior**

Run:

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
cd "$planning/frontend"
npm run check
npm run build
while IFS= read -r -d '' test_file; do node "$test_file"; done < <(find src -type f -name '*.test.mjs' -print0)
~~~

Expected: Svelte check reports zero errors and warnings, production build succeeds, and all Node contract tests pass.

- [ ] **Step 5: Commit**

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
cd "$planning"
git add frontend/package.json frontend/package-lock.json
git commit -m "fix(frontend): align Svelte Vite dependencies"
git update-ref refs/migration/frontend-baseline HEAD
~~~

### Task 2: Repair the GPU backend reproducible-test baseline

**Files:**
- Modify: backend/requirements.txt
- Modify: backend/tests/test_note_priority.py:1-18
- Modify when identified by the scan: backend/tests/test_notes_validation.py and other expiry-validation tests
- Test: backend/tests/test_note_priority.py
- Test: backend/tests/test_note_admin_override.py

- [ ] **Step 1: Lock the missing SQLAlchemy async dependency**

Add an explicit compatible dependency after SQLAlchemy:

~~~text
greenlet==3.1.1
~~~

The exact version may be adjusted only if Python 3.10 and 3.12 installation tests prove a compatibility issue.

- [ ] **Step 2: Replace the expired fixed timestamp**

Replace:

~~~python
FUTURE = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
~~~

with a function so each test receives a fresh value:

~~~python
def future_time(*, days: int = 365) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)
~~~

Replace FUTURE usages with future_time() and explicit offsets where needed. Do not replace the fixture timestamp at the legacy schema SQL row because it is historical data and not validated as a future expiration.

- [ ] **Step 3: Prove the dependency and focused tests from a clean environment**

Run:

~~~bash
cd /Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
rm -rf .venv
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
python -c "import greenlet"
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python -m unittest backend.tests.test_note_priority backend.tests.test_note_admin_override -v
~~~

Expected: greenlet imports and all focused tests pass.

- [ ] **Step 4: Scan for other expiring fixed test constants**

Run:

~~~bash
grep -RInE 'FUTURE|datetime\(2026|expires_at=.*2026' backend/tests
~~~

Review every result. Replace only future-validity assumptions with relative times.

- [ ] **Step 5: Run the complete backend suite**

~~~bash
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python -m unittest discover -s backend/tests -v
~~~

Expected: all tests pass.

- [ ] **Step 6: Clean and commit**

~~~bash
deactivate
rm -rf .venv
git add backend/requirements.txt
git diff --name-only -- backend/tests | while IFS= read -r changed_test; do
  test -z "$changed_test" || git add "$changed_test"
done
git commit -m "fix(backend): restore reproducible test environment"
git update-ref refs/migration/backend-baseline HEAD
~~~

### Task 3: Lock the Storage source baseline

**Files:**
- Read: /Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard/**
- Create in target later: docs/history-migration.md
- No Storage source edits expected

- [ ] **Step 1: Verify Storage working tree and ref**

~~~bash
storage=/Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard
git -C "$storage" status --short --branch
git -C "$storage" rev-parse feature/multiserver-storage-dashboard
~~~

Expected: no uncommitted files and HEAD includes commit 0d7e1dc or a documented successor.

- [ ] **Step 2: Run complete local Storage verification without writing caches**

~~~bash
storage=/Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard
storage_verify=$(mktemp -d /tmp/storage-monorepo-baseline.XXXXXX)
trap 'rm -rf "$storage_verify"' EXIT
git clone --no-hardlinks --single-branch \
  --branch feature/multiserver-storage-dashboard \
  "$storage" "$storage_verify/repo"
cd "$storage_verify/repo"
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check
bash deploy/test_deploy_scripts.sh
if [ "$(uname -s)" = Linux ]; then
  bash deploy/verify-linux.sh --local
else
  printf '%s\n' 'SKIP: Linux-only hstscan uses SYS_getdents64; covered by Step 3 remote Linux verification.'
fi
git diff --check
git -C "$storage" status --short
~~~

Expected: 224 or more tests pass, supported host-local checks exit 0, the Linux-only scanner build is skipped explicitly on non-Linux hosts, verification artifacts remain only in the temporary clone, and the original Storage worktree remains clean. The same Storage commit is still required to pass Step 3's remote Linux verification.

- [ ] **Step 3: Prove the scanner in the existing Linux verification environment**

~~~bash
storage=/Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard
storage_verify=$(mktemp -d /tmp/storage-monorepo-linux.XXXXXX)
trap 'rm -rf "$storage_verify"' EXIT
git clone --no-hardlinks --single-branch \
  --branch feature/multiserver-storage-dashboard \
  "$storage" "$storage_verify/repo"
cd "$storage_verify/repo"
STORAGE_VIZ_LINUX_HOST=ircv@166.104.167.11 STORAGE_VIZ_LINUX_PORT=2200 bash deploy/verify-linux.sh --remote
git -C "$storage" status --short
~~~

Expected: scanner build/test succeeds on Linux, overall_exit_code is 0, remote_cleanup is removed, verification artifacts remain only in the temporary clone, and the original Storage worktree remains clean. This command uses the existing strict-host-key and identity-file configuration and must not use a password fallback.

- [ ] **Step 4: Commit only if baseline documentation needs correction**

Do not change Storage behavior. If no correction is needed, create no commit.

### Task 4: Add tested history-inventory tooling

**Files:**
- Create: scripts/history_inventory.py
- Create: tests/test_history_inventory.py
- Create: docs/history-migration.md

- [ ] **Step 1: Write a failing inventory test**

The test creates a temporary Git repository with two branches, one annotated tag, and two authors. It invokes history_inventory.py and asserts the JSON output contains:

~~~python
assert inventory["refs"]["refs/heads/main"]["commit_count"] == 2
assert "refs/heads/feature/test" in inventory["refs"]
assert "refs/tags/v0.1.0" in inventory["refs"]
assert sorted(inventory["authors"]) == [
    "Alice <alice@example.com>",
    "Bob <bob@example.com>",
]
~~~

- [ ] **Step 2: Run the test and confirm failure**

~~~bash
python3.12 -m unittest tests.test_history_inventory -v
~~~

Expected: FAIL because scripts/history_inventory.py does not exist.

- [ ] **Step 3: Implement the minimum inventory script**

The script must:

- accept repository path and output path;
- enumerate refs/heads and refs/tags with for-each-ref;
- record object ID, object type, commit count, author set, and annotated-tag target;
- record repository status without including file contents;
- emit deterministic sorted JSON;
- fail if the source repository is dirty unless --allow-dirty is explicitly supplied.

- [ ] **Step 4: Run the focused test**

~~~bash
python3.12 -m unittest tests.test_history_inventory -v
~~~

Expected: PASS.

- [ ] **Step 5: Commit**

~~~bash
git add scripts/history_inventory.py tests/test_history_inventory.py docs/history-migration.md
git commit -m "chore: add source history inventory tooling"
~~~

### Task 5: Capture immutable source checkpoints without touching live worktrees

**Files:**
- Modify: docs/history-migration.md
- Create outside Git: .migration/gpu-dev-source.git
- Create outside Git: .migration/gpu-live-source.git
- Create outside Git: .migration/storage-source.git
- Create outside Git: .migration/inventory/*.json
- Create outside Git: .migration/monitoring-platform-target.git

- [ ] **Step 1: Ensure migration scratch paths are ignored**

Add to the integration repository ignore rules if needed:

~~~text
.migration/
.tools/
~~~

Commit the ignore-only change before creating those directories.

- [ ] **Step 2: Clone source repositories as mirrors**

~~~bash
mkdir -p .migration/inventory
SSHPASS="$GPU_SOURCE_PASSWORD" sshpass -e git clone --mirror \
  ssh://ircv@166.104.167.11:2200/home/ircv/workspace/monitoring_v2_dev \
  .migration/gpu-dev-source.git
SSHPASS="$GPU_SOURCE_PASSWORD" sshpass -e git clone --mirror \
  ssh://ircv@166.104.167.11:2200/home/ircv/workspace/monitoring_v2 \
  .migration/gpu-live-source.git
git clone --mirror \
  /Users/shchoi/.config/superpowers/worktrees/storage-viz/multiserver-storage-dashboard \
  .migration/storage-source.git
~~~

Never write a new remote into either source working tree.

- [ ] **Step 3: Inventory every mirror**

~~~bash
python3.12 scripts/history_inventory.py .migration/gpu-dev-source.git .migration/inventory/gpu-dev.json
python3.12 scripts/history_inventory.py .migration/gpu-live-source.git .migration/inventory/gpu-live.json
python3.12 scripts/history_inventory.py .migration/storage-source.git .migration/inventory/storage.json
shasum -a 256 .migration/inventory/*.json > .migration/inventory/SHA256SUMS
~~~

Copy the redacted summary and checksums, not credentials or source paths containing secrets, into docs/history-migration.md.

- [ ] **Step 4: Create checkpoint refs in the local mirrors only**

~~~bash
git -C .migration/gpu-dev-source.git tag pre-monorepo-gpu-dev refs/heads/feature/compact-gpu-dashboard
git -C .migration/gpu-live-source.git tag pre-monorepo-gpu-live refs/heads/main
git -C .migration/storage-source.git tag pre-monorepo-storage refs/heads/feature/multiserver-storage-dashboard
~~~

The original working repositories remain unchanged.

- [ ] **Step 5: Install and verify the pinned history scanner**

Download the official Gitleaks 8.30.1 Darwin arm64 release into .tools/gitleaks and verify the published archive checksum before extraction:

~~~bash
mkdir -p .tools/gitleaks
curl -fL -o .tools/gitleaks/gitleaks.tar.gz   https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_darwin_arm64.tar.gz
printf '%s  %s
'   b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5   .tools/gitleaks/gitleaks.tar.gz | shasum -a 256 -c -
tar -xzf .tools/gitleaks/gitleaks.tar.gz -C .tools/gitleaks gitleaks
.tools/gitleaks/gitleaks version
~~~

On another architecture, use the corresponding asset and checksum from the same official release and record that substitution.

- [ ] **Step 6: Run full-history secret and generated-data scans**

Run Gitleaks git-history detection against each mirror with redacted output saved outside Git. Separately enumerate tracked environment files, databases, caches, node_modules, non-sample JSON snapshots, and generated browser/build output. Treat data/*.sample.json and the privacy-safe Storage data/hosts.json manifest as fixtures, not runtime snapshots.

Record scanner version, archive checksum, config hash, exit status, and any remediation in docs/history-migration.md. Do not print discovered secret values.

If a real secret is found:

1. rotate the credential;
2. add a redaction mapping;
3. combine content redaction with the single path-prefix rewrite;
4. regenerate inventories and mappings;
5. do not push any affected history before remediation.

- [ ] **Step 7: Initialize the isolated local target and preserve archives before rewriting**

Create .migration/monitoring-platform-target.git as a bare repository. Before Task 6 runs, copy every unrewritten source branch into:

- refs/heads/archive/gpu-live/<original-branch>
- refs/heads/archive/gpu-dev/<original-branch>
- refs/heads/archive/storage/<original-branch>

Copy source tags under refs/tags/archive/source-tag/<source>/<tag>, then copy these checkpoint tags exactly:

- pre-monorepo-gpu-live
- pre-monorepo-gpu-dev
- pre-monorepo-storage

Transfer objects and refs with explicit fetch refspecs before any `git update-ref` operation; `update-ref` alone cannot point a fresh bare repository at objects it does not contain. Use:

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
target="$planning/.migration/monitoring-platform-target.git"
git init --bare "$target"

git -C "$target" fetch --no-tags "$planning/.migration/gpu-live-source.git" \
  '+refs/heads/*:refs/heads/archive/gpu-live/*' \
  '+refs/tags/*:refs/tags/archive/source-tag/gpu-live/*' \
  '+refs/tags/pre-monorepo-gpu-live:refs/tags/pre-monorepo-gpu-live'
git -C "$target" fetch --no-tags "$planning/.migration/gpu-dev-source.git" \
  '+refs/heads/*:refs/heads/archive/gpu-dev/*' \
  '+refs/tags/*:refs/tags/archive/source-tag/gpu-dev/*' \
  '+refs/tags/pre-monorepo-gpu-dev:refs/tags/pre-monorepo-gpu-dev'
git -C "$target" fetch --no-tags "$planning/.migration/storage-source.git" \
  '+refs/heads/*:refs/heads/archive/storage/*' \
  '+refs/tags/*:refs/tags/archive/source-tag/storage/*' \
  '+refs/tags/pre-monorepo-storage:refs/tags/pre-monorepo-storage'
~~~

Use explicit `git for-each-ref` comparisons to verify that every archived ref object ID matches its mirror. This local target has no GitHub remote.

- [ ] **Step 8: Commit the redacted checkpoint documentation**

~~~bash
git add .gitignore docs/history-migration.md
git commit -m "docs: record pre-monorepo source checkpoints"
~~~

### Task 6: Build prefixed GPU and Storage histories in disposable clones

**Files:**
- Create outside Git: .tools/filter-repo/
- Create outside Git: .migration/gpu-current-worktree/
- Create outside Git: .migration/gpu-prefixed.git
- Create outside Git: .migration/storage-prefixed.git
- Modify: docs/history-migration.md

- [ ] **Step 1: Create a local import branch containing only product baseline fixes**

Create import/gpu-current from feature/compact-gpu-dashboard in a temporary worktree. Cherry-pick only the Task 1 and Task 2 product-fix commits. Do not cherry-pick the monorepo design or plan commits.

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
source_bare=$(git -C "$planning" rev-parse --path-format=absolute --git-common-dir)
gpu_current="$planning/.migration/gpu-current-worktree"
frontend_fix=$(git -C "$planning" rev-parse refs/migration/frontend-baseline)
backend_fix=$(git -C "$planning" rev-parse refs/migration/backend-baseline)
git -C "$source_bare" worktree add "$gpu_current" -b import/gpu-current feature/compact-gpu-dashboard
git -C "$gpu_current" cherry-pick "$frontend_fix" "$backend_fix"
~~~

Run the clean GPU baseline again in this worktree. This branch is local only and leaves the remote development repository unchanged.

- [ ] **Step 2: Install git-filter-repo in an isolated tool environment**

~~~bash
python3.12 -m venv .tools/filter-repo
. .tools/filter-repo/bin/activate
pip install git-filter-repo
git-filter-repo --version
~~~

Record the exact installed version.

- [ ] **Step 3: Create the validated GPU import branch clone and prefix it**

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
source_bare=$(git -C "$planning" rev-parse --path-format=absolute --git-common-dir)
git clone --single-branch --branch import/gpu-current \
  "$source_bare" "$planning/.migration/gpu-prefixed"
cd "$planning/.migration/gpu-prefixed"
git filter-repo --to-subdirectory-filter apps/gpu-monitor --force
~~~

Expected: all selected GPU files now live under apps/gpu-monitor and the original source mirror is unchanged.

- [ ] **Step 4: Create a selected Storage branch clone and prefix it**

~~~bash
planning=/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/monorepo-integration
git clone --single-branch --branch feature/multiserver-storage-dashboard \
  "$planning/.migration/storage-source.git" \
  "$planning/.migration/storage-prefixed"
cd "$planning/.migration/storage-prefixed"
git filter-repo --to-subdirectory-filter apps/storage-monitor --force
~~~

- [ ] **Step 5: Generate original-to-rewritten commit mappings**

For GPU, compare the local import/gpu-current branch to the rewritten GPU branch so the two baseline-fix commits are included. Also compare the original feature/compact-gpu-dashboard checkpoint separately to prove it is an ancestor. For Storage, compare the selected source branch directly. Pair commits in topological order and verify equal counts. Record mappings in machine-readable files under .migration and summarize counts in docs/history-migration.md.

Fail if commit count, author, author date, or subject differs unexpectedly.

### Task 7: Assemble the new monorepo integration history

**Files:**
- New isolated target repository generated from prefixed histories
- Create: README.md
- Create: CONTRIBUTING.md
- Create: SECURITY.md
- Create: .github/CODEOWNERS
- Create: Makefile
- Create: tests/test_repository_layout.py
- Modify: docs/history-migration.md

- [ ] **Step 1: Write and checkpoint the failing repository-layout test in the planning branch**

Assert:

~~~python
assert Path("apps/gpu-monitor/frontend/package.json").is_file()
assert Path("apps/gpu-monitor/backend/main.py").is_file()
assert Path("apps/storage-monitor/viewer/serve.py").is_file()
assert Path("apps/storage-monitor/agent/scan_runner.py").is_file()
assert not Path("frontend").exists()
assert not Path("backend").exists()
~~~

Also reject tracked environment files, virtual environments, node_modules, runtime JSON snapshots, databases, caches, and browser output while allowing privacy-safe Storage sample fixtures.

Run the test in the planning worktree and confirm it fails, then commit only the contract test:

~~~bash
git add tests/test_repository_layout.py
git commit -m "test: define monorepo layout contract"
~~~

- [ ] **Step 2: Add rewritten GPU main to the existing isolated local target**

Use these paths:

~~~text
.migration/monitoring-platform-target.git
/Users/shchoi/.config/superpowers/worktrees/monitoring-platform/assembled-monorepo
~~~

The bare target already contains the unrewritten archive refs and checkpoint tags from Task 5. Push the rewritten GPU import branch to that local repository as main, then add the assembled-monorepo worktree for main. Do not add the GitHub remote and do not replace the planning worktree in-place.

- [ ] **Step 3: Transfer reviewed foundation artifacts into the assembled target**

Add the planning repository as a local temporary remote, fetch feature/monorepo-integration, and restore only:

- docs/superpowers/specs/2026-07-22-monitoring-platform-monorepo-design.md
- docs/superpowers/plans/2026-07-22-monorepo-foundation-history-migration.md
- scripts/history_inventory.py
- tests/test_history_inventory.py
- tests/test_repository_layout.py
- docs/history-migration.md

Remove the temporary planning remote after checkout. Run tests/test_repository_layout.py and confirm it still fails because Storage has not yet been merged.

Commit the restored foundation files before merging unrelated Storage history so the index is clean and the foundation files remain separate from the Storage import merge:

~~~bash
git add \
  docs/superpowers/specs/2026-07-22-monitoring-platform-monorepo-design.md \
  docs/superpowers/plans/2026-07-22-monorepo-foundation-history-migration.md \
  scripts/history_inventory.py \
  tests/test_history_inventory.py \
  tests/test_repository_layout.py \
  docs/history-migration.md
git commit -m "chore: add monorepo migration foundation"
~~~

- [ ] **Step 4: Merge Storage prefixed history**

Fetch the rewritten Storage branch as import/storage and merge with allow-unrelated-histories using:

~~~text
chore: import storage monitor history
~~~

- [ ] **Step 5: Verify pre-rewrite archives survived target assembly**

Compare every archive ref and checkpoint tag in .migration/monitoring-platform-target.git with the source inventories recorded in Task 5. Fail before committing if any ref is missing or changed.

- [ ] **Step 6: Add root documentation and ownership files**


Root documentation must clearly state:

- one repository, two independent products;
- application-local setup commands;
- no application cross-imports;
- no generated or collected data in Git;
- main merge is the later production deployment authorization;
- production deployment is not enabled by this foundation plan.

CODEOWNERS initially protects .github, deploy, root lock/config files, and scripts.

- [ ] **Step 7: Run the layout and history tests**

~~~bash
python3.12 -m unittest tests.test_repository_layout tests.test_history_inventory -v
git log --follow -- apps/gpu-monitor/backend/main.py
git log --follow -- apps/storage-monitor/viewer/serve.py
git fsck --full
git diff --check
~~~

Expected: tests pass, both representative histories reach pre-migration commits, and fsck reports no errors.

- [ ] **Step 8: Commit**

~~~bash
git add README.md CONTRIBUTING.md SECURITY.md .github/CODEOWNERS Makefile tests docs scripts
git commit -m "chore: establish monitoring platform monorepo"
~~~

### Task 8: Normalize application-local commands after prefix migration

**Files:**
- Move within history-derived tree: apps/gpu-monitor/run_monitoring.sh to apps/gpu-monitor/scripts/run_monitoring.sh
- Move: apps/gpu-monitor/run_development.sh to apps/gpu-monitor/scripts/run_development.sh
- Modify: script path calculations
- Create: apps/gpu-monitor/README.md
- Create or update: apps/storage-monitor/README.md
- Modify: Makefile
- Test: tests/test_repository_layout.py

- [ ] **Step 1: Add failing command-contract assertions**

Test that root commands exist:

~~~text
make test-gpu
make build-gpu
make test-storage
make verify
~~~

Test that GPU scripts resolve their application root from the script location rather than the caller working directory.

- [ ] **Step 2: Move scripts and repair path resolution**

Each shell script sets its application root using its own file location. No absolute /home/ircv/workspace path is allowed.

- [ ] **Step 3: Add root delegation commands**

Makefile delegates into application directories and does not introduce a shared package manager.

- [ ] **Step 4: Run both clean baselines from new paths**

GPU:

~~~bash
cd apps/gpu-monitor/frontend
npm ci
npm run check
npm run build
cd ..
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password \
  python -m unittest discover -s backend/tests -v
deactivate
rm -rf .venv
~~~

Storage:

~~~bash
assembled=$(git rev-parse --show-toplevel)
storage_verify=$(mktemp -d /tmp/storage-monorepo-command-check.XXXXXX)
trap 'rm -rf "$storage_verify"' EXIT
git clone --no-hardlinks "$assembled" "$storage_verify/repo"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude 'output/verification/' \
  "$assembled/apps/storage-monitor/" \
  "$storage_verify/repo/apps/storage-monitor/"
cd "$storage_verify/repo/apps/storage-monitor"
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider
find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check
bash deploy/test_deploy_scripts.sh
if [ "$(uname -s)" = Linux ]; then
  bash scanner/test_hstscan.sh
  bash deploy/verify-linux.sh --local
else
  printf '%s\n' 'SKIP: Linux-only scanner tests use SYS_getdents64; covered by Task 3 remote Linux verification.'
fi
git -C "$assembled" status --short
~~~

Expected: Storage checks exercise the current assembled-worktree content copied into the disposable clone, all generated verification artifacts remain in that clone, and the assembled worktree contains only the intended Task 8 changes.

- [ ] **Step 5: Commit**

~~~bash
git add -A \
  apps/gpu-monitor/run_monitoring.sh \
  apps/gpu-monitor/run_development.sh \
  apps/gpu-monitor/scripts
git add \
  apps/gpu-monitor/README.md \
  apps/storage-monitor/README.md \
  Makefile \
  tests/test_repository_layout.py
git commit -m "chore: normalize application-local commands"
~~~

### Task 9: Verify foundation acceptance without publishing or deploying

**Files:**
- Modify: docs/history-migration.md
- Create: docs/development.md
- Create: docs/architecture.md

- [ ] **Step 1: Run complete verification**

~~~bash
make verify
git fsck --full
git status --short
~~~

Expected: all supported checks pass and status is clean.

- [ ] **Step 2: Compare source and imported history**

Automated comparison must prove:

- selected branch commit counts match;
- author sets match;
- checkpoint commit content is reachable;
- GPU live-only commits remain reachable under archive refs;
- Storage checkpoint is reachable;
- annotated tags and archive refs resolve;
- representative file history follows through prefixes.

- [ ] **Step 3: Verify live isolation**

Read-only checks on 166.104.167.11 must prove:

- GPU live commit and working-tree status are unchanged;
- live tmux session names and listening ports are unchanged;
- GPU health endpoints still return expected responses;
- no monorepo process is running on the server;
- Storage services were not restarted.

- [ ] **Step 4: Ask Kimi for independent foundation validation**

Run Kimi Code model kimi-code/k3 in read-only mode against:

- the final spec;
- this implementation plan;
- the assembled monorepo;
- source inventory and mapping summaries;
- verification logs.

Required verdict:

~~~text
APPROVED
~~~

or actionable issues. Fix valid issues and rerun until approved.

- [ ] **Step 5: Run a separate Codex verifier review**

The verifier checks live isolation, history reachability, source cleanliness, test evidence, generated-data exclusions, and Kimi artifact completeness.

- [ ] **Step 6: Commit final documentation**

~~~bash
git add docs
git commit -m "docs: verify monorepo foundation migration"
~~~

## Foundation completion gate

Do not configure GitHub Actions deployment, register a production runner, install systemd services, change the GitHub default branch, or push rewritten main until all of the following are true:

- GPU plain npm ci succeeds.
- GPU frontend check and build pass.
- GPU backend tests pass from requirements alone.
- Storage supported tests pass.
- Source inventories and checkpoint refs are recorded.
- Secret scan disposition is recorded.
- History mappings pass automated checks.
- Live GPU and Storage services are unchanged.
- Kimi returns APPROVED.
- Codex verifier returns approved evidence.

After this gate, write and review the separate CI/contribution plan, followed by the central-service deployment plan, followed by the Storage-agent rollout plan.
