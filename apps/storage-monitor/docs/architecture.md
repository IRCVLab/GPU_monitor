# storage-viz architecture

`storage-viz` is a single-server storage visibility tool with a C scanner, a
JSON snapshot contract, and an offline browser dashboard. The architecture keeps
the data plane simple: scan locally, write static JSON, then let any HTTP server
serve the viewer and data files.

## System boundaries

| Boundary | Files | Responsibility | Extension rule |
|---|---|---|---|
| Scanner | `scanner/hstscan.c`, `scanner/Makefile` | Walk selected Linux filesystem targets, aggregate owner/mount/tree statistics, and write one schema-v1 JSON snapshot. | Preserve scanner correctness and performance; add CLI/config flags before hardcoding site assumptions. |
| Data fixtures | `data/hosts.json`, `data/*.sample.json`, `data/gen_sample.py` | Provide trackable host metadata and deterministic sample data for viewer development and smoke tests. | Generated production snapshots stay ignored; manifests and sample fixtures stay tracked. |
| Viewer | `viewer/index.html`, `viewer/echarts.min.js`, future `viewer/*.js` modules | Render the snapshot offline: treemap, users, top files, stale candidates, and cleanup-assist UI. | Browser code must remain local/offline and tolerate additive schema-v1 fields. |
| Runtime server | `viewer/serve.py`, `install.sh` | Serve static assets and, when explicitly configured, expose rescan status/control. | The UI must reflect actual runtime capabilities; static mode must not pretend rescan is available. |
| Operations docs | `docs/operations.md` | Deployment, systemd, logs, rescan, and operator runbooks. | Runtime specifics belong there rather than in code comments or viewer assumptions. |
| Schema docs | `docs/schema-v1.md` | Snapshot field contract and compatibility notes. | Schema-v1 is additive-compatible: old viewers ignore unknown fields. |

## Data flow

```text
scan targets
  └─ scanner/hstscan
       └─ data/<hostname>.json        # generated, ignored by git
            ├─ viewer/data symlink
            └─ viewer/index.html      # fetches host snapshot(s)

data/hosts.json                       # tracked manifest for host dropdowns
data/<hostname>.sample.json           # tracked dev/test fixtures
```

The scanner owns measurement. The viewer owns presentation. The runtime layer
owns how often snapshots are refreshed. Keeping these boundaries separate makes
another server a data/config change rather than a source edit.

## Host manifest contract

`data/hosts.json` is the viewer-facing inventory of available snapshots. Each
entry uses:

- `id`: stable machine-readable id (`hinton`, `lab-gpu-01`, etc.).
- `label`: human-readable dropdown label.
- `file`: basename without `.json`; the viewer can request
  `data/<file>.json` and, in development, `data/<file>.sample.json`.
- `default`: optional boolean; exactly one default is recommended.
- `description`: optional operator note.

Adding a host should mean adding a generated snapshot plus a manifest entry, not
editing JavaScript.

## Generated vs tracked data

Real scans can contain private paths and user activity, so `data/*.json` remains
ignored by default. The repository intentionally unignores:

- `data/hosts.json` for host metadata.
- `data/*.sample.json` for synthetic or scrubbed fixtures.

Use `data/gen_sample.py` to regenerate the deterministic sample fixture from the
repository worktree:

```bash
python3 data/gen_sample.py
```

The generator writes `data/hinton.sample.json` next to itself by default and can
also accept an explicit output path for temporary checks:

```bash
python3 data/gen_sample.py /tmp/hinton.sample.json
```

## Compatibility principles

- The scanner emits byte counts, not human-formatted strings.
- Tree node `bytes` should remain equal to `sum(children[].bytes) +
  other_bytes` when children are present.
- Unknown additive JSON fields should not break viewers.
- The viewer may hide labels for tiny treemap tiles, but tile area should remain
  proportional to bytes at the same comparison level.
- Cleanup-assist UI may generate shell-escaped commands for humans to review,
  but the browser must not execute deletion or add a delete endpoint.

## Future split points

The current MVP keeps several responsibilities in large files. When tests are in
place, split along stable boundaries rather than broad rewrites:

1. `viewer/data-client.js` for host manifest and snapshot loading.
2. `viewer/treemap.js` for byte-proportional layout.
3. `viewer/tables.js` and `viewer/selection.js` for large-table rendering and
   cleanup command generation.
4. Scanner helpers only after correctness tests protect JSON escaping, CLI
   output paths, mount walking, and hardlink behavior.
