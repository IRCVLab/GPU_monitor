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

Deployment remains application-specific. In the supported policy, local development is the default path:

`local development -> optional PR or direct main push -> main CI -> exact successful SHA live deployment`

Contributors may use optional pull requests or push directly to `main`. A successful same-repository `main` CI determines the exact SHA to deploy, and only that SHA is deployed to live.

Pull requests are optional. A failed `main` CI does not change the current live release, even though the failed commit remains in Git history.

Pull-request and deployment workflows use GitHub-hosted runners. Self-hosted production runners remain disabled while branch protection is unavailable. The deployment credential must be environment-scoped and accepted only by a server-side forced-command wrapper that cannot execute arbitrary repository-provided shell. Direct-main automatic deployment is a trusted-team policy: trusted writers can modify candidate code, CI, workflows, and the authorizer, so these repository-side checks reduce accidents but are not protection against malicious or compromised trusted writers. Branch protection with required review is the stronger future control when the plan allows it.
