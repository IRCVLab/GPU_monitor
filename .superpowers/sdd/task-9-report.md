# Task 9 Report: Build the Dense Overview-First Viewer

## Changed files
- `viewer/overview.js` — added centralized pure helpers for capacity thresholds, per-mount pressure evaluation, exceptional-state precedence, route parsing/building, and dense overview row rendering.
- `viewer/index.html` — replaced the host picker shell with overview/detail navigation, one logical `h1`, overview list container, and back-to-overview control while preserving the existing detail workspace modules.
- `viewer/data-client.js` — added bounded API helpers for session/server/snapshot/job/rescan calls plus stable-order, failure-isolated overview snapshot enrichment; preserved static-file fallback for local non-API use.
- `viewer/app.js` — rewired bootstrapping to overview-first mode, stable route/query/hash navigation, detail shell toggling, overview rendering, snapshot caching, and simple per-server rescan polling compatible with Task 8 APIs.
- `viewer/styles.css` — added dense list styling, restrained status chips, compact mount bars, overview/detail shell layout, and narrow-width stacking without horizontal overflow.
- `viewer/viewer.test.js` — added RED/GREEN coverage for ordered overview snapshot enrichment, centralized thresholds, precedence rules, route helpers, and the updated source contracts.
- `viewer/viewer_regression_test.js` — added render/navigation shell assertions for one `h1`, overview script ordering, stable row order, compact capacity text, Korean exceptional state copy/shape, click/Enter activation, and back-to-overview behavior while keeping existing treemap/selection regressions.

## RED evidence
Focused RED commands before implementation:

`node viewer/viewer.test.js`

Observed failure:
- `TypeError: loadOrderedSnapshotsForOverview is not a function`

`node viewer/viewer_regression_test.js`

Observed failure:
- `AssertionError [ERR_ASSERTION]: viewer code must be loaded from ordered external scripts including overview.js before app.js`

These failures confirmed the absent overview bootstrap/navigation contracts before production edits.

## GREEN commands/results
Frontend task commands:

`node viewer/viewer.test.js && node viewer/viewer_regression_test.js`

Result: both suites passed (`viewer regression tests passed` twice).

Relevant Python/API verification:

`python3 viewer/test_serve.py && python3 -m unittest collector.test_service -v`

Result: `viewer/test_serve.py` ran 6 tests, `OK`; `collector.test_service` ran 19 tests, `OK`.

Syntax/static checks:

`node --check viewer/app.js && node --check viewer/data-client.js && node --check viewer/overview.js && node --check viewer/viewer.test.js && node --check viewer/viewer_regression_test.js`

Result: exit 0.

`python3 -m py_compile viewer/serve.py viewer/test_serve.py`

Result: exit 0.

`git diff --check`

Result: exit 0 before commit.

## Design decisions
- Kept the landing page as a dense ordered list rather than a card grid; the viewer consumes server summaries in API order and never re-sorts by pressure, freshness, or status.
- Centralized capacity thresholds in `viewer/overview.js` and matched the inventory defaults from `collector.inventory.CapacityThresholds` (`80/92` percent, `512/128 GiB` free-space thresholds) so percentage and remaining-byte pressure stay consistent with backend expectations.
- Centralized the primary overview precedence in pure helpers and mapped each exceptional state to concise Korean text plus a visible shape cue, keeping normal freshness quiet.
- Preserved compact mount bars for every row by deriving overview rows from per-server snapshots in stable summary order with per-row failure isolation instead of timing-based insertion.
- Added query/hash route helpers (`?server=<id>#<tab>`) so refresh restores detail context without a framework and Back can return to overview predictably.
- Kept the existing treemap/users/top-files/stale modules intact behind a new detail shell instead of rewriting Task 10 detail behavior.
- Limited static-file fallback to missing/non-API environments (`404`/network-style failures) so authenticated API failures do not silently masquerade as sample data.

## Self-review findings
- **Stable ordering:** overview rendering iterates current summary order directly; snapshot enrichment uses `Promise.all` over the ordered summary array and returns rows in that same order.
- **Exceptional precedence:** tests cover absent/not-installed, pull unreachable/invalid, scan failed, configuration drift, partial scan, stale retained snapshot, active scan, and capacity pressure in the required order.
- **Accessibility:** exactly one logical `h1`; rows are full-width buttons with Enter/Space activation and existing focus-visible styling; detail back navigation is a real button.
- **XSS safety:** new overview rendering uses DOM creation + `textContent` rather than interpolating untrusted summary/snapshot strings into HTML.
- **Failed snapshot isolation:** a single snapshot failure populates that row’s status/error path without throwing away the full overview list or disturbing order.
- **URL navigation:** route helpers sanitize server ids/tabs, update query/hash state, restore detail tabs on refresh, and support overview return.
- **Responsive density:** overview rows collapse to a single-column stack below `760px`; mount bars remain visible and `main` still keeps horizontal overflow hidden.
- **Existing detail behavior:** treemap, table, and cleanup regressions remain green; detail modules still render from the existing snapshot shape after navigation.

## Commit
- `57549273e08dbc77425ca478f9708974fb5802b7` — `feat: add multiserver storage overview`

## Concerns
- I did not run a live browser smoke flow, so responsive density and focus order are validated by structure/tests/CSS review rather than an interactive browser session.
- The report file is intentionally left outside the viewer-only commit because the task’s commit step explicitly staged `viewer`.

## Review fixes

### Changed files
- `viewer/data-client.js` — preserved exact manifest order in `normalizeHosts`; removed default-host promotion.
- `viewer/overview.js` — added `snapshot_load_failed` status, preserved required higher-priority server-state precedence above it, and switched overview rendering to `ul > li > button` semantics.
- `viewer/app.js` — made API/static bootstrap probing explicit and sequential, exposed a pure bootstrap helper for tests, and added detail-load generation guards so stale async completions cannot mutate the current detail view.
- `viewer/index.html` — changed the overview container to a native `ul`.
- `viewer/styles.css` — added list reset styling for the native overview list.
- `viewer/viewer.test.js` — added regressions for middle `default:true` order preservation, client snapshot-load-failure precedence, and relaxed status-label assertions.
- `viewer/viewer_regression_test.js` — added regressions for native list semantics, visible client-load-failure rendering, explicit bootstrap mode selection, stale async detail completion, and relaxed Korean-label assertions.

### RED evidence
Focused RED commands before these fixes:

`node viewer/viewer.test.js`

Observed failure:
- `AssertionError [ERR_ASSERTION]: manifest order must remain exact even when default:true appears in the middle`

`node viewer/viewer_regression_test.js`

Observed failure:
- `AssertionError [ERR_ASSERTION]: overview list must be a real ul`

The newly added regressions also required absent implementation for explicit bootstrap probing, stale-detail load guards, and snapshot-load-failure rendering; those were added before production edits and verified through the same RED cycle.

### GREEN commands/results
Required frontend commands:

`node viewer/viewer.test.js`

Result: passed (`viewer regression tests passed`).

`node viewer/viewer_regression_test.js`

Result: passed (`viewer regression tests passed`). The command emits expected test-fixture console logging from simulated `404` and snapshot-failure branches, but exits `0` and all assertions pass.

Required Python/API commands:

`python3 viewer/test_serve.py`

Result: 6 tests passed, `OK`.

`python3 -m unittest collector.test_service -v`

Result: 19 tests passed, `OK`.

Required syntax/diff commands:

`node --check viewer/app.js && node --check viewer/data-client.js && node --check viewer/overview.js && node --check viewer/viewer.test.js && node --check viewer/viewer_regression_test.js`

Result: exit 0.

`git diff --check`

Result: exit 0.

### Self-review findings
- **Ordering:** manifest order is now preserved exactly; neither `default:true` metadata nor overview severity mutates list order.
- **Precedence:** client snapshot/API load failure is visible only after absent/unreachable/invalid/failed/drift/partial/stale checks, and before healthy/active/capacity-only presentation.
- **Stale async completion:** detail loads carry a generation token; late completions may update cache/overview state but cannot assign `DATA`, surface the wrong error, or render the wrong server into the active detail shell.
- **Explicit API/static mode:** only an exact `404` from the initial `/api/session` probe selects static mode; status-less/network failures and post-session API failures now surface as API errors instead of falling back to samples.
- **Semantics/focus:** overview uses native `ul/li/button` semantics, keeping whole-row keyboard activation and existing focus-visible treatment.
- **XSS:** overview rendering still uses DOM node creation plus `textContent`, so the new error-state path does not introduce HTML interpolation from untrusted data.

### Commit
- `ecb6624f6d57ef85cd3e5934e5c4c8556d1ed532` — `fix: stabilize storage overview state`

### Concerns
- The required Node regression suite intentionally logs simulated API/session/snapshot failures through the existing console paths while still passing; browser evidence from the controller run remains preserved, but the unit-test output is not completely silent.

## Review fixes — second wave

### Changed files
- `viewer/app.js` — added per-server request versions so obsolete same-server completions are discarded before every shared mutation, while the newest same-server completion can still refresh cache/overview state off-route.
- `viewer/data-client.js` — aligned frontend safe-ID validation with backend rules by rejecting exactly `.` and `..`, and exported the shared helper for API validation tests.
- `viewer/overview.js` — added shared `isSafeServerId` route/helper validation so `.` and `..` never enter URL parsing or generated detail links.
- `viewer/viewer.test.js` — added regressions for `.`/`..` rejection in API helpers and route helpers.
- `viewer/viewer_regression_test.js` — added deterministic deferred-promise regressions for late obsolete same-server success/rejection, plus async-safe console muting for expected failure-path tests.

### RED evidence
Focused RED commands before production edits:

`node viewer/viewer.test.js`

Observed failure:
- `TypeError: safeServerId is not a function`

`node viewer/viewer_regression_test.js`

Observed failures/noise:
- expected simulated API/session/snapshot failure stacks still printed because console muting restored too early for async tests
- `AssertionError [ERR_ASSERTION]: obsolete older alpha success must not rewrite overview status or mount metrics`

### GREEN commands/results
`node viewer/viewer.test.js`

Result: passed (`viewer regression tests passed`).

`node viewer/viewer_regression_test.js`

Result: passed (`viewer regression tests passed`) with clean output.

`python3 viewer/test_serve.py`

Result: 6 tests passed, `OK`.

`python3 -m unittest collector.test_service -v`

Result: 19 tests passed, `OK`.

`node --check viewer/app.js && node --check viewer/data-client.js && node --check viewer/overview.js && node --check viewer/viewer.test.js && node --check viewer/viewer_regression_test.js`

Result: exit 0.

`git diff --check`

Result: exit 0.

### Self-review findings
- **Per-server request versions:** once a newer request starts for the same server, any older completion now exits before cache writes, overview-entry mutation, overview rerender, or detail/error mutation.
- **Cross-route behavior:** the newest request for server A can still refresh A's cache while the route is on B, but it cannot overwrite B detail state because detail generation checks still gate `DATA` and error rendering.
- **Safe IDs:** API helpers, route parsing, and route generation now reject `.` and `..` exactly, matching backend behavior without broadening the character policy.
- **Console noise:** regression tests now stub `console.warn/error` through awaited completion and restore them in `finally`, so passing output stays clean without changing production logging.
- **XSS/focus:** the new changes remain inside existing safe text/DOM rendering paths and preserve whole-row button semantics and focus treatment from the first fix wave.

### Commit
- `a984044e73a8f66f04fc6e730dbdcc2923130de6` — `fix: guard storage overview request versions`

### Concerns
- The report section was appended after creating the exact requested commit so it could record the true commit hash; this leaves the report file modified in the worktree alongside the controller's pre-existing untracked artifacts.
