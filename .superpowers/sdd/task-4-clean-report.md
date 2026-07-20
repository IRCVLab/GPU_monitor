# Task 4 Clean Storage deployment / verification report

## Status

Completed deployment and verification for the Clean Storage redesign at storage repo commit `2c3d3e7`.

No Storage code changes were made. GPU live/dev repos were already at the requested commits (`f2ea62f` live, `9f24800` dev). Backend services were not restarted.

## Deployment evidence

- Deployed only `/opt/storage-viz-dashboard/viewer`.
- Backup created: `/opt/storage-viz-dashboard/viewer.backup-20260721-025757`.
- Restarted only `storage-viz-dashboard.service`.
- Storage service after restart: `systemctl is-active storage-viz-dashboard.service` → `active`.
- Storage root check: `http://127.0.0.1:8088/` → HTTP `200`, `11268` bytes, SHA256 `1ed8c07d6b912716c756139d75cb717e7ca4bc1fff30ab44425b2409343a4229`.

## Required tests

- `python3 -m unittest discover -s agent -p 'test_*.py'` → `Ran 76 tests`, OK.
- `python3 -m unittest discover -s collector -p 'test_*.py'` → `Ran 90 tests`, OK.
- `python3 -m unittest viewer.test_serve` → `Ran 13 tests`, OK.
- `node viewer/viewer.test.js` → `viewer regression tests passed`.
- `node viewer/viewer_regression_test.js` → `viewer regression tests passed`.
- `STORAGE_VIZ_LINUX_HOST=ircv@166.104.167.11 STORAGE_VIZ_LINUX_PORT=2200 bash deploy/verify-linux.sh --remote` → overall exit `0`; remote commands `make -C scanner clean all test`, `python3 data/test_fixtures.py`, agent tests, collector tests, deploy script tests, and `deploy/install-agent.sh --dry-run` all exit `0`; remote cleanup removed.
- `git diff --check` → exit `0`.

## GPU process / health evidence

Baseline before Storage deployment:

- Backend 8001: `uvicorn`, PID `3232`; `/health` HTTP `200`, `15` bytes, SHA256 `a29ee2b15c494311c52521766e44af56a3ad2248e7a8ab465e5206463c13d288`, body `{"status":"ok"}`.
- Backend 8101: `uvicorn`, PID `1343647`; `/health` HTTP `200`, `15` bytes, SHA256 `a29ee2b15c494311c52521766e44af56a3ad2248e7a8ab465e5206463c13d288`, body `{"status":"ok"}`.

After deployment / final verification:

- Backend 8001: still PID `3232`; `/health` HTTP `200`, SHA256 `a29ee2b15c494311c52521766e44af56a3ad2248e7a8ab465e5206463c13d288`.
- Backend 8101: still PID `1343647`; `/health` HTTP `200`, SHA256 `a29ee2b15c494311c52521766e44af56a3ad2248e7a8ab465e5206463c13d288`.
- Live frontend 5173: frontend-only preview process was not running after deployment; restarted only the existing live frontend tmux workflow. New PID `2139717`, HTTP `200`, `1286` bytes, SHA256 `56383785be7200bfb1905f66d82e455cba2d211adb746af14acaab7c0bb6872e`.
- Dev frontend 5174: existing PID `843414`, HTTP `200`, `1717` bytes, SHA256 `5acd4fdcf66295b1cc1d6965554afb64b23a9c33331e6a18e809295994d1686e`.

## Reciprocal navigation / link evidence

Storage → GPU Monitor:

- Storage served HTML includes one `GPU Monitor` link to `http://127.0.0.1:5173/`.
- Playwright Storage QA confirmed `.suite-nav-link.href === "http://127.0.0.1:5173/"` on desktop and mobile.

GPU Monitor → Storage:

- Live repo `/home/ircv/workspace/monitoring_v2` at `f2ea62f`: `node frontend/src/routes/storage-link.contract.test.mjs` → `Storage navigation contract passed.`
- Dev repo `/home/ircv/workspace/monitoring_v2_dev` at `9f24800`: `node frontend/src/routes/storage-link.contract.test.mjs` → `Storage navigation contract passed.`
- Live source/build include `href="http://127.0.0.1:8088/"` with label `Storage`.
- Dev source includes `href="http://127.0.0.1:8088/"` with label `Storage`.

## Playwright msedge QA evidence

Used Playwright CLI with `--browser msedge`. Chrome was not used.

Desktop viewport `1280x720`:

- Storage overview status: `7 servers`.
- Server order: `Poseidon`, `Hinton`, `Turing`, `Lecun`, `Ace`, `Neo`, `Shannon`.
- Visible actionable mount count: `22`.
- Mount order/list verified exactly across all seven servers.
- Compact hierarchy verified: overview list `display:flex`, rows `display:grid`, mount strips `display:grid`.
- Theme toggle persisted via `themeMode=dark` cookie; reload preserved `dark` class and `aria-pressed="true"`.
- Storage internal navigation verified: overview → `?server=poseidon#treemap` detail → overview back.
- Detail capacity rows for Poseidon: `/home`, `/data2`, `/data4`, `/data`, `/data1`, `/data3`.
- Horizontal overflow: overview `0`, detail `0`.
- Console/page errors: `[]`.
- Desktop screenshot SHA256: `2523e40febc626dc50e51e8bb6535af1abf0f96247ee67236f233ebef966ac71`.

Mobile viewport `360x732`:

- Same seven-server order and `22` visible mounts verified.
- Same compact hierarchy verified.
- Theme cookie persistence verified.
- Storage internal navigation verified.
- Horizontal overflow: overview `0`, detail `0`.
- Console/page errors: `[]`.
- Mobile screenshot SHA256: `4d61a09751af4507e3fdc7d65dc15956588a2772f64b0928c2eee9b1758a48b3`.

## Timeboxed browser limitation

Playwright navigation from Storage to `http://127.0.0.1:5173/` was timeboxed because the local machine already had a non-task in-app listener on `127.0.0.1:5173`, which interfered with direct browser navigation to the remote live GPU frontend. Per instruction, reciprocal GPU navigation was therefore verified through served HTML/source/build grep plus the live/dev `storage-link.contract.test.mjs` contract tests instead of continuing browser navigation loops.

## Cleanup

- Closed/killed Playwright CLI browser daemons.
- Removed msedge QA profiles under `/tmp/storage-viz-msedge-profile*`.
- Removed generated QA screenshots/scripts/log JSON from `/tmp`.
- Removed repository `output/` artifacts.
- Stopped the temporary 5174 SSH tunnel opened for this task; left the pre-existing 8088 tunnel untouched.

## Concerns / notes

- Live frontend 5173 was down after Storage deployment; recovered with frontend-only tmux preview restart using Node 24.14.0. Backend PIDs 8001/8101 remained unchanged.
- Direct live GPU browser navigation from the local browser remains limited by the pre-existing local `127.0.0.1:5173` listener; link correctness is covered by contract/source/build evidence.

## 2026-07-21 Reciprocal navigation port-conflict fix evidence

### Local TDD / code evidence

- RED: after updating `viewer/viewer.test.js` to require `http://127.0.0.1:15173/`, `node viewer/viewer.test.js` failed as expected with `AssertionError [ERR_ASSERTION]: header must include a same-tab GPU Monitor suite link` while `viewer/index.html` still linked to `http://127.0.0.1:5173/`.
- GREEN: changed Storage `viewer/index.html` same-tab `GPU Monitor` anchor to `http://127.0.0.1:15173/`.
- Approved design and implementation plan now use `http://127.0.0.1:15173/` as the Storage -> GPU default and explicitly document that dedicated local tunnel port 15173 avoids the unrelated local `127.0.0.1:5173` collision.
- `node viewer/viewer.test.js` → `viewer regression tests passed`.
- `node viewer/viewer_regression_test.js` → `viewer regression tests passed`.
- `python3 -m pytest viewer` → `21 passed in 2.50s`.
- `git diff --check` → exit `0`.

### Dedicated GPU live tunnel evidence

- Created/recovered tmux session `gpu-monitor-live-tunnel` running `ssh -N -L 127.0.0.1:15173:127.0.0.1:5173 -p 2200 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 ircv@166.104.167.11`.
- `tmux has-session -t gpu-monitor-live-tunnel` → exit `0`.
- `lsof -nP -iTCP:15173 -sTCP:LISTEN` → `ssh` PID `76509` listening on `127.0.0.1:15173`.
- `curl -sS -o /tmp/gpu15173.html -w 'http_code=%{http_code} bytes=%{size_download} final_url=%{url_effective}\n' http://127.0.0.1:15173/` → `http_code=200 bytes=1286 final_url=http://127.0.0.1:15173/`; response begins with GPU frontend HTML (`<!doctype html>`, `<html lang="ko" class="dark">`).

### Storage deployment attempt / blocker evidence

- Remote precheck over `ssh -p 2200 -o BatchMode=yes ircv@166.104.167.11`: `/opt/storage-viz-dashboard/viewer` exists; `systemctl is-active storage-viz-dashboard.service` → `active`; `curl http://127.0.0.1:8088/` → HTTP `200`, `11268` bytes; served HTML still contains `http://127.0.0.1:5173/`.
- Deployment to `/opt/storage-viz-dashboard/viewer` and restart of only `storage-viz-dashboard.service` are blocked by missing remote write/restart authority for user `ircv`: `/opt/storage-viz-dashboard/viewer` is `root:root` mode `0755`; `touch /opt/storage-viz-dashboard/viewer/.write-test` → `Permission denied`; `sudo -n true` and `sudo -n /usr/bin/systemctl restart storage-viz-dashboard.service` both return `sudo: a password is required`; `root@166.104.167.11` and `storage-viz@166.104.167.11` SSH with `BatchMode=yes` return `Permission denied`.
- Because the remote service could not be updated with the available non-interactive credentials, runtime Storage served-link verification remains blocked: current remote Storage still serves `http://127.0.0.1:5173/`, while local committed source/tests require the exact same-tab `http://127.0.0.1:15173/` link.
