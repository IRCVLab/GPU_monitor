# Security Policy

## Supported scope

Security review covers both independent applications in this repository:

- `apps/gpu-monitor`
- `apps/storage-monitor`

## Secrets and data handling

Do not commit secrets, `.env` files, private keys, live inventory, runtime JSON snapshots, databases, caches, virtual environments, dependency directories, or browser output. Use only reviewed privacy-safe sample fixtures for Storage demos.

## Deployment status

Production deployment is not enabled by this foundation plan. Treat any production rollout as a separate, reviewed authorization after the later `main` merge decision.

## Reporting

Report suspected credential exposure, data leakage, unsafe deployment behavior, or cross-application boundary violations to the repository maintainers immediately. Include affected paths, commands run, and whether any generated or collected data was observed in Git.
