
# Final fix wave report — multiserver Storage Dashboard

## Scope and interface decisions
- Fixed Important 1 by aligning agent publication with collector snapshot contract: payload now includes `config_digest`; `scan_generation`/snapshot filename now bind `server_id`, `scan_started_unix`, and `v1` exactly (`<server_id>-<scan_started_unix>-v1`). Existing collision handling remains immutable: a duplicate started-generation fails publication rather than emitting collector-incompatible suffixes.
- Fixed Important 2 with a fixed unprivileged active-state interface before manual rescan start: SSH argv after `--` is exactly `/usr/bin/systemctl show --property=ActiveState --value storage-viz-scan.service`; only the existing start path uses exact `sudo -n /usr/bin/systemctl start storage-viz-scan.service`. Active states `active`, `activating`, and `reloading` map to `ACTIVE_JOB`; malformed/nonzero active-state query maps to `ACTIVE_STATE_FAILED` and does not start a job or consume cooldown.
- Preserved security/separation constraints: no arbitrary command/path input, no shell execution, no admin fallback, no GPU Monitor modification/restart, no real monitored storage-server deployment. Remote verification used only isolated key-auth Linux execution under `/tmp`.
- Minor: removed the trailing blank line at EOF in `agent/mount_policy.py`.

## TDD RED evidence
- Important 1 RED: `python3 -m unittest agent.test_scan_runner.ScanRunnerTests.test_run_once_snapshot_and_status_validate_with_collector_without_hand_editing ...` failed with `ValueError: payload config_digest must be lowercase 64-hex`, proving actual `scan_runner.run_once()` output/status could not pass `collector.snapshot.validate_download()` without hand editing.
- Important 2 RED: `python3 -m unittest ... collector.test_transport.OpenSshTransportTests.test_scan_active_state_uses_fixed_unprivileged_systemctl_show_command ... collector.test_jobs.JobsTest.test_remote_active_scan_rejects_without_consuming_cooldown_or_starting_job ...` failed with missing `active_state_timeout_seconds`/`scan_active_state` and job manager returning `202` instead of `409 ACTIVE_JOB`; malformed query test also failed from missing interface.

## GREEN evidence and exact counts
- Targeted GREEN: `python3 -m unittest agent.test_scan_runner.ScanRunnerTests.test_run_once_snapshot_and_status_validate_with_collector_without_hand_editing collector.test_transport.OpenSshTransportTests.test_scan_active_state_uses_fixed_unprivileged_systemctl_show_command collector.test_transport.OpenSshTransportTests.test_scan_active_state_rejects_malformed_or_failed_bounded_output collector.test_jobs.JobsTest.test_remote_active_scan_rejects_without_consuming_cooldown_or_starting_job collector.test_jobs.JobsTest.test_remote_active_scan_guard_allows_inactive_and_blocks_unknown_query_result` → 5 tests, OK.
- Affected Python GREEN: `python3 -m unittest agent.test_scan_runner collector.test_transport collector.test_jobs collector.test_service` → 75 tests, OK.
- Final local gate artifact: `output/verification/final-local-green.txt`.
  - `python3 data/test_fixtures.py` → 4 tests, OK.
  - `python3 -m unittest discover -s agent -p 'test_*.py' -v` → 51 tests, OK.
  - `python3 -m unittest discover -s collector -p 'test_*.py' -v` → 83 tests, OK.
  - `python3 viewer/test_serve.py` → 8 tests, OK.
  - `node viewer/viewer.test.js` → 8 scripted assertions/functions, printed `viewer regression tests passed`.
  - `node viewer/viewer_regression_test.js` → 17 scripted assertions/functions, printed `viewer regression tests passed`.
  - `bash deploy/test_deploy_scripts.sh` → all deploy assertions printed PASS, including deploy asset tests complete.
  - `bash -n install.sh deploy/*.sh scanner/test_hstscan.sh` → exit 0.
  - `deploy/install-agent.sh --dry-run` → rendered agent install under a temp prefix; no systemctl call; `systemd-analyze` unavailable on macOS and skipped by script.
  - `git diff --check ea59cb5` before commit → exit 0.
- Final real remote Linux artifact: `output/verification/linux-verification.txt`.
  - Remote temp path: `/tmp/storage-viz-verify.FQISBt`.
  - Remote commands all recorded `exit_code=0`: `make -C scanner clean all test`; `python3 data/test_fixtures.py`; all agent unittest; all collector unittest; `bash deploy/test_deploy_scripts.sh`; `deploy/install-agent.sh --dry-run`.
  - `remote_cleanup=removed`; `overall_exit_code=0`.
  - Independent cleanup confirmation artifact: `output/verification/final-remote-cleanup-confirmation.txt`, `test ! -e /tmp/storage-viz-verify.FQISBt` succeeded.
- GPU Monitor separation: fresh `output/verification/gpu-monitor-before.txt` and `output/verification/gpu-monitor-after.txt` captured around the final remote `/tmp` verification; `output/verification/gpu-monitor.diff` is empty/0 bytes. LIVE and DEV health hashes remained `a29ee2b15c494311c52521766e44af56a3ad2248e7a8ab465e5206463c13d288`.

## Self-review
- Reviewed diff for fixed argv boundaries: active-state query is a fixed list appended after SSH `--`, uses no `sudo`, no shell, no caller-supplied command/path, bounded timeout, and bounded single-line output validation. Start remains the pre-existing exact sudo command only.
- Reviewed manual rescan semantics: local active job check still runs first; remote active query happens before cooldown/concurrency/job creation; active/unknown query states do not call `manual_rescan` or `start_rescan`; inactive path preserves audit/cooldown/concurrency and `manual_rescan` rechecks active state immediately before fixed start to reduce race risk.
- Reviewed agent/collector contract: actual agent status+snapshot now validates via collector without hand editing; payload/status config digests match; generation binds `scan_started_unix` as required by collector validation.
- Reviewed branch separation: no GPU Monitor source/config/runtime files were modified; only isolated `/tmp/storage-viz-verify.*` remote verification was used; cleanup confirmed absent.

## Commit
- Commit hash: final exact hash is reported in the task response; embedding the final hash inside this same tracked report would change the commit hash.

## Concerns
- macOS cannot compile Linux `SYS_getdents64`; the required Linux scanner build/test was run on the key-auth remote Linux host and passed in the final artifact.
- The repository ignores `.superpowers/sdd/*`; this report must be force-added if it is required in the commit.
