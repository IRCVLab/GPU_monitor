# Storage scan visibility

Approved direction: show last completion and duration, actual next timer execution, running phase/root/file count/elapsed time and a clearly labelled historical ETA. Remove the orbit on the Rescan button and keep its dimensions stable; show status in a separate persistent panel. Automatic scans and rescans use the same remote state. Failures and disconnected/stale observations must remain distinguishable from success.

Keep immutable last-good snapshots independent of transient progress. Scanner writes bounded atomic progress telemetry at a throttled cadence, agent publishes lifecycle status including errors, transport reads fixed paths and systemd properties only. Central serves bounded validated telemetry independently of blocking rescan/snapshot locks. No remote paths or commands come from the browser. Older agents degrade to explicit unavailable progress; no fabricated percentages or next-run times. ETA uses last successful duration and is absent when unavailable or exceeded.

The detail page polls status independently of whether this browser initiated the scan, guards navigation races, and refreshes the displayed snapshot upon completion. Overview carries timing/status metadata without loading full snapshots. Local preview and tests precede deployment; central rollout alone cannot install remote agents.

## Reviewed acceptance details

- Observations older than 90 seconds are stale; scanner heartbeats older than 30 seconds explicitly suppress counts and ETA. Service activity takes precedence over leftover terminal files; interrupted runs are never shown as complete.
- Completion timestamps and actual duration describe the latest completed scan. `estimate_duration_sec` is separate, uses the most recent fully successful scan, survives partial failures, and explicit null never falls back to a partial snapshot.
- Real systemd deadlines use remote monotonic and realtime clock references. Disabled, unavailable, elapsed or invalid schedules remain unknown.
- A displayed generation is acknowledged only when that snapshot loads. Failed or old-generation downloads retry; downloads do not block status polling. Navigation guards cover A → B → A and per-server requests.
- Progress files carry a run identifier; lock-conflict attempts cannot overwrite the running scan. Success is published after snapshot publication.

## User-directed compact layout revision

The user rejected the standalone status card for low information density and separation from Rescan. Replace it with a compact toolbar in the server heading, with the action directly adjacent to last completion/duration and next-run countdown. Keep the button label Rescan in all states; show activity and errors beside it. Remove redundant header timestamps/scanner metadata and the SCAN STATUS heading. Sample mode shows only a small badge and completion history, without empty scheduling fields. Running scans add a compact second line with file count, elapsed time, estimated remaining time and target/root progress. Exact timestamps and the ETA basis remain available in tooltips. All collection and polling behavior remains intact.

Verified all three viewer suites and 16 browser scenarios (sample, idle, running, failed at 320/390/820/1440px; light/dark screenshots at390/1440). Idle/sample toolbar height32–34px; no horizontal page overflow or JavaScript errors. Independent review approved.
