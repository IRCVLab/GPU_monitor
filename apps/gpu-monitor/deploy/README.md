# GPU monitor release artifacts

`build-release.sh` creates immutable release artifacts for the GPU monitor app from a clean checkout at an explicit HEAD commit SHA. Artifact bytes are derived from the committed HEAD tree exported to a temporary source root; frontend install/check/build commands run only in that temporary source root and do not mutate checkout `node_modules` or `build` directories.

```bash
apps/gpu-monitor/deploy/build-release.sh \
  --sha "$(git rev-parse HEAD)" \
  --output-dir /tmp/gpu-release-output
```

Outputs:

- `gpu-monitor-<sha>.tar.gz` — normalized gzip/tar payload containing only runtime-allowed backend source, locked backend requirements, frontend package lock files, and a fresh frontend build.
- `gpu-monitor-<sha>.sha256` — checksum file for the tarball. It records the absolute artifact path so the brief-required `sha256sum -c /tmp/.../gpu-monitor-*.sha256` command works from any current directory; moving artifacts requires regenerating or editing the checksum file path.
- `release-manifest.json` — provenance manifest with application name, exact Git SHA, artifact filename, SHA-256 digest, and schema version. The filename is intentionally fixed by the Task 4 interface, so a single output directory should hold only one active manifest at a time when building multiple SHAs.

The builder fails closed when the requested SHA is not a 40-character lowercase hex HEAD SHA, tracked source is dirty, nonignored untracked files exist, required tracked runtime inputs are missing from the committed HEAD tree, frontend dependency/check/build commands fail, symlink or path-escape inputs are encountered, partial outputs would be produced, or excluded runtime/generated/secret content would enter the staged release.

Runtime secrets and data (`.env`, databases, virtualenvs, `node_modules`, caches, and build leftovers) remain server-local and are not copied into the immutable release artifact.

## Server manifest trust boundary

The build manifest remains an operator/CI artifact and is not trusted by the deployment server. Upload sends only the tarball bytes plus the exact Git SHA and SHA-256 digest in the closed forced-command grammar. After validating the arguments and recomputing the uploaded digest, the server reconstructs the deterministic manifest inside the immutable release directory.

## Server-side forced deployment boundary

Task 5 adds the server-side forced command and activation scripts under `apps/gpu-monitor/deploy/server/`. Install them on the target host in live-only mode with a distinct live key, explicitly retiring development SSH authorization while preserving development release files, state, and configuration:

```bash
sudo apps/gpu-monitor/deploy/server/install-deployer.sh \
  --retire-dev \
  --live-public-key "$(cat /path/to/live_deploy.pub)" \
  --node-prefix /path/to/clean/node-prefix
```

`--node-prefix` must name a canonical, self-contained Node prefix with an executable
`bin/node` at version 18.13.0 or newer and
`lib/node_modules/npm/bin/npm-cli.js`. The installer copies that prefix into a
versioned, root-owned directory under `/opt/gpu-monitor/node-runtimes/` and
atomically points `/opt/gpu-monitor/node` at it. Runtime services and activation
therefore use one coherent Node/npm pair without depending on an operator home,
NVM shell initialization, or the host distribution's `/usr/bin/node`. A real
first install requires `--node-prefix`; later idempotent reinstalls may omit it
while the managed runtime exists. The installer does not replace `/usr/bin/node`
or start any service.

When an NVM installation contains unrelated global packages, construct a clean
prefix containing only `bin/node`, the relative `bin/npm` launcher, and
`lib/node_modules/npm` before passing it to the installer. This avoids copying
operator tools into the service runtime.

The server-side threat model assumes reviewed team repository code is trusted once it is accepted for deployment. Task 5 prevents accidental cross-environment deployment, SSH forced-command argument crossover, and Unix/filesystem/process privilege crossover between deployment state and runtime services. It is not a hostile multi-tenant loopback-isolation project: the installer does not add network namespaces or firewall rules, and the service templates keep normal loopback binding behavior.

For unprivileged validation or packaging, use `--dry-run --prefix <dir>`. `--prefix` is accepted only with `--dry-run`; every real install writes the fixed production paths. Real installs require root and create separate password-locked roles for each environment:

- `gpu-deploy-dev` and `gpu-deploy-live` own deployment state such as uploads, locks, release directories, generation pointers, deployment JSONL, and `authorized_keys`.
- `gpu-monitor-dev` and `gpu-monitor-live` run the systemd services. The environment root, `releases`, and `generations` grant the matching runtime group read/traverse access only; `incoming`, the contents of the setgid `tmp` staging root, locks, and JSONL state remain deploy-owned and inaccessible to runtime. The staging root carries the runtime GID only so construction preserves the eventual published group deliberately; its `2700` mode grants the runtime group no access. Runtime users own only their mutable shared directory under `/var/lib/gpu-monitor/<env>`.

The installer lays out `/srv/gpu-monitor/{dev,live}`, `/var/lib/gpu-monitor/{dev,live}`, `/etc/gpu-monitor/{dev,live}.env`, environment-local locks/incoming storage, the restart broker, exact sudoers allowlists validated with `visudo`, and systemd templates without starting or enabling any service. Reinstallation keeps only the writable traversal parents setgid/writable, reconciles published release and generation descendants to read-only modes, and keeps any legacy hidden `.release-*` candidate private rather than exposing it to runtime. It normalizes public keys to key type plus base64 blob before rejecting identical dev/live material, so comments cannot disguise key reuse. When a live key argument is omitted during rotation, the installer also normalizes the already-installed live authorization and rejects a proposed development key with the same material before either authorization is changed. Live-only reconciliation is explicit: `--retire-dev` must be combined with `--live-public-key`, cannot be combined with `--dev-public-key`, removes only `/home/gpu-deploy-dev/.ssh/authorized_keys`, and preserves development release files, state, and configuration. Supplying `--live-public-key` by itself is rejected so development revocation cannot happen by accidental omission. It validates that deploy/runtime accounts have the expected UIDs/GIDs, homes, shells, locked passwords, group memberships, and no cross-environment group access. The installer binds keys to explicit forced-command environments (`gpu-monitor-deploy-command dev` or `gpu-monitor-deploy-command live`), so a development key cannot request live commands. The production activator requires the effective username to be exactly `gpu-deploy-dev` or `gpu-deploy-live` for the selected environment; root has no bypass and must use the matching deploy identity. Installed key lines never expose the test-only argv mode and reject key inputs that try to prepend `authorized_keys` options.

On every install, the installer atomically rewrites and deduplicates only the exact reserved port keys below while preserving comments, unrelated settings, and secrets byte-for-byte:

```text
# /etc/gpu-monitor/dev.env
GPU_MONITOR_BACKEND_PORT=8101
PORT=5174

# /etc/gpu-monitor/live.env
GPU_MONITOR_BACKEND_PORT=8001
GPU_MONITOR_BRIDGE_PORT=8000
PORT=5173
```

Before enabling units, operators must add the application secrets and runtime settings required by the backend (including `SECRET_KEY`, `ADMIN_PASSWORD`, and a server-local writable `DATABASE_URL`) to the selected environment file. The backend and bridge templates run `backend.main:app` and `backend.slack_bridge:app` through uvicorn. The frontend template runs the packaged `frontend/server.mjs` with the managed Node runtime and binds it to loopback. That server delegates normal page and asset requests to the generated adapter-node handler, strips only the exact `/api` prefix while proxying HTTP requests to the environment-local backend, and preserves `/ws` paths while tunnelling WebSocket upgrades. The proxy target defaults to `127.0.0.1:$GPU_MONITOR_BACKEND_PORT` and rejects non-loopback targets. Activation health checks exercise the browser-facing `/api/health` and `/ws/metrics` paths through the frontend port in addition to checking each backend directly, so a missing production proxy cannot pass release activation.

Current live operators use only these `SSH_ORIGINAL_COMMAND` forms:

```text
upload live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
status live
rollback live
```

Historical compatibility note: development forced-command forms still exist for preserved development state and older controlled environments, but they are not current live-only operator commands:

```text
upload dev <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate dev <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
status dev
rollback dev
```

Uploads stream from SSH stdin into environment-local incoming storage, are bounded to 512 MiB by default, and are kept only after SHA-256 verification. Activation opens the uploaded artifact through the incoming directory file descriptor with `O_NOFOLLOW`/`O_CLOEXEC`, rejects symlinks/FIFOs/non-regular files, validates archive structure defensively from the same opened descriptor used for hashing, reconstructs the release manifest from the validated command arguments plus recomputed digest, and extracts into a private `tmp/release-*` candidate that runtime cannot traverse. Locked runtime dependency construction is bounded and fail-closed: production requires trusted absolute timeout, Python, pip-through-the-created-venv, and the installer-managed Node/npm runtime; every timeout, venv, pip, npm, or cleanup failure rejects the candidate without publication. Activation then checks expanded size/free-space limits, verifies every candidate inode has the expected runtime GID, applies and verifies read-only published modes, fsyncs every regular file and directory bottom-up, atomically publishes `releases/<sha>`, fsyncs both staging and release parents, and only then performs the pointer swap.

`current` and `previous` remain visible at the environment root for services and operators, but they resolve through a single atomically-swapped generation symlink so activation and manual rollback snapshot both pointers and publish them as one generation. Status, upload, activation, and rollback all take the environment flock. If restart, health, pointer, filesystem, or fsync recovery fails, the JSONL state records truthful `rollback_succeeded` or `rollback_failed` information instead of suppressing recovery errors. When the first activation fails and no prior `current` release exists, recovery removes the failed generation pointers, stops only that environment's managed units through the exact broker allowlist, skips meaningless health checks against an intentionally absent deployment, and records `rollback_succeeded` with `restored_absent`.

Development activation restarts only backend/frontend units and checks ports `8101` and `5174`. Live activation restarts backend/frontend/bridge units and checks ports `8001`, `5173`, and `8000`; Task 5 installs templates only and does not start or replace live services during installation. Each environment uses a separate flock file, content-addressed incoming uploads with aggregate quota/cleanup, and durable JSONL deployment state; rolls back and rechecks on restart or health failure including manual rollback; removes a newly constructed inactive candidate only after durable recovery succeeds and explicit effective `current`/`previous` checks prove it unreferenced; preserves the candidate when recovery cannot establish that fact; and retains the latest three successful activations without deleting the release targeted by `current` or `previous`.

Production forced-command execution ignores inherited override variables, uses fixed `/srv/gpu-monitor` roots, a fixed minimal command path, `/usr/bin/python3`, and the 512 MiB upload ceiling. The `--test-mode <dev|live>` argv form is reserved for the unprivileged regression harness; it is never written to `authorized_keys`, and internal activation/health scripts accept overrides only when that mode is propagated explicitly.
