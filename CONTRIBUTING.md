# Contributing

This monorepo preserves two independent products. Keep changes scoped to the application that owns the behavior unless a root governance or migration task explicitly changes the boundary.

## Development workflow

1. Work in an isolated branch or worktree.
2. Run app-local setup and tests from the app directory you changed.
3. Run root repository contracts before proposing integration:

   ```bash
   python3.12 -m unittest tests.test_repository_layout tests.test_history_inventory -v
   git diff --check
   ```

4. Do not commit generated/runtime data: `.env`, virtual environments, `node_modules`, caches, databases, runtime JSON snapshots, or browser/test output.
5. Do not add cross-imports between `apps/gpu-monitor` and `apps/storage-monitor`.

## Deployment boundary

Deployment remains application-specific. The GPU release path validates the PR head SHA through CI and shared development-server validation, then a reviewed GitHub merge to `main` is the production deployment authorization point. The resulting main SHA may differ from the PR head SHA, so fresh `ci/required` success, merged-PR provenance, and effective approval verification are required before building and deploying the exact successful main SHA.

Pull-request and deployment workflows use GitHub-hosted runners. Self-hosted production runners remain disabled while branch protection is unavailable. The deployment credential must be environment-scoped and accepted only by a server-side forced-command wrapper that cannot execute arbitrary repository-provided shell. These checks are trusted-team safeguards against accidental direct-push deployment, not a substitute for branch protection against a malicious authorized writer.
