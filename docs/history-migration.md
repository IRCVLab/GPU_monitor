# History migration inventory

Use `scripts/history_inventory.py` to create a deterministic JSON inventory of a source Git repository before migrating history.

```bash
python3.12 scripts/history_inventory.py /path/to/source-repo output/history-inventory.json
```

The inventory records refs under `refs/heads` and `refs/tags`, object IDs and types, reachable commit counts, author identities, annotated-tag targets, and porcelain status metadata. It never reads or emits file contents.

Dirty source repositories are rejected by default so the inventory represents committed history only. If an explicitly approved workflow needs to capture a dirty work tree, pass `--allow-dirty`; the status section still contains only Git status codes and paths, not file contents.

## Task 5 checkpoint capture (redacted)

- Scope: immutable local checkpoint capture only; no history rewrite, GitHub remote, push, deploy, tmux restart, port change, or live worktree edit performed.
- Source labels are redacted to labels only in this document; GPU access used SSH key authentication, not password/sshpass.
- Source history inventories were regenerated with the tracked CLI only: `python3.12 scripts/history_inventory.py <local-bare-mirror> <inventory-json>`. The obsolete ignored bare-inventory helper was removed and is not used.
- Tracked inventory CLI validation: `python3.12 -m unittest tests.test_history_inventory` reports 8 tests passing after the bare-mirror fix; RED phase previously failed two new bare-mirror tests with `not a Git work tree`.

### Mirror inventory

| Source | Bare | Dirty | Status entries | Heads | Tags at inventory time | Authors | HEAD OID |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| GPU dev | true | false | 0 | 5 | 0 | 3 | `64c4b838d6e1293daf52ab0039084a2b9f84bc59` |
| GPU live | true | false | 0 | 2 | 0 | 2 | `f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce` |
| Storage | true | false | 0 | 3 | 0 | 1 | `0d7e1dcf2cfd9cfe819851e37384e8bb80930365` |

Inventory SHA-256 regenerated with tracked script:

```text
0f82cd5821ead6ecf4b217abc83007086a6fd3cd7421f4f7183d524270e9fc2c  .migration/inventory/gpu-dev.json
1aa85d1d070db2a28c09f2cad10d4779315614727e6240dfb3de349c9f36c939  .migration/inventory/gpu-live.json
5c6ae63aaa303764d2c07f39744bb2abc6e1edde4ff179e06f9cb3c4dc883d01  .migration/inventory/storage.json
```

Tracked-script regenerated checksums matched the previous inventory files byte-for-byte: `identical`.

### Checkpoint tags

| Tag | Source ref | OID |
| --- | --- | --- |
| `pre-monorepo-gpu-dev` | `gpu-dev:feature/compact-gpu-dashboard` | `64c4b838d6e1293daf52ab0039084a2b9f84bc59` |
| `pre-monorepo-gpu-live` | `gpu-live:main` | `f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce` |
| `pre-monorepo-storage` | `storage:feature/multiserver-storage-dashboard` | `0d7e1dcf2cfd9cfe819851e37384e8bb80930365` |

### Secret scan

- Gitleaks version: `8.30.1`
- Verified Darwin arm64 release archive SHA-256: `b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5`
- Scan config: official `v8.30.1/config/gitleaks.toml`, SHA-256 `e163e53b9e7e8a8511e77271e2b323ed057759542a6d988258afe3a1fa329caf`

| Source | Exit | Findings | Commits scanned | Data scanned |
| --- | ---: | ---: | ---: | --- |
| GPU dev | 0 | 0 | 217 | 2166332 bytes (2.17 MB) |
| GPU live | 0 | 0 | 35 | 717325 bytes (717.33 KB) |
| Storage | 0 | 0 | 170 | 3159010 bytes (3.16 MB) |

Remediation status: no Gitleaks findings in full-history scans; no credential rotation requested or performed.

### Generated/runtime path inventory

| Source | Matched paths | Category counts |
| --- | ---: | --- |
| GPU dev | 16 | cache_or_dependency_dir=8, environment_file=1, generated_browser_or_build_output=7 |
| GPU live | 9 | cache_or_dependency_dir=8, environment_file=1 |
| Storage | 16 | cache_or_dependency_dir=7, fixture_json_allowed=5, generated_browser_or_build_output=4 |

Generated-path inventory SHA-256:

```text
d9bb9952dc5924627927e6078b36e988ac16f361a7884e6d3855537840704993  .migration/inventory/gpu-dev-generated-paths.json
f1bcf4684619d4337a06e1c914fca77ef488c4f810aa1c7f282600d25587f4f2  .migration/inventory/gpu-live-generated-paths.json
081a9747841e3bb8d500137fc17ab717dc4c7ecc5ba611e413bd73c3052d24d3  .migration/inventory/storage-generated-paths.json
```

### Isolated target archive refs

- Target bare repository has no configured remotes.
- Archived/ref verification checks: `16` total, `0` failures; counts `{"checkpoint-tag-exact": 3, "head": 10, "source-tag-archive": 3}`.
- Ref verification SHA-256: `1aad5ecb628ad16a06e00b786aa19640dd0ef60d0f65ab8d65c393839055e72a`

## Task 8 archive branch preservation preflight

Verification timestamp: `2026-07-23T18:48:20Z`.

`python3.12 scripts/preserve_archive_refs.py --remote origin --dry-run` read the
current remote refs and matched all 10 archive branches to the frozen migration
inventory. This preflight did not create or push tags and did not delete
branches. Accordingly, peeled tag OIDs remain explicitly pending rather than
being represented as verified.

| Branch | Frozen branch OID | Intended annotated tag | Peeled tag OID | Reachable commits |
| --- | --- | --- | --- | ---: |
| `archive/gpu-dev/codex/task5-failure-veil` | `7aa30626cf0ceda3b1d5aada4c19d834ecd4b834` | `archive/branch/gpu-dev/codex/task5-failure-veil` | pending (not pushed) | 160 |
| `archive/gpu-dev/develop` | `cf70ad07bda5b9b2efb7fb3b06869cc080f95c9a` | `archive/branch/gpu-dev/develop` | pending (not pushed) | 46 |
| `archive/gpu-dev/feature/apple-dashboard-refinement` | `ca9ec6614458a6049041dca3c3b874ae4f34bf6f` | `archive/branch/gpu-dev/feature/apple-dashboard-refinement` | pending (not pushed) | 57 |
| `archive/gpu-dev/feature/compact-gpu-dashboard` | `64c4b838d6e1293daf52ab0039084a2b9f84bc59` | `archive/branch/gpu-dev/feature/compact-gpu-dashboard` | pending (not pushed) | 217 |
| `archive/gpu-dev/main` | `c50f9d2aa9465d742c870ba47793589807832efa` | `archive/branch/gpu-dev/main` | pending (not pushed) | 33 |
| `archive/gpu-live/main` | `f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce` | `archive/branch/gpu-live/main` | pending (not pushed) | 34 |
| `archive/gpu-live/old` | `b18c78fd7adda3c6065df32d183524f281fa94fe` | `archive/branch/gpu-live/old` | pending (not pushed) | 30 |
| `archive/storage/checkpoint/ai-advisor-workspace-20260717` | `0685b5f2161041ccce7025a8e5d2b4dd140d6590` | `archive/branch/storage/checkpoint/ai-advisor-workspace-20260717` | pending (not pushed) | 81 |
| `archive/storage/feature/multiserver-storage-dashboard` | `0d7e1dcf2cfd9cfe819851e37384e8bb80930365` | `archive/branch/storage/feature/multiserver-storage-dashboard` | pending (not pushed) | 195 |
| `archive/storage/master` | `ea59cb591fbf408c583bdfad570726d8787cc25a` | `archive/branch/storage/master` | pending (not pushed) | 80 |

The preservation tool fails closed on moved branch OIDs, lightweight or
mismatched tags, invalid or directory/file-conflicting tag refs, and missing
local commit objects. It creates reproducible unsigned annotated tags, pushes
tag object OIDs with expect-absent leases, and permits branch deletion only in
the separate `--delete-verified-branches` mode. That mode re-verifies the same
frozen OIDs and tag peel targets before issuing one atomic push with an explicit
expected-OID lease for every deleted branch.

There remains an unavoidable short porcelain-level interval between the fresh
remote verification and the deletion push. Branch movement in that interval is
blocked by the explicit leases and causes the atomic push to fail without
partial deletion. Tag mutation in that interval is not covered by those branch
leases, so archive tags must remain append-only and branch deletion still
requires a separate operational confirmation.

## Task 6 prefixed history build (redacted)

- Scope: disposable local clones/worktrees only; no target remote added, no push, deploy, tmux, port, service, live/dev/Storage worktree, source mirror history rewrite, archive ref rewrite, or planning-history rewrite performed.
- Local import branch: `import/gpu-current` was created from `feature/compact-gpu-dashboard` and contains exactly the two requested product baseline cherry-picks: `refs/migration/frontend-baseline` then `refs/migration/backend-baseline`. The pre-monorepo GPU checkpoint remains the parent ancestry below those two product-fix commits.
- Tooling: `git-filter-repo` was installed in isolated ignored tool environment `.tools/filter-repo/`; pip package version `2.47.0`; `git-filter-repo --version` output `a40bce548d2c`.
- Rewrite mode: prefixed clones were recreated with `--preserve-commit-hashes` in addition to `--to-subdirectory-filter` so author, author date, and subject comparisons remain exact, including merge/revert subjects that contain commit IDs.

### Prefixed branch results

| Source | Original ref | Prefixed path | Original commits | Rewritten commits | Author/date/subject check | HEAD paths outside prefix |
| --- | --- | --- | ---: | ---: | --- | ---: |
| GPU | `import/gpu-current` | `apps/gpu-monitor/` | 219 | 219 | pass | 0 |
| Storage | `feature/multiserver-storage-dashboard` | `apps/storage-monitor/` | 195 | 195 | pass | 0 |

### Machine-readable mapping artifacts

Ignored local artifacts under `.migration/`:

- `gpu-current-commit-map.json`: GPU original-to-prefixed commit map, topological order, metadata verification, and pre-monorepo GPU checkpoint ancestry/content verification.
- `storage-current-commit-map.json`: Storage original-to-prefixed commit map, topological order, metadata verification, and pre-monorepo Storage checkpoint ancestry/content verification.
- `task6-summary.json`: tool version, branch heads, mapping counts, and baseline cherry-pick verification summary.

### Checkpoint verification

| Checkpoint | Original blob entries | Prefixed blob entries | Original ancestor | Rewritten ancestor | Content match after prefix strip |
| --- | ---: | ---: | --- | --- | --- |
| `pre-monorepo-gpu-dev` | 198 | 198 | pass | pass | pass |
| `pre-monorepo-storage` | 84 | 84 | pass | pass | pass |

### Baseline verification

| Requested ref | Local cherry-pick subject | Metadata match | Stable patch-id match |
| --- | --- | --- | --- |
| `refs/migration/frontend-baseline` | `fix(frontend): align Svelte Vite dependencies` | pass | pass |
| `refs/migration/backend-baseline` | `fix(backend): restore reproducible test environment` | pass | pass |

### Clean GPU baseline checks

Executed in the ignored disposable `import/gpu-current` worktree:

- `npm ci --prefix frontend`: pass; npm reported 3 low-severity audit findings.
- `npm run check --prefix frontend`: pass, `svelte-check found 0 errors and 0 warnings`.
- `npm run build --prefix frontend`: pass.
- `SECRET_KEY=<dummy> ADMIN_PASSWORD=<dummy> python -m pytest backend/tests`: pass, 63 tests passed with 3 warnings.

## Task 9 foundation verification (redacted)

Task 9 created the root development and architecture documentation and rechecked the monorepo foundation without publishing or deploying. This section records the final executable foundation evidence at HEAD `a45cb90` (`Prevent root verification bytecode caches`) plus the remaining Kimi advisory-gate state. Later docs-only commits do not change the executable verification target.

### Executable verification

Fresh verification artifacts are stored outside Git under the planning worktree at `.superpowers/sdd/task-9-evidence/`.

| Check | Evidence artifact | Result |
| --- | --- | --- |
| Full final root verification at `a45cb90` | `make-verify-a45cb90.*` | exit 0 |
| Strict/full repository object integrity | `git-fsck-strict-full-a45cb90.*` | exit 0, stdout/stderr empty |
| Worktree status at `a45cb90` | `git-status-a45cb90.*` | exit 0, clean |
| Source/import history comparison | `foundation-history-verification-final.json` | 88 checks, 0 failures |
| Source refs | `source-ref-status-clean.stdout` | planning, Storage, and local mirror refs recorded |
| Live isolation snapshots | `live-readonly-snapshot.stdout`, `live-readonly-snapshot-final.stdout`, `live-readonly-diff.*` | no Task 9 live mutation detected |
| Kimi K3 final review attempts | `kimi-review.*`; later direct retry reported by operator | no final approval: hung attempt exit 130, later quota HTTP 403 |

`PATH="$PWD/apps/gpu-monitor/.venv/bin:$PATH" make verify` at final executable HEAD `a45cb90` exited 0 with this evidence:

- repository layout tests: 7 tests passed, including `test_root_python_verification_recipes_suppress_bytecode_writes`;
- history inventory tests: 8 tests passed;
- root Python verification recipes use `PYTHONDONTWRITEBYTECODE=1`, hardening root verification against `tests/__pycache__` bytecode caches;
- GPU frontend check: `svelte-check found 0 errors and 0 warnings`;
- GPU backend tests: 63 tests passed;
- GPU frontend build: Vite/SvelteKit build succeeded;
- Storage disposable-clone checks: 224 tests and 513 subtests passed;
- Storage deploy contract checks: all PASS;
- non-Linux scanner branch: explicit skip, covered by prior Linux verification;
- whitespace check: `git diff --check` passed.

The first direct `make verify` attempt failed because the current Python interpreter did not have the declared GPU backend dependencies installed. The accepted verification runs used an ignored app-local virtual environment at `apps/gpu-monitor/.venv` with packages installed from `apps/gpu-monitor/backend/requirements.txt` plus `pytest`.

### History/ref comparison

The Task 9 verifier compared current refs against the existing ignored inventories and commit-map artifacts:

- GPU imported history: 219 original commits, 219 rewritten commits, mapping length 219;
- Storage imported history: 195 original commits, 195 rewritten commits, mapping length 195;
- source and imported author sets matched for mapped histories;
- checkpoint content match flags remained true;
- active rewritten heads remained reachable from integration refs;
- all archive refs and checkpoint tags resolved;
- Task 5 ref-verification artifact still had zero failures;
- one GPU live-only commit remained reachable from `refs/heads/archive/gpu-live/main`;
- Storage checkpoint ref `0685b5f2161041ccce7025a8e5d2b4dd140d6590` remained reachable as an archive ref;
- representative `git log --follow` histories for GPU `backend/main.py` and Storage `viewer/serve.py` followed through their prefixes;
- tracked generated/runtime exclusions had zero violations.

### Live isolation

Remote read-only SSH checks used `BatchMode=yes` and `IdentitiesOnly=yes`. No restart, deploy, push, tmux mutation, port mutation, or remote add command was run.

Live GPU source state at the snapshot:

- `/home/ircv/workspace/monitoring_v2`: branch `main`, HEAD `f2ea62f5ba4dc6a791bf0faf3fee4153e83462ce`, status `## main...origin/main [ahead 2]`;
- `/home/ircv/workspace/monitoring_v2_dev`: branch `feature/compact-gpu-dashboard`, HEAD `64c4b838d6e1293daf52ab0039084a2b9f84bc59`, clean short status.

Read-only runtime snapshot showed the existing tmux session names and listening GPU ports, including `monitoring_v2_backend`, `monitoring_v2_frontend`, `monitoring_v2_slack_bridge`, development sessions, `storage-viz-direct`, and ports `8000`, `8001`, `5173`, and `5174`. GPU health endpoints on `127.0.0.1:8001/health` and `127.0.0.1:8000/health` returned `{"status":"ok"}`. Port `8011` was not listening and returned connection refused. No process containing `assembled-monorepo`, `monitoring-platform`, `apps/gpu-monitor`, or `apps/storage-monitor` was found on the server.

Storage service read-only state showed `storage-viz-dashboard.service` loaded, active, running, `NRestarts=0`, with start timestamp `Wed 2026-07-22 13:15:00 KST`; checked alternate Storage service names were not found/inactive. A second Task 9 live snapshot was diffed against the first to verify the local documentation work did not change live refs, tmux names, filtered ports, health responses, monorepo process scan, or Storage restart counters.

### Kimi state

Kimi history is recorded without converting earlier advisory approvals into final Task 9 approval:

- earlier Kimi plan validations returned `APPROVED` twice;
- the final required Task 9 Kimi K3 read-only review attempt hung, was terminated after a bounded wait, and `kimi-review.exit` records `exit=130` with partial progress text but no verdict;
- a later direct retry was rejected by the provider with quota HTTP 403;
- therefore there is no final Kimi `APPROVED` verdict for Task 9.

All executable checks passed at final executable HEAD `a45cb90`; the only remaining advisory/foundation-gate gap is final Kimi approval.
