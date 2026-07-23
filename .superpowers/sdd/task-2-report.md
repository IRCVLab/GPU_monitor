# Task 2 Report: Deterministic GPU Release Authorization

## Status
Implemented deterministic fail-closed release authorization core in `scripts/authorize_gpu_release.py`, tests in `tests/test_authorize_gpu_release.py`, and root Makefile wiring for `release-auth-test` under `make test`.

## TDD Evidence
- RED: `PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_authorize_gpu_release -v` failed with `ModuleNotFoundError: No module named 'scripts.authorize_gpu_release'` before implementation.
- GREEN: focused tests pass after implementation.

## Verification
- `python3.12 scripts/authorize_gpu_release.py --repository IRCVLab/GPU_monitor --workflow-run-file <event> --pulls-file <pulls> --reviews-file <reviews> --checks-file <checks>` produced authorized JSON and exit 0 for deterministic fixture mode.
- `make release-auth-test` passed: 8/8 authorization tests.
- `make test` passed: repository layout, history inventory, CI impact, workflow policy, deploy readiness, and release authorization tests.
- `git diff --check` passed.

## Self-review
- Authorization fails closed on malformed inputs and CLI/live read/JSON/schema errors.
- Live mode uses read-only `gh api` calls to fetch associated PRs, PR reviews, and check runs for the workflow head SHA.
- Authorization requires completed successful push workflow on `main` from the expected repository, exactly one merged PR targeting `main`, latest effective non-author approval, and completed successful `ci/required` on the exact final main SHA.
- The final main SHA is intentionally independent of the PR head SHA in tests and implementation.

## Concerns
None known.

## Review Fix Follow-up

### Changes
- Enforced strict merge evidence: `merged_at` must be a non-empty string, or `merged` must be exactly `true`; malformed merge evidence denies with `malformed_input`.
- Added complete live pagination with `gh api --paginate --slurp`; list pages are flattened for pulls/reviews and object pages are flattened through `check_runs` for checks.
- Made duplicate matching `ci/required` runs deterministic by selecting the latest `completed_at`, then numeric `id`, and failing closed on malformed or ambiguous duplicate ordering.
- Casefolded PR author/reviewer comparisons and per-reviewer latest-review keys.
- Validated repository as `OWNER/REPO` and final SHA as exactly 40 lowercase hex before live API path construction.
- Kept `gh` invocation shell-free and read-only using argument-list `subprocess.run`.

### Regression Tests Added
- Malformed merge evidence and explicit `merged: true` fallback.
- Paginated live review pages where old approval is superseded by later `CHANGES_REQUESTED`/`DISMISSED`.
- Paginated live pull pages where a second merged PR appears on a later page.
- Old successful required check superseded by newer failed check, plus malformed duplicate ordering.
- Case-insensitive author/reviewer matching and per-reviewer effective review keys.
- Invalid final SHA and repository validation before live API paths.

### Verification
- RED: new regression tests failed before implementation for permissive merge evidence, one-page live reads, first-success check selection, case-sensitive identity comparisons, and missing SHA/repository validation.
- GREEN: `PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_authorize_gpu_release -v` passed 17/17.
- `python3.12 -m py_compile scripts/authorize_gpu_release.py tests/test_authorize_gpu_release.py` passed.
- `make release-auth-test` passed 17/17.
- `make test` passed all root test targets, including release authorization.
- `git diff --check` passed.

### Concerns
None known.
