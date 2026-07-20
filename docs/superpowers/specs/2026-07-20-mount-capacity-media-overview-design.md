# Mount-Centric Capacity and Media Overview

**Date:** 2026-07-20  
**Status:** Approved for implementation planning  
**Scope:** Storage Dashboard only. GPU Monitor code, processes, ports, and deployment paths remain untouched.

## 1. Problem

The development dashboard currently shows only `hinton` because development mode discovers `data/*.sample.json` and the repository contains only `data/hinton.sample.json`. The overview also separates utilization percentages from byte capacities too strongly, does not show an aggregate managed capacity, and cannot distinguish SSD-backed storage from HDD-backed storage.

The user selected the visual direction **“Option A, mount-centric.”** The page remains a compact, stable-order server list, but each mount becomes the primary capacity decision surface. Server names organize the list rather than competing with mount capacity information.

## 2. Goals

1. Show multiple deterministic development servers without implying that sample data is live production data.
2. Show aggregate managed capacity, used bytes, available bytes, and utilization on one compact baseline.
3. For every server, keep used bytes, total bytes, available bytes, and utilization visually adjacent.
4. Make each mount the primary visual unit and show its capacity bar immediately below its numbers.
5. Classify each mount as `ssd`, `hdd`, `mixed`, or `unknown` without executing heavyweight discovery commands.
6. Avoid double-counting duplicate views of the same local filesystem.
7. Preserve inventory order exactly.

## 3. Non-goals

- No physical disk health, SMART telemetry, RAID health management, or predictive failure alerts.
- No attempt to label an unresolved device as SSD or HDD.
- No network filesystem capacity; existing network-mount exclusions remain mandatory.
- No storage cleanup execution from the browser.
- No GPU Monitor integration or shared runtime.
- No claim that filesystem capacity equals raw physical disk capacity.

## 4. Information Architecture

### 4.1 Page aggregate

The overview begins with one restrained aggregate line:

- **Managed local storage:** sum of unique included local filesystem capacities;
- **Used:** corresponding unique-filesystem used bytes;
- **Available:** corresponding unique-filesystem available bytes;
- **Utilization:** aggregate used divided by aggregate total.

The heading uses “managed local storage,” not “physical storage,” because filesystem accounting may exclude unformatted space, parity, reserved blocks, thin-provisioned backing capacity, or storage hidden behind a volume manager.

### 4.2 Server grouping

Each server remains in inventory order. Its header contains only:

- display name;
- `used / total`;
- available bytes.

The header has no competing aggregate progress bar. Its purpose is to group mounts and provide a quick server subtotal.

### 4.3 Mount as the primary unit

Each mount cell contains, in reading order:

1. mount path;
2. media label (`SSD`, `HDD`, `Mixed`, or `Unknown`);
3. `used / total` and utilization on the same line;
4. utilization bar directly beneath the numbers;
5. available bytes in a quiet footer.

Pressure color is reserved for warning and critical capacity states. SSD/HDD labels use low-emphasis neutral or informational surfaces and do not compete with capacity warnings.

Desktop uses up to three mount cells per server row. Narrow layouts collapse to one column without horizontal scrolling.

## 5. Capacity Accounting

### 5.1 Mount values

Use the existing scanner fields:

- `df_total`;
- `df_used`;
- `df_avail`;
- `df_use_pct`.

No UI value is derived from scanned tree bytes because tree bytes represent content visible under the configured scan root, while `df_*` values represent filesystem capacity.

### 5.2 Unique capacity identity

The producer adds an optional additive `capacity_id` to selected roots and linked mount entries. Its schema is exact:

- encoding: `dev-<major>-<minor>`;
- regex: `^dev-(0|[1-9][0-9]{0,9})-(0|[1-9][0-9]{0,9})$`;
- maximum length: 31 ASCII characters;
- `dev-0-0` is not valid;
- unresolved identities omit the field rather than emitting `null`, an empty string, a path, UUID, or device name;
- a complete/partial root and its linked mount either both omit the field or contain the exact same value.

For ordinary block-backed filesystems the id is derived from the stable mountinfo major/minor identity. Multiple bind mounts, alternate paths, or subvolume views resolving to the same capacity identity count once in server and page aggregates while remaining individually visible if policy selected them. Existing v1 snapshots may derive the same in-memory id from a non-zero selected-root `major_minor` after linking by `mount_id`; the stored snapshot is not rewritten.

If a safe stable identity cannot be established, the mount remains visible but is excluded from the exact aggregate. The UI then marks the aggregate as partial rather than guessing or silently double-counting.

All arithmetic uses non-negative integer byte values. The collector validates the encoding and linked selected-root/mount equality. The field is additive within schema v1; old snapshots remain valid. An old snapshot can still contribute exactly when a non-zero major/minor identity can be derived through its selected-root link; otherwise it contributes only to the visible mount list and makes the aggregate partial.

### 5.3 Aggregate model and partial display

Both page and server totals use the same model:

```text
known_total_bytes
known_used_bytes
known_available_bytes
known_utilization_pct
excluded_mount_count
partial_reasons[]
is_partial
```

Exact aggregates show “Managed local storage 222 TB,” “128 TB used,” “94 TB available,” and “58% utilization.” Partial aggregates show **“Confirmed capacity ≥ 222 TB”**, retain the known used/available values with the same `≥` qualifier, label utilization **“58% within confirmed storage,”** and append **“2 mounts excluded”**. The same wording applies to a partial server subtotal. If no capacity identity can be proven, totals and utilization show `—` while mount-level values remain visible.

## 6. Media Classification

### 6.1 Data model

The producer adds additive fields to each selected root and linked mount:

```json
{
  "storage_media": "ssd",
  "storage_media_confidence": "resolved"
}
```

Allowed media values are `ssd`, `hdd`, `mixed`, and `unknown`. Confidence is `resolved` or `unresolved`.

### 6.2 Resolution algorithm

For an eligible local mount, resolve `/sys/dev/block/<major>:<minor>` and recursively traverse its `slaves` relationships until leaf block devices are reached. Partition nodes require an explicit parent-device step: when the resolved node contains partition metadata and has no local `queue/rotational`, ascend within `/sys/class/block` to its parent whole-disk node before reading rotational state or traversing slaves. The resolver never ascends outside the block-device sysfs tree. Read each resolved whole/leaf device’s `queue/rotational` value:

- every resolved leaf is `0` → `ssd`;
- every resolved leaf is `1` → `hdd`;
- resolved leaves contain both `0` and `1` → `mixed`;
- missing sysfs nodes, unreadable values, cycles, unsupported virtual storage, or no leaves → `unknown`.

Device-mapper, LVM, and mdraid are therefore classified from their backing leaves. NVMe devices naturally classify as SSD. ZFS or other stacks that do not expose a trustworthy block-device chain remain Unknown.

The resolver caps recursion depth and visited nodes, rejects cycles, and performs cached sysfs reads once per unique major/minor device during a six-hour scan. It does not run `lsblk`, `smartctl`, `udevadm`, or per-file commands and does not alter the scanner’s filesystem traversal cost materially.

## 7. Development Data

Development sample mode gains four deterministic, privacy-safe fixtures in stable order:

1. `hinton`;
2. `atlas`;
3. `orion`;
4. `zeus`.

`data/hosts.json` is the authoritative sample/static manifest and lists these four ids in this order. Development API mode reads only ids present in that manifest and preserves manifest order instead of filename order. Missing manifest files are reported rather than silently reordering the remaining fixtures.

Fixtures cover SSD-only, HDD-only, mixed, unknown, healthy, warning, and critical capacities. The `/api/servers` envelope adds `data_mode: "sample"` for `STORAGE_VIZ_DEV_SAMPLE_DIR` and `data_mode: "inventory"` for production inventory mode. Static fallback identifies the same state from the tracked sample manifest. The frontend renders a visible “sample data” marker only when this explicit signal is `sample`; it never guesses from hostnames or file suffixes. Production inventory mode never reads sample files.

The existing `hinton` fixture remains generated rather than hand-edited. The generator owns all sample JSON files so tests can reproduce them exactly.

## 8. Data Flow

1. Mount policy selects only eligible local filesystems.
2. The scan runner resolves `capacity_id` and media classification once per selected mount device.
3. The scanner snapshot stores additive identity/media fields alongside existing capacity fields.
4. Collector validation accepts old v1 snapshots and strictly validates new fields when present.
5. The API returns snapshots unchanged.
6. The overview model groups mounts by server, deduplicates aggregate capacity only when `capacity_id` proves equivalence, and preserves inventory order.
7. The renderer displays page aggregate, server subtotal, and mount cells without resorting data.

## 9. Error and Degraded States

- Missing snapshot: retain the server row and existing operational status; show no capacity values.
- Old snapshot without `capacity_id`: derive an in-memory id only from a linked non-zero selected-root major/minor; otherwise show the mount and mark the page/server aggregate partial.
- Unknown media: show `Unknown`; never infer from filesystem type, device name, or capacity.
- Partial scan: keep valid mount capacity visible and retain the existing partial-scan warning.
- Failed media lookup: does not fail the storage scan.
- Duplicate capacity identity with inconsistent `df_*` values: exclude that identity from the exact aggregate and surface an internal partial-data state; do not choose one value arbitrarily.

## 10. Motion and Interaction

Capacity values remain stable in position. On refresh, progress-bar width may transition using the existing reduced-motion policy; labels update without layout-shifting animation. Server and mount order never changes. Clicking anywhere in a server group continues to open the existing detail route unless the target is a future mount-specific control.

## 11. Testing

### Agent tests

- SSD, HDD, mixed, and unknown sysfs fixtures;
- ext4/xfs mounts backed by ordinary partition nodes whose rotational state exists only on the parent whole disk;
- device-mapper and mdraid slave recursion;
- cycles, missing nodes, unreadable rotational files, and bounded traversal;
- cache behavior proving one resolution per unique device;
- no external command execution.

### Schema and collector tests

- optional additive fields preserve old v1 compatibility;
- exact `capacity_id` regex, length, absent/null behavior, and non-zero identity validation;
- allowed media/confidence enum validation;
- linked root/mount identity and media consistency;
- invalid values and overlong strings rejected.

### Overview model tests

- exact total/used/available calculation;
- duplicate `capacity_id` counted once;
- inconsistent duplicates produce partial aggregate;
- old snapshots produce partial aggregate without losing mounts;
- known-only aggregate fields, `≥` copy, excluded count, partial reasons, and confirmed-range utilization;
- inventory/server/mount order remains stable;
- SSD/HDD/Mixed/Unknown labels map correctly.

### Browser tests

- desktop and narrow responsive layouts;
- no horizontal overflow;
- capacity numbers remain adjacent to percentages;
- warning/critical status is not communicated by color alone;
- `data_mode` API/static signal and sample-data marker visible only in development sample mode;
- detail navigation remains functional;
- reduced motion respected.

### Regression gates

Run all existing Python, Node, deployment, Linux scanner, and Playwright checks. Verify the separate GPU Monitor health endpoints and before/after digest remain unchanged during the bounded verification window.

## 12. Acceptance Criteria

- Development mode visibly presents four deterministic sample servers.
- The page shows managed total, used, available, and aggregate utilization without a large visual gap.
- Every mount shows path, media type, used/total, percentage, bar, and available bytes as one compact unit.
- Server and page totals do not silently double-count known duplicate capacity identities.
- Unsupported media is explicitly Unknown.
- Existing production inventory order, security boundaries, cleanup safety, and GPU Monitor isolation remain intact.
