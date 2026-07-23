# Task 2 Report

Status: DONE

## Red evidence
- Initial Task 2 RED: `PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy -v` failed before implementation because `scripts/validate_workflows.py` did not exist.
- Preflight RED: after adding Kimi K3 edge-case tests, the same focused command failed on unsupported YAML/no-jobs handling, explicit missing/empty directory messaging, expanded production label denylist, per-scope write permissions, PR matrix runner ambiguity, and inline mapping `pull_request_target` detection.

## Green evidence
- `PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy -v` passed: 17 tests.
- `make test` passed, including `policy-test` and real `.github/workflows` validation. Current repository has no workflow directory, reported explicitly as missing.
- `git diff --check` passed.

## Parser policy definitions
- Dependency-free parser supports the constrained scalar/list/mapping forms covered in tests and fails closed on malformed lines, top-level YAML lists, empty workflow files, and workflows without jobs.
- Every step-level or job-level reusable-workflow `uses:` value must end exactly with `@` plus 40 lowercase hexadecimal characters.
- `pull_request_target` is rejected in scalar, list, nested mapping, inline mapping, and quoted key forms.
- Pull-request workflows reject `self-hosted` runners in scalar/list forms and reject dynamic/matrix-selected runners as ambiguous.
- Deploy jobs are identified by `deploy` in the job id or job-level `name`; only exact job-level `github.ref == 'refs/heads/main'` or `github.ref_name == 'main'` guards pass.
- Production runner label denylist: `prod`, `production`, `prd`, `prod-runner`; these labels are allowed only on deploy jobs.
- Top-level and job-level `permissions: write-all` and per-scope `*: write` permissions are rejected.
- Missing and empty workflow directories are explicit success cases to preserve Task 2's "before a real workflow exists" requirement.

## Security review fix: ambiguous YAML policy syntax

Status: DONE

### Red evidence
- `python3.12 -m unittest tests.test_workflow_policy` failed with 10 expected failures after adding regressions for anchored/aliased `on`, top-level `permissions`, job-level `permissions`, job-level `runs-on`, and mapping-form `runs-on` exploits. The failures showed those ambiguous forms previously returned `OK: workflow policy validated 1 workflow file(s)`.

### Green evidence
- `PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy` passed: 20 tests.
- `make test` passed, including `policy-test` and `scripts/validate_workflows.py .github/workflows`.
- `git diff --check` passed.

### Fix summary
- Added fail-closed `unsupported-yaml-anchor-alias` violations for YAML anchor/alias tokens, with job attribution when the unsupported syntax appears inside a job block.
- Added explicit `unsupported-runs-on-mapping` rejection for direct job-level `runs-on: {...}` mapping forms, including `{labels/self-hosted}` and `{labels: [self-hosted, linux]}`.
- Preserved accepted SHA-pinned action/reusable workflow forms and existing valid scalar/list workflow forms under the expanded regression suite.

### Addendum: block runs-on mappings
- Added RED/GREEN coverage for block-style `runs-on:` mappings with child `labels: [self-hosted, linux]`; this now fails closed under `unsupported-runs-on-mapping` alongside inline mapping forms.
