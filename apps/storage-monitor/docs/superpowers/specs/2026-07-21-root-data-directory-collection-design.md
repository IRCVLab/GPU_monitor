# Root `/data` Directory Collection Design

## Problem

The agent currently treats the root filesystem specially: it scans `/home` but never scans `/` as a whole. This protects the servers from an expensive, noisy root traversal. A server can, however, store research data in `/data` as an ordinary directory on the root filesystem rather than as a separate mount. That directory is absent from `/proc/self/mountinfo`, so the current mount-only selection policy omits it.

## Decision

Keep the root-filesystem safety boundary and add one bounded synthetic root: `/data`.

- Continue scanning `/home` on the root filesystem.
- Add `/data` only when it exists as a directory and is backed by the same device as `/`.
- Do not add a synthetic `/data` when `/data` is an explicit mount. Existing local, remote, bind, virtual, container, and boot mount policy remains authoritative.
- Never scan `/` as a target.
- Keep `/home` and synthetic `/data` as separate scanner targets even though they share one backing device.
- Preserve the same `capacity_id`, media classification, filesystem type, and mount source for both roots so UI capacity aggregation counts the root device once.
- The scanner's existing `st_dev` boundary prevents either target from descending into a nested filesystem mounted below it.

Only the exact top-level `/data` path is added. Pattern matching such as `/data1`, `/dataset`, or arbitrary root directories is intentionally out of scope; separate mounts at those paths continue to be discovered normally.

## Architecture

### Filesystem probe

`agent.scan_runner` owns the live filesystem probe because `agent.mount_policy` remains deterministic and pure over supplied data. The probe:

1. Finds the root mount entry.
2. Refuses synthesis if mountinfo already contains an exact `/data` entry, regardless of whether that mount is selected or prohibited.
3. Uses `lstat` through an injectable adapter so symlinks are never followed.
4. Selects `/data` only when it is a real directory and its `st_dev` matches the root mount device.
5. Treats missing, inaccessible, or changing paths as absent rather than failing the scan.

### Root selection

`mount_policy.select_scan_roots` accepts a supplied list of verified root-directory paths. Selection first chooses one canonical source mount per existing mount identity. Only the canonical `/` source then expands into `/home` and `/data`; aliases sharing the root identity remain skipped duplicates. A root source can therefore emit multiple distinct `SelectedRoot` records without weakening duplicate mount handling.

If `/home` is a separate eligible mount, that mount owns `/home` while the root mount may still own synthetic `/data`.

### Snapshot and UI

The existing enrichment path links each scanner result to its selected root. `/home` retains the existing logical `mount_id` derived from the source mount ID. Synthetic `/data` uses the stable safe logical ID `<source-mount-id>-root-data`. Both records retain the same source mount ID internally and use the root mount's `major_minor`, so block-media resolution and `capacity_id` remain shared. The dashboard can display separate `/home` and `/data` usage trees while showing the physical root capacity once.

Collector validation continues to require unique logical `mount_id` and `scan_root` values. Linked mount records use the same logical IDs as their selected roots.

### Scanner target-device guard

The agent passes each target to `hstscan` together with its expected Linux `major:minor` device. `hstscan` compares that expected device with the target's `st_dev` immediately after `lstat` and before starting traversal. A missing target, a symlink, or a device mismatch is skipped and cannot be scanned. The resulting absent raw mount is reported through the existing bounded per-root failure path, while other safe roots continue.

This guard applies to every agent-selected target, not only synthetic `/data`, and closes the mount-change race between policy selection and traversal.

### Hardlink accounting

`hstscan` keeps its existing process-global hardlink deduplication. A physical inode linked across `/home` and `/data` is counted once in total and is attributed to the first deterministic target that encounters it (`/home` precedes `/data`). This preserves physical-byte accounting and avoids double-counting users or server totals. Per-root hardlink attribution is therefore intentionally first-target allocation rather than independent `du` parity.

## Failure and Safety Behavior

- Missing `/data`: unchanged `/home`-only behavior.
- Separate local `/data` mount: existing mount selection behavior.
- Remote, bind, virtual, or prohibited `/data` mount: excluded; no synthetic fallback.
- `/data` symlink, including a same-device symlink, or non-directory: excluded.
- Probe permission or race failure: excluded for that run; the agent continues with other safe roots.
- Device or mount change after the probe: scanner expected-device guard skips the changed target.
- Nested mount below `/data`: scanner device boundary prevents crossing into it; its own eligible mount is scanned separately.

## Tests

- Mount policy emits `/home` and supplied synthetic `/data` from one root device.
- Separate `/home` still coexists with root-backed `/data`.
- Root aliases remain duplicates rather than selected roots.
- Probe accepts an ordinary same-device directory.
- Probe rejects missing, non-directory, same-device symlink, different-device path, explicit mount, prohibited mount, and lstat failure cases.
- Exact `/data` is the only synthesized path; `/data1` and `/dataset` are not synthesized.
- Scanner tests prove expected-device mismatch skips traversal and that process-global hardlink allocation remains deterministic across same-device targets.
- Scan runner passes both roots with expected devices to `hstscan`, emits two linked records with unique logical `mount_id` values and one shared `capacity_id`, and preserves existing absent-`/data` behavior.
- Collector validation accepts the two unique logical roots while still rejecting duplicate logical IDs.
- Full Python, scanner, deployment, viewer, and snapshot test suites remain green.

## Deployment

Deploy the updated agent Python modules and their tests through the existing agent installation path. Do not change the central dashboard service contract. Trigger a bounded manual scan after deployment and verify that affected servers report `/data` without duplicate physical capacity.
