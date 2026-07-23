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

## Task 5 handoff note

The manifest is emitted beside the tarball rather than embedded inside it. Embedding the final artifact SHA-256 inside the tarball would create a checksum cycle, so server-side upload/activation should transport and verify the external `release-manifest.json` with the tarball and checksum file, or define a separate non-circular in-artifact provenance file in Task 5.
