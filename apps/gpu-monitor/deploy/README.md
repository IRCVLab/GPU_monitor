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

Task 5 adds the server-side forced command and activation scripts under `apps/gpu-monitor/deploy/server/`. Install them on the target host with a development key, and optionally a distinct live key:

```bash
sudo apps/gpu-monitor/deploy/server/install-deployer.sh \
  --dev-public-key "$(cat /path/to/dev_deploy.pub)" \
  --live-public-key "$(cat /path/to/live_deploy.pub)"
```

The server-side threat model assumes reviewed team repository code is trusted once it is accepted for deployment. Task 5 prevents accidental cross-environment deployment, SSH forced-command argument crossover, and Unix/filesystem/process privilege crossover between deployment state and runtime services. It is not a hostile multi-tenant loopback-isolation project: the installer does not add network namespaces or firewall rules, and the service templates keep normal loopback binding behavior.

For unprivileged validation or packaging, use `--dry-run --prefix <dir>`. `--prefix` is accepted only with `--dry-run`; every real install writes the fixed production paths. Real installs require root and create separate password-locked roles for each environment:

- `gpu-deploy-dev` and `gpu-deploy-live` own deployment state such as uploads, locks, release directories, generation pointers, deployment JSONL, and `authorized_keys`.
- `gpu-monitor-dev` and `gpu-monitor-live` run the systemd services. Runtime users get read-only traversal of immutable releases plus ownership only of their own mutable shared directory under `/var/lib/gpu-monitor/<env>`. Runtime users do not own deployment pointers, locks, state, incoming uploads, sudo configuration, or deployment homes.

The installer lays out `/srv/gpu-monitor/{dev,live}`, `/var/lib/gpu-monitor/{dev,live}`, `/etc/gpu-monitor/{dev,live}.env`, environment-local locks/incoming storage, the restart broker, exact sudoers allowlists validated with `visudo`, and systemd templates without starting or enabling any service. It rejects identical normalized dev/live keys and validates that deploy/runtime accounts have the expected UIDs/GIDs, homes, shells, locked passwords, group memberships, and no cross-environment group access. The installer binds keys to explicit forced-command environments (`gpu-monitor-deploy-command dev` or `gpu-monitor-deploy-command live`), so a development key cannot request live commands. The production activator also verifies the OS caller identity matches the requested environment before accepting an internal activation. Installed key lines never expose the test-only argv mode and reject key inputs that try to prepend `authorized_keys` options. The development key is installed by default; the live key is installed only when `--live-public-key` is supplied.

The installer writes port defaults only when each environment file is first created:

```text
# /etc/gpu-monitor/dev.env
GPU_MONITOR_BACKEND_PORT=8101
PORT=5174

# /etc/gpu-monitor/live.env
GPU_MONITOR_BACKEND_PORT=8001
GPU_MONITOR_BRIDGE_PORT=8000
PORT=5173
```

Before enabling units, operators must add the application secrets and runtime settings required by the backend (including `SECRET_KEY`, `ADMIN_PASSWORD`, and a server-local writable `DATABASE_URL`) to the selected environment file. The backend and bridge templates run `backend.main:app` and `backend.slack_bridge:app` through uvicorn. The frontend template runs the packaged Svelte adapter-node entrypoint directly with Node and binds it to loopback.

The forced command accepts only these `SSH_ORIGINAL_COMMAND` forms:

```text
upload dev <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
upload live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate dev <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
status dev
status live
rollback dev
rollback live
```

Uploads stream from SSH stdin into environment-local incoming storage, are bounded to 512 MiB by default, and are kept only after SHA-256 verification. Activation opens the uploaded artifact through the incoming directory file descriptor with `O_NOFOLLOW`/`O_CLOEXEC`, rejects symlinks/FIFOs/non-regular files, validates archive structure defensively from the same opened descriptor used for hashing, reconstructs the release manifest from the validated command arguments plus recomputed digest, extracts into a temporary release, installs locked runtime dependencies there with bounded time/cache behavior, checks expanded size/free-space limits, applies final owner/group/modes, fsyncs every regular file and directory bottom-up, atomically publishes an immutable `releases/<sha>` directory, and only then performs the pointer swap.

`current` and `previous` remain visible at the environment root for services and operators, but they resolve through a single atomically-swapped generation symlink so activation and manual rollback snapshot both pointers and publish them as one generation. Status, upload, activation, and rollback all take the environment flock. If restart, health, pointer, filesystem, or fsync recovery fails, the JSONL state records truthful `rollback_succeeded` or `rollback_failed` information instead of suppressing recovery errors.

Development activation restarts only backend/frontend units and checks ports `8101` and `5174`. Live activation restarts backend/frontend/bridge units and checks ports `8001`, `5173`, and `8000`; Task 5 installs templates only and does not start or replace live services during installation. Each environment uses a separate flock file, content-addressed incoming uploads with aggregate quota/cleanup, and durable JSONL deployment state; rolls back and rechecks on restart or health failure including manual rollback; removes a newly constructed inactive candidate when activation fails after publishing it; and retains the latest three successful activations without deleting the release targeted by `current` or `previous`.

Production forced-command execution ignores inherited override variables, uses fixed `/srv/gpu-monitor` roots, a fixed minimal command path, `/usr/bin/python3`, and the 512 MiB upload ceiling. The `--test-mode <dev|live>` argv form is reserved for the unprivileged regression harness; it is never written to `authorized_keys`, and internal activation/health scripts accept overrides only when that mode is propagated explicitly.
