# Synthesis note: storage monitor target design

This note records how `target-design.md` was assembled from the three arena candidates. It covers which candidate became the base, what was grafted, what was rejected, and which facts were corrected.

## Base and why

Candidate 1 is the base. It had the tightest single mental model: the volume as the unit of publication, one actor per server with pure reducers, and one judge module behind a render-only viewer. It also had the most concrete file layouts, JSON shapes and test names, and a migration made of small units that each deploy alone. It kept the dashboard release path and CI signing, so central never holds the agent signing key.

## Cross-judge verdict

A Sonnet judge, reviewing independently, chose C1 as the base.

| Candidate | Scores |
|---|---|
| C1 | 5 / 5 / 5 / 4 / 4 / 4 |
| C2 | 4 / 4 / 3 / 2 / 2 / 4 |
| C3 | 4 / 4 / 3 / 3 / 2 / 3 |

## Grafts

From candidate 2:

- Canary-first rollout with a `probation` state per host, and `rollout.json` that halts the fleet when the canary fails or any host rolls back (§3.4, I23). C1's CI signing and `rollback_floor_seq` stay.
- Agent rollout stays coupled to the dashboard release. The canary did not need a second desired state. §3.4 states what a dashboard rollback does to agents.
- Host-computed scan state (`idle`, `running`, `interrupted`) from `scan.lock` and `run.json`. It replaces C1's `ExecStopPost` finalize and `boot_id` check (§3.1, I12).
- Invariants stated as tests: C1's design rules became rows I19 to I25, next to I1 to I18.
- Total transition tables and seeded random-event tests for every state machine (§3 preamble, I19).
- `--fixtures` mode with scrubbed fixtures for the viewer, tests and demos (§4.3).

From candidate 3:

- Contact results ordered per host by `contact_seq`, so a late failed probe cannot reverse a newer success. Host timestamps are aged against host time, never compared with the central clock (I7, I20).
- A durable rescan job with request ids, idempotent resend during `reconciling`, and a terminal `unconfirmed` state. No success on SSH exit alone (§3.3, I24).
- The complete HTTP error-to-status table (405, 413, 410, a 200 fleet with typed per-host error rows, a 200 empty detail) and one JSON error envelope with `request_id` (§4.3).
- The descriptor-relative traversal for A1 and A5: `openat` with `O_NOFOLLOW`, an FD budget with verified reopen from the pinned root, and scoped error rows with partial coverage (§4.1).
- A guarded `statvfs` capacity sample on every tick, including slow-only not-due ticks, kept separate from captures (A9).
- Rollback after a bad first real run, through probation (§3.4).
- Fail-closed removal for an unreadable mount table, same-device bind mounts, and paths the encoder cannot round-trip (§4.4).
- Selection reconcile across a new head by binding, raw path and kind. Anything else is flagged, and Copy stays disabled until the user reviews it (§3.5).
- The ambiguity rule for duplicate or missing UUIDs, and the capture binding that carries the covered subtree (§2.1).
- An explicit prune class per target for A6, instead of deleting the option.

## Rejected

- **Svelte rewrite.** The bugs are state-ownership bugs, a rewrite cannot land in small units, and it would add npm to the release path. It can be revisited as one bounded unit after API v2.
- **HTTPS mirror delivery (C3).** It needs outbound HTTPS from hosts, a mirror to operate and a second delivery path. It also breaks "central starts every connection".
- **Central-host signing (C2).** A compromised central host could then run root code on every storage server.
- **Per-host SQLite (C3).** One writer per directory and rename order already make the only multi-file write safe. JSON stays readable with `jq`.
- **Big-bang cutover (C2).** Units that "ship nothing" until a final host cutover prove nothing in production and roll back all at once.

## Factual errors corrected in the base

- **Scanner grouping.** C1 said "one hstscan per volume". Today one run takes several `--target` roots (`agent/scan_runner.py:530-542`) and shares one `(dev, ino)` set (`scanner/hstscan.c:206-208`). The design keeps one run per due group, with all roots of one filesystem, bind aliases included, in the same run, and publishes per-volume captures from it. Shared hard links count once per volume. Their per-root split goes to the first root that claims the inode.
- **VolumeId.** C1 hashed `fs_uuid + mount_root` yet claimed bind mounts get the same id. The id is now the UUID or a local alias only. The covered subtree moved into the root binding. A duplicate or missing UUID disables reuse and grouping, with a `VOLUME_IDENTITY_AMBIGUOUS` diagnostic.
- **A6.** C1 deleted `prune_home_mb`. It is now fixed with a prune class per target.
- **V3.** C1 had a screenshot check only. It now has a pointer hit test at the button's corners and a click that changes the pick count.
- **ETA history.** C1 summed per-volume durations, which the shipped ETA does not do. Durations stay per plan with the shipped lower-median and max rule, imported once at M2.
- **Migration order.** C1 put low-risk units first. The order is now riskiest-first where dependencies allow, with a shim table (S1 to S8, R1).
