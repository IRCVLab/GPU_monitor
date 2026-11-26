# Storage monitor bug catalog (2026-11-26)

Every bug below was reproduced during the 2026-11-26 bug hunt against main at
06cdd5f. Repro scripts live outside the repo, in the session scratchpad
(`hunt-collector/`, `hunt-agent/`, `hv/`). The harness phase ports each one
into the repo as a failing test before any redesign unit touches its area.

"Cause" names the structural cause from the redesign framing:

- **S1** The same judgment is computed in more than one place.
- **S2** The state machine is implicit: string and boolean fields mutated
  from many functions.
- **S3** The viewer keeps its state in globals, with no store or router.
- **S4** Error boundaries are wrong: one failure spreads, errors map to the
  wrong responses, path checks are scattered.
- **S5** Agent publication, identity and deployment.

## Collector and API

| ID | Bug | Cause | Where |
|---|---|---|---|
| C1 | "연결 실패" badge stays up to 15 min while telemetry and data are current | S2 | service.py `_mark_unreachable`, `_collect_scan_status` |
| C2 | A rejected newer generation is re-downloaded every telemetry tick (up to 512 MB per download) | S2 | service.py `_collect_scan_status`, `_mark_invalid` |
| C3 | One server's unreadable state blanks `/api/servers` for every server | S4 | service.py `server_summaries`, serve.py |
| C4 | A scheduled scan starting between the rescan check and the job thread marks the scan "failed" | S2 | service.py `_manual_rescan_locked`, `_mark_rescan_failed` |
| C5 | A rescan POST gets no HTTP response when the store lock is busy | S4 | jobs.py `_start_rescan_locked`, serve.py |
| C6 | A store error on a known server is reported as UNKNOWN_SERVER (404) | S4 | serve.py snapshot route |
| C7 | In trusted-proxy mode, `/./data/...`, `/data/` and `/x/../data/...` bypass the read check | S4 | serve.py `_data_path` plus static handler and `viewer/data` symlink |
| C8 | Every dashboard restart re-downloads every snapshot it already holds | S2 | service.py verified publications kept in memory only |
| C9 | `/api/servers` stalls up to about 400 ms while a snapshot is committed | S4 | service.py summary lock held across commit and fsync |

## Viewer

| ID | Bug | Cause | Where |
|---|---|---|---|
| V1 | Window focus or the 15-min auto-refresh resets drill level, folder and removal selection | S3 | app.js focus handler, `refreshOverviewData`, `resetCleanupSelectionState` |
| V2 | The server list and the server page color the same volume differently (thresholds differ) | S1 | data-client.js `capColor` vs overview.js `DEFAULT_CAPACITY_THRESHOLDS` |
| V3 | The Stale "Select" button is clipped and partly unclickable at every width | S3 (CSS) | styles.css stale columns |
| V4 | At 390 px the Stale header drifts from its columns; the Path column is 32-62 px wide | S3 (CSS) | `#staleHead` outside `#staleViewport` |
| V5 | The same file shows different ages in Top files and Stale (browser clock vs scan time) | S1 | tables.js `daysAgo` vs `age_days` |
| V6 | A server card opens the current tab, not the tab its link names | S3 | app.js routing vs overview.js card href |
| V7 | An unknown server id leaves a half-rendered detail page | S3/S4 | app.js `showDetailLoading` then error |
| V8 | Back then forward loses the Top files owner filter | S3 | app.js filter reset on server change |
| V9 | Users sorted by Files with a storage filter active sorts by hidden unfiltered counts | S1 | tables.js sort key vs displayed value |

## Agent and scanner

| ID | Bug | Cause | Where |
|---|---|---|---|
| A1 | Renaming a directory during a scan silently drops its subtree; the root still says complete | S5 | hstscan.c reopen-by-path, ENOENT ignored |
| A2 | Mount ids differ per mount namespace, so the slow-capture fingerprint never matches and HDD reuse breaks | S5 | scan_runner.py fingerprint uses `source_mount_id` |
| A3 | If no root scans cleanly, nothing is published (single-root hosts freeze) | S5 | scan_runner.py publish requires one complete root |
| A4 | A failed HDD scan discards a good SSD scan from the same run | S5 | scan_runner.py all-or-nothing publication |
| A5 | Paths longer than 4096 characters are not scanned | S5 | hstscan.c fixed path buffer |
| A6 | `prune_home_mb` has no effect | S5 | hstscan.c applies it only to target `/` |
| A7 | Non-UTF-8 sibling names collide as `�` | S5 | hstscan.c name encoding |
| A8 | The lifecycle keeps `phase: publishing` after completion | S2 | scan_runner.py final publish_lifecycle |
| A9 | HDD capacity figures can be up to 3 h old because the whole cached capture is reused | S1/S5 | scan_runner.py slow-capture reuse |
| A10 | A tree deeper than the collector's 128-level cap gets the whole snapshot rejected; default pruning already emits 150 levels for a 101 MB file at depth 150 (found in M4) | S5 | hstscan.c rollup depth vs collector snapshot.py depth cap |

## Fixed earlier on 2026-11-26 (shipped)

Collapsed removal bar (#11), the removal command without prompts and with a
host check (#13), the mount guard and nested-pick summary (#14), and the
typical/longest ETA (#12, #15). All touch S1-S4 and must keep working through
the redesign.
