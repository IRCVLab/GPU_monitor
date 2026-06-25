# storage-viz JSON schema v1

Scanner output is a single JSON object written atomically to
`<data-dir>/<hostname>.json`. Sizes are byte counts based on filesystem blocks.

## Top-level fields

| Field | Type | Description |
| --- | --- | --- |
| `schema_version` | number | Current value is `1`. |
| `hostname` | string | Host that produced the scan. |
| `scanner_version` | string | Scanner binary version. |
| `scan_started_unix` | number | Scan start time as Unix seconds. |
| `scan_duration_sec` | number | Wall-clock scan duration. |
| `run_as_root` | boolean | Whether the scanner process had effective UID 0. |
| `mounts` | array | One entry per scanned target that existed. |
| `users` | array | Per-UID ownership totals. |
| `top_files` | array | Largest files retained by the scanner. |
| `stale` | array | Old large files retained as cleanup candidates. |
| `blocked` | array | Paths the scanner could not read, with reasons. |

## Mount entry

Each `mounts[]` entry contains:

- `path`, `fstype`
- `df_total`, `df_used`, `df_avail`, `df_use_pct`
- `scanned_bytes`, `scanned_files`, `scanned_dirs`, `errors`
- `tree`: recursive directory node used by the treemap

A tree node contains `name`, `bytes`, `files`, `uid`, `mtime`, optional
`children`, and optional `other_bytes` for children pruned below the configured
threshold. Treemap area should use `bytes`; `other_bytes` preserves exact totals
when small entries are collapsed.

## File rows

`top_files[]` and `stale[]` rows contain `path`, `bytes`, `uid`, `owner`, and
`mtime`. Consumers should treat paths as display/copy text only; storage-viz does
not execute deletion commands.

## Compatibility rules

- Viewers should reject unknown major schema versions, but tolerate additional
  fields.
- Missing optional arrays should be treated as empty arrays.
- A non-root scan is valid but may undercount unreadable directories; show
  `blocked[]` and `run_as_root=false` clearly.
