# Task 3 Report — SQLite live-state preflight and backup

## Scope

Implemented the production data-safety boundary for the GPU monitor backend so startup can preflight and optionally back up the live SQLite database before schema initialization when `MONITORING_EXPECTED_SERVER_COUNT > 0`, while leaving local development behavior unchanged when that setting remains `0`.

## Files changed

- `apps/gpu-monitor/backend/live_database.py`
- `apps/gpu-monitor/backend/tests/test_live_database.py`
- `apps/gpu-monitor/backend/config.py`
- `apps/gpu-monitor/backend/database.py`
- `apps/gpu-monitor/backend/main.py`
- `apps/gpu-monitor/.env.example`

## Backend path / ownership

- Entry point: `apps/gpu-monitor/backend/main.py` `lifespan()`
- Domain boundary: new `apps/gpu-monitor/backend/live_database.py`
- Persistence side effects:
  - read-only SQLite inspection before schema init
  - optional online backup to a configured directory
  - existing schema creation/migration remains in `init_db()`

## TDD evidence

### RED

1. Added `backend/tests/test_live_database.py` first.
2. Ran:

```bash
cd apps/gpu-monitor
SECRET_KEY=baseline-test-key-1234 ADMIN_PASSWORD=baseline-test-password \
  python3.12 -m unittest backend.tests.test_live_database -v
```

3. Observed expected failures:
   - `backend.live_database` import failed because the module did not exist
   - `Settings` still accepted negative `collect_interval`

### GREEN

After implementation, ran:

```bash
cd apps/gpu-monitor
SECRET_KEY=baseline-test-key-1234 ADMIN_PASSWORD=baseline-test-password \
  python3.12 -m unittest backend.tests.test_live_database -v
```

Result: `Ran 12 tests ... OK`

Then ran backend regression coverage:

```bash
cd apps/gpu-monitor
SECRET_KEY=baseline-test-key-1234 ADMIN_PASSWORD=baseline-test-password \
  python3.12 -m unittest discover -s backend/tests -v
```

Result: `Ran 75 tests ... OK`

## Behavior change summary

- Added a SQLite-only preflight helper that:
  - parses SQLite file URLs safely, including query strings and percent-encoded paths
  - rejects non-file and in-memory SQLite URLs with `LiveDatabaseError`
  - rejects non-SQLite URLs when production preflight is enabled
  - runs `PRAGMA integrity_check`
  - reads registered servers in deterministic order
  - counts legacy notes without mutating schema
  - fails closed on missing DB, corrupt DB, zero registered servers, or fewer servers than expected
- Added online backup support using `sqlite3.Connection.backup()` into a temp file, then `os.replace()` for atomic publication.
- Backup files are forced to `0600` and older backup files beyond retention are removed.
- Added production settings:
  - `MONITORING_EXPECTED_SERVER_COUNT`
  - `MONITORING_DATABASE_BACKUP_DIR`
  - `MONITORING_DATABASE_BACKUP_KEEP`
- Added integer validation for existing polling/history settings and the new preflight settings.
- Moved SQLite parent-directory creation out of module import time and into `init_db()` so production preflight can fail before schema initialization or implicit DB creation.
- Wired `main.lifespan()` to run preflight in a worker thread before `init_db()` only when `MONITORING_EXPECTED_SERVER_COUNT > 0`.
- Legacy notes schema compatibility remains intact: preflight is read-only, and the existing `ensure_notes_expiry_schema_sync()` still upgrades the disposable legacy fixture while preserving nine server rows and the legacy note row.

## Validation performed

### Critical success path

- Legacy SQLite DB with nine registered servers and a legacy `notes` table:
  - preflight succeeded
  - backup file was created
  - follow-on schema sync added `display_name`, `priority`, `kind`, `gpu_indices`, and `expires_at`
  - all nine server rows and the legacy note row remained present

### High-risk failure paths

- Missing SQLite DB: startup preflight path now raises `LiveDatabaseError` before schema mutation.
- Corrupt SQLite DB: integrity check now raises `LiveDatabaseError` before schema mutation.
- Zero registered servers with expected count set: raises `LiveDatabaseError`.
- Real server count below expected count: raises `LiveDatabaseError`.
- Non-SQLite URL with expected count set: raises `LiveDatabaseError`.

## Auth / permissions / compatibility notes

- No auth or route permission logic changed.
- `Storage` was not touched.
- Existing development behavior remains unchanged unless operators set `MONITORING_EXPECTED_SERVER_COUNT > 0`.
- Existing note schema migration behavior remains unchanged after preflight.

## Residual risk / follow-up

- Backup creation only runs when `MONITORING_DATABASE_BACKUP_DIR` is configured; operators should set it in production before enabling `MONITORING_EXPECTED_SERVER_COUNT`.
- Relative SQLite paths still resolve relative to the process working directory; the default repository configuration uses an absolute path, and production should prefer absolute `DATABASE_URL` values.
- Backend regression output still contains pre-existing `datetime.utcnow()` deprecation warnings in `backend/collectors/gpu.py`; this task did not change that code path.
