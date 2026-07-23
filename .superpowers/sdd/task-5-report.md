# Task 5 Report: Server-side forced GPU deployment activation

## Status

Implemented and verified. The server-side forced deployment/activation/rollback boundary now exists under `apps/gpu-monitor/deploy/server/`, with regression coverage in `apps/gpu-monitor/deploy/test_release_scripts.sh` and operator details in `apps/gpu-monitor/deploy/README.md`.

Commit SHA: final SHA is produced by committing this report and is returned by the agent response. A Git commit cannot embed its own final hash inside a tracked file without changing that hash.

## Files changed

- `apps/gpu-monitor/deploy/server/gpu-monitor-deploy-command` — forced-command SSH entrypoint with exact closed `SSH_ORIGINAL_COMMAND` grammar, environment key boundary, scrubbed production path, bounded streamed upload, digest verification, and no `eval`.
- `apps/gpu-monitor/deploy/server/activate-release.sh` — environment-specific activation/status/rollback implementation with flocks, defensive extraction, server-reconstructed manifests, immutable release directories, atomic `current`/`previous` pointers, unit restarts, health rollback/recheck, JSONL state, and retention of the latest three successful releases while preserving current/previous.
- `apps/gpu-monitor/deploy/server/health-check.sh` — bounded health checks for dev (`8101`, `5174`) and live (`8001`, `5173`, `8000` bridge only for live).
- `apps/gpu-monitor/deploy/server/install-deployer.sh` — idempotent installer with `--dry-run`/`--prefix`, root requirement for real install, dedicated `gpu-deploy` nologin user, separate dev/live key inputs, dev-only default, no service start/enable, and forced-command environment binding.
- `apps/gpu-monitor/deploy/server/systemd/gpu-monitor-backend@.service` — backend template.
- `apps/gpu-monitor/deploy/server/systemd/gpu-monitor-frontend@.service` — frontend template.
- `apps/gpu-monitor/deploy/server/systemd/gpu-monitor-bridge@.service` — live bridge template.
- `apps/gpu-monitor/deploy/test_release_scripts.sh` — TDD coverage for forced-command grammar/auth, upload size/digest cleanup, env isolation, atomic pointers, rollback, selected units/flocks, archive validation, retention, status, installer, and exact whitespace/newline rejection regression.
- `apps/gpu-monitor/deploy/README.md` — operator notes for server installation, command grammar, upload/activation behavior, ports, rollback, retention, and key boundary.
- `.superpowers/sdd/task-5-report.md` — this report.

## RED evidence

Initial Task 5 RED run after adding server-side tests failed because the required server scripts did not exist:

```text
$ bash apps/gpu-monitor/deploy/test_release_scripts.sh
...
ok - release artifact contract is satisfied
ok - failed build leaves no partial outputs and script works from any CWD
FAIL: server forced-command wrapper is missing or not executable
```

Additional exact-grammar regression RED was recorded during self-review after noticing the wrapper normalized whitespace with `read -a`:

```text
$ bash apps/gpu-monitor/deploy/test_release_scripts.sh
...
ok - failed build leaves no partial outputs and script works from any CWD
ok - server scripts exist
FAIL: unsafe forced command was accepted: status  dev
```

The wrapper was then changed to use anchored exact regex parsing for only the accepted command forms.

## Verification

Final verification was run after the last production-script change.

### `bash apps/gpu-monitor/deploy/test_release_scripts.sh`

Result: pass. Final evidence included:

```text
ok - server scripts exist
ok - forced-command grammar and env authorization reject unsafe requests
ok - uploads are size-bounded, digest-verified, and cleaned on failure
ok - activation isolates envs, uses atomic pointers/units/flocks, and rolls back on failed health
ok - archive validation, retention, status, and installer contract are enforced
```

### `make release-script-test`

Result: pass. Final evidence from `/tmp/gpu-task5-logs/make-release-script-test-final2.log` included:

```text
ok - release artifact contract is satisfied
ok - failed build leaves no partial outputs and script works from any CWD
ok - server scripts exist
ok - forced-command grammar and env authorization reject unsafe requests
ok - uploads are size-bounded, digest-verified, and cleaned on failure
ok - activation isolates envs, uses atomic pointers/units/flocks, and rolls back on failed health
ok - archive validation, retention, status, and installer contract are enforced
```

### `make test`

Result: pass. Final evidence from `/tmp/gpu-task5-logs/make-test-final2.log` included:

```text
Ran 11 tests in 0.009s
OK
Ran 9 tests in 1.267s
OK
Ran 29 tests in 0.666s
OK
Ran 49 tests in 4.710s
OK
OK: workflow policy validated 1 workflow file(s)
Ran 37 tests in 2.101s
OK
Ran 23 tests in 0.001s
OK
ok - archive validation, retention, status, and installer contract are enforced
```

### `git diff --check`

Result: pass; output was empty in `/tmp/gpu-task5-logs/git-diff-check-final2.log`.

## Security and failure-path self-review

- Exact forced-command grammar is closed over only `upload`, `activate`, `status`, and `rollback` forms for `dev|live`; lowercase 40-hex SHA and 64-hex digest are validated by anchored regex before dispatch.
- No `eval` is used. The wrapper accepts an installer-provided `dev|live` argv boundary so a dev key cannot invoke live commands even if `SSH_ORIGINAL_COMMAND` asks for live.
- Uploads stream from stdin through an internal Python verifier into a temp file, enforce a 512 MiB default bound, delete temp files on oversize/digest mismatch, and publish only verified artifacts.
- Activation recomputes artifact digest and reconstructs `release-manifest.json` from validated server-side args plus recomputed digest; no external manifest is trusted.
- Archive validation rejects absolute paths, parent traversal, invalid root prefixes, links, device/FIFO types, setuid/setgid entries, excessive file counts, and excessive expanded size before extraction.
- Releases are built in temp dirs, dependencies are installed before publish, then the release is atomically renamed into `releases/<sha>` and made non-writable. Existing release dirs are not mutated on repeat activation.
- Dev and live roots are separate under `${PREFIX}/srv/gpu-monitor/{dev,live}` or `/srv/gpu-monitor/{dev,live}`; locks are separate under `${PREFIX}/var/lock/gpu-monitor/{dev,live}.lock`.
- Dev activation restarts/checks only dev backend/frontend. Live activation restarts/checks backend/frontend/bridge for live only. Installer starts/enables nothing.
- `current` and `previous` pointers are symlink swaps via `os.replace`. Failed health restores prior pointers, restarts selected units, attempts recheck, and appends rollback JSONL state.
- Retention removes only successful releases outside the latest-three set and never deletes the release targeted by `current` or `previous`.
- Test fakes are exposed only through explicit test variables such as `PREFIX`, `GPU_MONITOR_TEST_PATH`, `GPU_MONITOR_MAX_UPLOAD_BYTES`, and bounded validation knobs; production defaults use a fixed minimal PATH and internal Python.

## Concerns

- None known from local verification. npm reports existing low-severity frontend dependency advisories during release builder tests; this task did not change frontend dependencies.
