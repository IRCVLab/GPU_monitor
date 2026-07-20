# Final clean fix report — 2026-07-21 03:25 KST

## Outcome

Implemented and deployed the final review-fix wave for Storage Viz. Current committed/deployed viewer satisfies the approved Clean token contract, hides the successful server-count lead, preserves the aria-live status channel, serves the exact `http://127.0.0.1:15173/` GPU link, and passes desktop msedge Playwright verification.

## Commit

- `75f3dad` — `Fix final storage overview review gaps`

## Code/test changes

- `viewer/styles.css` — changed dark `--surface2` to exact `#181b1f` and light `--surface2` to exact `#eceff1`.
- `viewer/app.js` — changed successful overview status handling to clear and hide the old server-count lead instead of displaying `N servers`.
- `viewer/viewer_regression_test.js` — added full approved light/dark token contract coverage, regression coverage for hidden successful server-count lead with `aria-live="polite"` preserved, and independent exact warning/critical percent selector assertions.

## TDD / local verification

- RED: `node viewer/viewer_regression_test.js` failed before production changes on dark `--surface2` (`#1c2024` vs expected `#181b1f`).
- `node viewer/viewer.test.js` → `viewer regression tests passed`.
- `node viewer/viewer_regression_test.js` → `viewer regression tests passed`.
- `python3 -m pytest viewer/test_serve.py` → `13 passed in 2.49s`.
- Node syntax checks for `viewer/app.js`, `viewer/data-client.js`, `viewer/overview.js`, `viewer/selection.js`, `viewer/tables.js`, `viewer/treemap.js`, `viewer/users-chart.js` → exit `0`.
- `git diff --check` → exit `0`.

## Deployment evidence

- Deployed committed `viewer/` to `/opt/storage-viz-dashboard/viewer` through `ircv@166.104.167.11:2200` using the provided sudo password authority.
- Restarted only `storage-viz-dashboard.service`.
- Remote `systemctl is-active storage-viz-dashboard.service` → `active`.
- Remote `curl http://127.0.0.1:8088/` → `http_code=200 bytes=11269`.
- Local storage tunnel on `127.0.0.1:8088` → `ssh` PID `4291` listening.
- Dedicated GPU tunnel on `127.0.0.1:15173` → `ssh` PID `76509` listening; `curl http://127.0.0.1:15173/` → `200`, `1286` bytes.

## Served/browser evidence

- Served Storage HTML contains exact `href="http://127.0.0.1:15173/"`.
- Served CSS contains dark `--surface2: #181b1f;` and light `--surface2: #eceff1;`.
- Playwright msedge unique-profile desktop run:
  - screenshot: `output/playwright/storage-viz-final-msedge-desktop.png`
  - JSON: `output/playwright/storage-viz-final-msedge-evidence.json`
  - no visible exact `7 servers` lead after successful load
  - `#overviewStatus` = empty, hidden, `aria-live="polite"`
  - click Storage `GPU Monitor` link → `http://127.0.0.1:15173/`
  - console errors: `[]`; page errors: `[]`

## Generated-output cleanup — 2026-07-21 03:31 KST

- Removed the tracked generated Playwright screenshot and JSON evidence files because generated output must not remain in the repository and the screenshot may expose operational data.
- Preserved the textual verification evidence above; no replacement screenshot or browser artifact was generated.
