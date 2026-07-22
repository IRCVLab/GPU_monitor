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

Deployment remains application-specific and is not activated by this foundation repository state. A later reviewed merge to `main` is the production deployment authorization point.
