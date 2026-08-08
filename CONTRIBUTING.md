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

Deployment remains application-specific. Local development is the default path:

`local development -> optional PR or trusted direct main push -> main CI -> outbound server puller -> exact successful SHA live activation`

Pull requests are optional. Trusted team members may push directly to `main`; this is a team trust policy, not a defense against malicious or compromised trusted writers. Branch protection with required review remains the stronger future control when the GitHub plan allows it.

GPU Live does not use GitHub-hosted inbound SSH deployment, `gpu-live` deployment secrets, or self-hosted runners. The server runs a five-minute systemd timer, calls the public GitHub API, requires successful `ci/required` for the exact current `main` SHA through `scripts/authorize_gpu_release.py`, builds from a clean exact-SHA checkout as the dedicated non-login builder, and activates locally as `gpu-deploy-live`. Failed CI, a changed `main`, failed authorization, failed build, or failed activation leaves Live unchanged; authorized release failures use persistent exponential retry backoff.

The Live database floor is explicit production configuration: `MONITORING_EXPECTED_SERVER_COUNT=9`, `MONITORING_DATABASE_BACKUP_DIR=/var/lib/gpu-monitor/live/backups`, and `MONITORING_DATABASE_BACKUP_KEEP=5`. First promotion validates a candidate copy from a disposable online backup with collectors and Slack disabled before any managed cutover.

Storage is not part of the GPU Live deployment path. A Storage-only change cannot restart GPU Live. The old forced-command SSH wrapper may remain for manual emergency `status live` or `rollback live`, but it is not the GitHub automatic deployment transport.
