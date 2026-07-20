# storage-viz JSON schema v1

Scanner output is a single JSON object written atomically to
`<data-dir>/<hostname>.json`. Sizes are byte counts based on filesystem blocks.
Schema v1 is additive: viewers should continue to accept existing v1 fields and
ignore unknown fields, while newer producers include stable multiserver and mount
identity fields for deterministic merging.

## Top-level fields

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | number | Stable major schema version. Current value is `1`. |
| `hostname` | string | Host that produced the scan; retained for display/backward compatibility. |
| `server_id` | string | Stable privacy-safe server key used by multiserver manifests. For the sample, `hinton`. |
| `scanner_version` | string | Scanner binary version. |
| `scan_started_unix` | number | Scan start time as Unix seconds. |
| `scan_finished_unix` | number | Scan finish time as Unix seconds. |
| `scan_duration_sec` | number | Wall-clock scan duration in whole seconds; `scan_finished_unix == scan_started_unix + scan_duration_sec`. |
| `scan_generation` | string | Deterministic generation id for a snapshot, e.g. `<server_id>-<scan_started_unix>-v1`. |
| `run_as_root` | boolean | Whether the scanner process had effective UID 0. |
| `selected_roots` | array | One entry per requested scan root, including mount identity and bounded scan counters. |
| `mounts` | array | One entry per scanned target that existed. Each entry references `mount_id` and `scan_root`. |
| `users` | array | Per-UID ownership totals. |
| `top_files` | array | Largest rows retained by the scanner. |
| `stale` | array | Old large rows retained as cleanup candidates. |
| `blocked` | array | Paths the scanner could not read, with reasons. |

## Selected root entry

Each `selected_roots[]` entry identifies a requested root and the mount that was
scanned. Values must be deterministic for the same filesystem snapshot and must
not leak machine-local private paths beyond the displayed scan roots.

| Field | Type | Description |
| --- | --- | --- |
| `mount_id` | string | Stable id unique within this server snapshot, e.g. `data1`. |
| `major_minor` | string | Device major/minor string such as `8:32`. |
| `mount_source` | string | Privacy-safe mount source label. The sample uses `/dev/storage-viz/...` synthetic labels. |
| `mount_root` | string | Root inside the mounted filesystem, usually `/`. |
| `mountpoint` | string | Absolute mount point visible on the server. |
| `scan_root` | string | Absolute root requested/scanned by storage-viz. |
| `fstype` | string | Filesystem type. |
| `status` | string | `complete`, `partial`, `failed`, or `skipped`. `complete` and `partial` roots produce tree-bearing `mounts[]`; `failed` and `skipped` roots do not. |
| `scanned_bytes` | number | Non-negative byte count under `scan_root`; `0` for `failed`/`skipped`. |
| `scanned_files` | number | Non-negative file count under `scan_root`; `0` for `failed`/`skipped`. |
| `scanned_dirs` | number | Non-negative directory count under `scan_root`; `0` for `failed`/`skipped`. |
| `blocked_count` | number | Non-negative bounded count of blocked paths under this selected root. |
| `error_count` | number | Non-negative bounded count of scanner errors for this selected root. |
| `error_code` | string or null | Representative scanner error code, or `null` when there is no root-level error code. |

Optional storage identity/media fields may appear on any selected root. They are
additive v1 fields; absence means an older producer did not publish media
metadata.

| Field | Type | Description |
| --- | --- | --- |
| `capacity_id` | string | Optional stable capacity identity, format `dev-<major>-<minor>`, matching `^dev-(0|[1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})$`, max 31 characters, with `dev-0-0` reserved/invalid. This is derived from major/minor only and does not expose `/dev`, sysfs, or GPU paths. |
| `storage_media` | string | Optional media class: exactly `ssd`, `hdd`, `mixed`, or `unknown`. |
| `storage_media_confidence` | string | Optional confidence: exactly `resolved` or `unresolved`. `unknown` pairs with `unresolved`; `ssd`, `hdd`, and `mixed` pair with `resolved`. |

## Mount entry

Each `mounts[]` entry contains:

- `path`: backward-compatible display path, normally equal to `scan_root`
- `mount_id`: references a `selected_roots[].mount_id` whose status is `complete` or `partial`
- `scan_root`: references the same selected root's `scan_root`
- `fstype`
- `df_total`, `df_used`, `df_avail`, `df_use_pct`
- `scanned_bytes`, `scanned_files`, `scanned_dirs`, `errors`
- optional `capacity_id`, `storage_media`, and `storage_media_confidence`
- `tree`: recursive node used by the treemap

For `complete` and `partial` selected roots, the linked `mounts[]` entry and
the `selected_roots[]` entry must either both omit the optional storage
identity/media fields or contain exactly the same values. `failed` and `skipped`
roots do not have linked mount entries; if they include optional media fields,
those fields validate independently under the same enum, pairing, length, and
capacity-id rules.

A tree node contains `name`, `kind`, `bytes`, `files`, `uid`, `mtime`, optional
`children`, and optional `other_bytes` for children pruned below the configured
threshold. `kind` is one of `directory`, `file`, `symlink`, or `other`; current
synthetic tree fixtures use `directory` nodes. Treemap area should use `bytes`;
`other_bytes` preserves exact totals when small entries are collapsed. For every
node with children, `bytes == sum(child.bytes) + other_bytes`.

## File rows

`top_files[]` and `stale[]` rows contain `path`, `kind`, `bytes`, `uid`, `owner`,
and `mtime`; `stale[]` rows also include `age_days`. `kind` is one of
`directory`, `file`, `symlink`, or `other`; current synthetic top/stale rows use
`file`. Consumers should treat paths as display/copy text only; storage-viz does
not execute deletion commands.

## Synthetic fixture stability

`data/gen_sample.py` writes four deterministic, privacy-safe multiserver
fixtures in manifest order: `hinton`, `atlas`, `orion`, and `zeus`.

All generated fixtures use:

- `scan_duration_sec`: `42`
- deterministic `scan_started_unix` / `scan_finished_unix` values
- `scan_generation` formatted as `<server_id>-<scan_started_unix>-v1`
- synthetic mount sources under `/dev/storage-viz/...`

For example, the `hinton` fixture includes:

- `server_id`: `hinton`
- `scan_started_unix`: `1719200000`
- `scan_finished_unix`: `1719200042`
- `scan_generation`: `hinton-1719200000-v1`
- four tree-producing selected roots: `/home`, `/data`, `/data1`, and `/data3`
- the root filesystem entry has `mountpoint` `/` and `scan_root`/mount `path` `/home`

Do not hand-edit generated sample JSON. Update `data/gen_sample.py`, run it, and
then run `data/test_fixtures.py` so the tracked fixture and schema invariants stay
in sync.

## Compatibility rules

- Viewers should reject unknown major schema versions, but tolerate additional
  fields.
- Missing optional arrays should be treated as empty arrays.
- New v1 fields are additive; older v1 viewers may ignore `server_id`,
  `scan_finished_unix`, `scan_generation`, `selected_roots`, `mount_id`,
  `scan_root`, `kind`, `blocked_count`, `error_count`, `error_code`,
  `capacity_id`, `storage_media`, and `storage_media_confidence`.
- Older v1 snapshots without storage identity/media fields remain valid. When
  present, the fields contain only bounded enums and major/minor-derived
  identity; they must not expose kernel device paths, sysfs paths, GPU paths, or
  other host-local pathnames.
- Every `mounts[]` entry must reference a selected root, but `failed` and
  `skipped` selected roots are valid without mount entries.
- A non-root scan is valid but may undercount unreadable directories; show
  `blocked[]` and `run_as_root=false` clearly.
