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
