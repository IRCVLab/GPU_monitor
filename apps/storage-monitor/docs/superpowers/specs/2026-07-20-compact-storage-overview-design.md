# Compact Storage Overview Design

## 1. Goal

Make the storage dashboard materially denser and easier to scan without reducing the mount-level facts needed to decide where capacity is available.

The redesign removes information that does not help that decision:

- boot filesystems are not collected;
- the page-wide total-storage aggregate is removed;
- repeated server-wide capacity prose is removed;
- nested mount cards become compact inline mount strips.

Server and mount order remain the configured/input order. Capacity pressure never changes ordering.

## 2. Collection Policy

The scanner excludes `/boot` and every descendant such as `/boot/efi` before generic local-filesystem selection. The exclusion is path-specific; `vfat`, `exfat`, and other supported filesystems remain eligible when mounted at a non-boot data path.

Excluded boot mounts are reported with the bounded reason `boot-filesystem` so collection behavior remains observable. They do not appear in `selected_roots`, `mounts`, mount counts, capacity aggregation, detail selectors, or filters.

Existing mandatory exclusions for network, virtual, container, loop/image, and bind-subtree mounts remain unchanged.

## 3. Overview Information Hierarchy

The overview has three levels only:

1. Server identity and exceptional status.
2. Mount path and storage medium.
3. Used percentage, free capacity, and pressure bar.

Normal status is quiet and occupies no badge. Warning, critical, stale, collection, and connectivity states retain text plus shape so status is not communicated by color alone.

The page-wide `전체 로컬 스토리지` block is deleted. Cross-server total capacity is not actionable because researchers choose a server and a mount, not one pooled storage volume.

The explanatory lead is reduced to one short line or removed when the page already communicates the hierarchy without it. The sample-data marker remains available only in sample mode.

## 4. Server Row

Each server remains one keyboard-accessible button and one visual surface.

The compact server header contains:

- server name;
- mount count after boot exclusion;
- exceptional status badge only when needed.

It does not repeat total, used, available, utilization, or a normal-status label. Capacity facts belong to the mount strips below it.

Desktop rows use a narrow server column and a flexible mount area. Spacing, border radius, and hover treatment remain restrained; the redesign reduces padding and nested borders rather than removing click affordance.

## 5. Inline Mount Strip

Each mount is one compact horizontal strip with this visual order:

1. path;
2. `SSD` or `HDD` label;
3. used percentage;
4. thin pressure bar;
5. free capacity.

Used/total capacity text is removed from the overview because it duplicates the percentage and free-capacity decision cues. Exact total and used values remain available in server detail.

The path and percentage receive the strongest text contrast. Media and free capacity use secondary contrast. Warning and critical pressure use semantic colors; healthy mounts use the neutral/accent treatment already defined by the theme.

Long paths truncate visually and expose the full path through the native title text. Numeric fields use tabular figures so strips align without requiring a rigid table.

## 6. Responsive Layout

On wide screens, mount strips fill a compact two- or three-column grid according to available width. Server cards with many mounts wrap naturally without horizontal scrolling.

On narrow screens, the server header remains one line where possible and mount strips become one column. Touch targets remain at least 44 pixels for the server button even though internal visual spacing is reduced.

## 7. Detail Capacity Area

The server detail view keeps exact mount information but replaces the current capacity hero cards with a denser rail/list. Each mount line contains path, filesystem/media, used/total, percentage, free capacity, and one thin bar.

The detail view keeps mount selection behavior and all existing treemap, users, top-files, stale-files, and cleanup interactions. This redesign changes presentation density, not storage analysis behavior.

## 8. Data Flow and Compatibility

Boot exclusion occurs in `agent/mount_policy.py`, before scanning and snapshot generation. The viewer consumes snapshots without a second hidden boot filter, ensuring overview, detail, counts, and filters agree.

The existing per-mount summarization and pressure derivation remain the source of truth. Page-wide aggregation may remain as an internal tested utility if other code uses it, but the overview does not compute or render it.

Existing snapshots that still contain boot mounts remain valid. During rolling deployment, the viewer defensively omits `/boot` descendants so old snapshots do not reintroduce UI clutter. This compatibility filter is temporary-safe and consistent with the new collection policy.

## 9. Error and Partial States

Servers without a valid snapshot keep their existing error treatment and remain in configured order. A server with zero actionable mounts displays a concise `표시할 데이터 마운트 없음` state rather than a capacity card.

Unknown storage media remains `Unknown`; missing capacity remains `—`. The UI does not invent totals or silently reinterpret partial data.

## 10. Motion and Accessibility

Hover uses only a subtle surface change and at most a one-pixel lift. Mount bars may transition width when data changes, but layout dimensions do not animate.

Keyboard activation, focus-visible outlines, semantic button/list structure, reduced-motion behavior, text-plus-shape status cues, and mobile overflow protection remain required.

## 11. Verification

Automated tests must prove:

- `/boot` and `/boot/efi` are excluded with `boot-filesystem`;
- non-boot `vfat` data mounts remain eligible;
- boot mounts do not affect mount counts or overview/detail output;
- the page aggregate is absent;
- server order and mount order remain unchanged;
- overview strips contain path, media, percentage, bar, and free capacity without repeated used/total text;
- detail capacity lines retain exact used/total values;
- desktop and mobile layouts have no horizontal overflow;
- keyboard navigation and reduced-motion behavior remain functional.

Code verification includes scanner tests on Linux, agent and collector suites, viewer Python and JavaScript regression suites, static HTTP checks, and live API validation after deployment.

## 12. Deployment Isolation

Changes deploy only to the storage-viz scanner agents and storage-viz dashboard paths. Existing GPU Monitor processes, ports, worktrees, services, and health responses remain untouched and are compared before and after rollout.

