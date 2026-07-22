# IRCV Monitoring Platform Monorepo and Delivery Design

## Status

Approved architecture direction: one repository containing two independently deployable monitoring applications.

This document defines repository boundaries, history migration, continuous integration, automatic deployment, rollback, documentation, and operational isolation. It does not merge the GPU and Storage products into one runtime or one backend.

## Goals

1. A contributor can clone one repository, create a branch, modify either monitor, run documented checks, and open a pull request.
2. A change merged to main is automatically validated and deployed to the affected central service without a separate deployment approval.
3. GPU Monitor and Storage Monitor retain independent builds, processes, ports, configuration, health checks, versions, releases, and rollback paths.
4. Existing GPU and Storage commit history is preserved in the new repository.
5. The live GPU service remains available during migration and is not modified by ordinary development edits.
6. Shared visual language is maintained from one source without forcing both applications into one frontend framework.
7. Secrets, runtime snapshots, scan output, and machine-specific configuration never enter Git.

## Non-goals

- Combining GPU and Storage APIs, databases, or backend processes.
- Rewriting both products into one framework.
- Replacing application behavior during repository migration.
- Automatically running untrusted pull-request code on the production host.
- Deploying privileged Storage agents to every monitored server on every ordinary UI commit.
- Introducing Nx, Turborepo, Kubernetes, or another orchestration layer without a demonstrated need.

## Current-State Findings

### GPU Monitor

- Live source: /home/ircv/workspace/monitoring_v2 on main.
- Development source: /home/ircv/workspace/monitoring_v2_dev on feature/compact-gpu-dashboard.
- The development remote currently points to the live directory rather than GitHub.
- Live and development trees have diverged.
- Production and development processes are launched through long-lived tmux sessions.
- No GitHub Actions workflows or declarative production service manifests exist.
- Local environment files are ignored, but deployment depends on host-local virtual environments, Node modules, and manually built assets.

### Storage Monitor

- Active source: feature/multiserver-storage-dashboard in the local Storage repository.
- The central dashboard, scanner agent, deployment templates, and documentation already have visible boundaries.
- No GitHub remote or automatic CI/CD pipeline is configured.
- Dashboard deployment and per-host scanner deployment are operationally different and remain separate release targets.

### Deployment host

- Ubuntu 22.04, Docker, systemd, Git, Python, and outbound HTTPS access to GitHub are available.
- No GitHub Actions self-hosted runner is installed.
- The default host Node.js is old and is not the build toolchain source of truth.
- The ircv account does not have passwordless sudo.
- Live and development tmux services coexist, so cutover includes explicit port and process ownership checks.

## Chosen Architecture

Use a hybrid monorepo: one collaboration and source-control boundary, multiple independent application and deployment boundaries.

    IRCV-monitoring/
    ├── apps/
    │   ├── gpu-monitor/
    │   │   ├── frontend/
    │   │   ├── backend/
    │   │   ├── tests/
    │   │   ├── scripts/
    │   │   ├── README.md
    │   │   └── .env.example
    │   └── storage-monitor/
    │       ├── viewer/
    │       ├── scanner/
    │       ├── agent/
    │       ├── collector/
    │       ├── tests/
    │       ├── scripts/
    │       ├── README.md
    │       └── .env.example
    ├── packages/
    │   └── design-tokens/
    ├── deploy/
    │   ├── gpu-monitor/
    │   ├── storage-dashboard/
    │   ├── storage-agent/
    │   └── shared/
    ├── docs/
    ├── .github/
    │   ├── CODEOWNERS
    │   └── workflows/
    ├── CONTRIBUTING.md
    ├── SECURITY.md
    ├── LICENSE
    └── README.md

The existing private repository IRCVLab/GPU_monitor can be used initially. A later rename to IRCVLab/monitoring-platform is recommended but not required for migration.

## Boundary Rules

### GPU Monitor owns

- GPU collection and availability semantics.
- GPU frontend and FastAPI backend.
- Slack bridge behavior.
- GPU-specific tests, configuration, ports, services, and release history.

### Storage Monitor owns

- Storage dashboard and API.
- Scanner and collector behavior.
- Per-host agent installation and scheduling.
- Storage-specific tests, configuration, services, snapshots, and release history.

### Shared area owns only

- Semantic colors and material tokens.
- Typography, spacing, radius, elevation, and motion tokens.
- Shared SVG assets.
- Product navigation conventions.
- CI shell helpers with no application-specific behavior.
- Documentation templates and operational conventions.

Shared application state, backend models, API clients, and framework-specific UI components are prohibited initially. A shared component package may be introduced only after compatible runtime contracts and repeated duplication demonstrate real maintenance cost.

## Git History Migration

Create immutable checkpoint tags before restructuring and import this exact ref matrix:

| Source | Required ref | Monorepo destination |
| --- | --- | --- |
| GPU live | main at the checkpoint commit | archive/gpu-live and tag pre-monorepo-gpu-live |
| GPU development | feature/compact-gpu-dashboard at the checkpoint commit | main integration source under apps/gpu-monitor |
| GPU remaining branches | every local branch and tag recorded during inventory | archive/gpu/<original-ref> |
| Storage active | feature/multiserver-storage-dashboard at the checkpoint commit | main integration source under apps/storage-monitor |
| Storage remaining branches | every branch and tag recorded during inventory | archive/storage/<original-ref> |

Migration procedure:

1. Export a signed inventory containing every source ref, commit identifier, commit count, author list, and tag.
2. Clone each existing repository into a read-only local mirror and create immutable checkpoint tags in those mirrors, leaving live and development working repositories unchanged.
3. Publish the unrewritten archival refs and checkpoint tags into the isolated local target repository before any path rewrite. GitHub publication occurs only after all foundation gates pass.
4. Import GPU development under apps/gpu-monitor and Storage active under apps/storage-monitor.
5. Preserve source histories using temporary clones and path-prefix history rewriting, then merge rewritten histories.
6. Keep divergent GPU live history reachable through its archival branch and checkpoint tag.
7. Record original-to-rewritten commit mappings in docs/history-migration.md.
8. Verify source and imported commit counts, authorship, timestamps, tags, and representative file history before accepting migration.
9. Never import environment files, runtime snapshots, generated scan data, virtual environments, build output, Node modules, Playwright artifacts, or tmux state.

History rewriting changes commit identifiers but preserves commit contents, authorship, timestamps, and ordering. Original repositories remain read-only archives until migration and rollback are verified.

## Branch and Contribution Model

- main is the only production source of truth.
- Contributors work on feature, fix, or documentation branches.
- Pull requests run affected checks on GitHub-hosted runners.
- main rejects force pushes and deletions.
- Required status checks pass before merge.
- Deployment has no separate human approval after merge.
- A merge to main is the explicit production decision and automatically starts affected deployment jobs.
- Private repository access still controls who can clone and push. External contributors require approved organization access or a supported fork policy.
- A local commit cannot deploy anything. Deployment begins only after the commit is pushed and accepted into main.

## CI Selection

GPU checks run for:

- apps/gpu-monitor
- packages/design-tokens
- deploy/gpu-monitor
- shared CI or root configuration affecting GPU

Storage checks run for:

- apps/storage-monitor
- packages/design-tokens
- deploy/storage-dashboard
- deploy/storage-agent
- shared CI or root configuration affecting Storage

Documentation-only changes do not deploy applications. Shared token changes validate both frontends, while each application still deploys independently only after its own checks pass.

A single required affected-dispatch status always runs. It computes GPU, Storage dashboard, Storage agent, shared, workflow, and documentation impact and emits explicit test and deploy decisions. Branch protection requires this dispatcher rather than optional path-filtered jobs that may remain skipped. Changes to workflows, root dependency configuration, deploy code, or the dispatcher itself are treated conservatively and validate all potentially affected targets.

## Runner and Trust Model

Use two runner classes:

1. GitHub-hosted runners for pull-request linting, unit tests, static checks, and builds.
2. A repository-scoped, deploy-only self-hosted runner on the central deployment host for trusted main deployment jobs.

The production runner:

- never runs pull_request workflows;
- preferably belongs to an organization-level runner group restricted to this repository and the exact deployment workflow files;
- if the GitHub organization plan cannot enforce workflow-restricted runner groups, uses a repository-level runner dedicated to this repository together with mandatory operator review for every workflow and deploy-path change;
- accepts only deployment jobs produced after protected-main CI;
- runs under a dedicated unprivileged operating-system account;
- cannot execute arbitrary repository shell as root;
- invokes one host-owned, root-owned, non-writable allowlisted deploy command per application;
- writes only to application release directories;
- restarts only allowlisted systemd units through exact sudoers command entries;
- receives no SSH password, broad root key, or reusable administrator credential;
- uses concurrency groups so only one deployment per application runs at a time.

Repository controls:

- CODEOWNERS covers .github/workflows, deploy, root dependency locks, and runner configuration.
- Changes to those paths require review from designated operators even when ordinary application changes do not.
- Actions are pinned to immutable commit identifiers.
- Each workflow declares minimal permissions and defaults to read-only contents access.
- pull_request_target is prohibited for application build or execution.
- Production GitHub Environments have no required human reviewer because merge to protected main is the deployment authorization.
- Runtime secrets remain host-side where practical; GitHub secrets expose only the minimum values required by a named deploy job.
- A required security test proves pull-request workflows cannot select the production runner labels.
- Runner-group and workflow restrictions are verified against the IRCVLab GitHub organization plan before registration; unavailable controls are not claimed as security boundaries.

If workflow restriction, mandatory operator review, or narrowly scoped restart permission cannot be enforced, automatic production deployment is blocked and the existing script-driven process remains in place. Passwords are never embedded in workflows.

## Build and Artifact Flow

1. A trusted main workflow checks the affected application.
2. Build jobs use pinned Node and Python versions rather than host defaults.
3. CI produces immutable artifacts identified by commit SHA.
4. Artifacts include application code and built assets but exclude environment files and runtime data.
5. The deploy-only runner downloads the exact successful artifact.
6. The deployment script validates artifact checksums and required configuration before activation.

The production host does not build from a mutable working tree and does not pull Git directly inside the active service directory.

## Atomic Deployment

Use application-specific release directories.

    /srv/ircv-monitoring/
    ├── gpu-monitor/
    │   ├── releases/<commit-sha>/
    │   ├── current
    │   └── previous
    └── storage-dashboard/
        ├── releases/<commit-sha>/
        ├── current
        └── previous

Configuration and state remain outside releases.

    /etc/ircv-monitoring/gpu-monitor.env
    /etc/ircv-monitoring/storage-dashboard.env
    /var/lib/ircv-monitoring/gpu-monitor/
    /var/lib/ircv-monitoring/storage-dashboard/

Deployment sequence:

1. Acquire the application deployment lock.
2. Extract the artifact into a new release directory.
3. Install or verify runtime dependencies in that release.
4. Validate configuration without printing secret values.
5. Validate the new release before activation.
6. Atomically switch the application current link.
7. Restart the complete application deployment unit.
8. Run process, HTTP, API, and data-readiness health checks.
9. Keep the previous release and mark the deployment successful.
10. On failure, restore the previous link, restart the complete previous application unit, verify every component, and fail the workflow visibly.

GPU is one release set containing frontend, backend, and Slack bridge. Deployment restarts them in dependency order, validates all three, and rolls all three back together. Mixed GPU component versions are never accepted as a successful deployment. This can be implemented with one systemd target plus ordered component units or an equivalent host-owned orchestration command.

Storage dashboard is a separate release set. GPU deployment never restarts Storage. Storage dashboard deployment never restarts GPU.

## Production Process Management

Production moves from tmux to separate systemd units:

- ircv-gpu-backend.service
- ircv-gpu-frontend.service
- ircv-gpu-slack-bridge.service
- ircv-storage-dashboard.service
- storage scan services and timers remain separate from dashboard lifecycle

Tmux remains acceptable for development only. Existing live tmux processes are not removed until corresponding systemd services pass cutover checks and rollback has been tested.

The GPU cutover runbook must document and test pre-cutover restoration:

1. Stop the new GPU systemd deployment unit.
2. Verify production ports are released.
3. Start the untouched /home/ircv/workspace/monitoring_v2 tmux stack with its original environment.
4. Verify frontend, backend, Slack bridge, and data freshness.
5. Confirm the monorepo deployment never mutates the old live directory or its environment.
6. Keep this path available for a defined rollback window before archival cleanup.

## Storage Agent Release Policy

The central Storage dashboard follows automatic main deployment.

Per-host scanner agents have higher operational impact and privileged filesystem access. They use a separate versioned target:

- tags such as storage-agent-v0.2.0;
- validation against fixtures and one explicitly configured canary host;
- an automated canary health gate before promotion;
- sequential rollout to monitored hosts;
- host-level timeout and rollback;
- no scanner restart for dashboard-only changes;
- a distinct least-privilege service account and exact sudoers entries on each host;
- per-host or narrowly scoped credentials rather than one broad root key;
- a read-only scan contract that cannot delete or mutate monitored data;
- restoration of the previous agent package and timer configuration on failure.

This keeps UI changes fast while preventing an accidental repository change from launching privileged scans across every server simultaneously.

## Versioning

Applications use independent tags:

- gpu-v1.0.0
- storage-dashboard-v0.5.0
- storage-agent-v0.2.0

A repository-wide release is not required. Deployment records identify application, commit, environment, result, and rollback status.

## Configuration and Secrets

- Commit complete example environment files containing names, descriptions, and safe defaults only.
- Validate required variables at startup.
- Store production values under /etc/ircv-monitoring with restrictive permissions.
- Store GitHub deployment credentials as environment or runner secrets.
- Never commit SSH passwords, Slack tokens, signing secrets, admin passwords, session secrets, collected host snapshots, or user filesystem paths.
- Add secret scanning and generated-data checks to CI.
- Document credential rotation and service-account ownership.

## Documentation Set

Root documentation includes:

- product overview and repository map;
- clone, bootstrap, and local development;
- branch and pull-request workflow;
- affected-area test commands;
- GPU and Storage startup instructions;
- port map and environment setup;
- CI/CD architecture and runner setup;
- first deployment, rollback, and disaster recovery;
- service status, logs, health endpoints, Storage scan status, and troubleshooting.

## Error Handling and Observability

Each deploy records application, source commit, artifact checksum, previous release, timing, health output, and rollback result.

Deployment failures leave the last healthy release active. A failed Storage agent rollout stops before later hosts. Logs redact secrets and collected filesystem data.

Health checks distinguish process health from data freshness. HTTP success alone is insufficient when required configuration or state is unreadable.

## Test Strategy

Repository migration checks:

- both applications build and run from new paths;
- commit history and authorship remain accessible;
- fixture and test assets are preserved;
- no secrets, runtime data, or generated output are tracked.

GPU CI:

- Python tests;
- frontend unit tests;
- Svelte static checks;
- production build;
- API smoke test;
- browser smoke tests for views and theme persistence.

Storage CI:

- scanner and collector tests;
- fixture and schema validation;
- viewer tests;
- deployment-script tests;
- browser smoke tests for server switching, rescan, treemap selection, and stale-data protection.

Deployment and security checks:

- install a new release;
- validate artifact checksum mismatch rejection;
- validate configuration failure without secret leakage;
- verify health success and intentional health failure rollback;
- verify GPU frontend, backend, and Slack bridge roll forward and back as one release set;
- verify concurrent deployment serialization;
- verify GPU-only changes do not restart Storage;
- verify Storage-only changes do not restart GPU;
- verify shared-token changes validate both;
- verify a malicious pull-request workflow cannot select or execute on the production runner;
- verify workflow and deploy paths require CODEOWNER review;
- verify root-owned deploy commands reject non-allowlisted arguments;
- verify Storage canary failure prevents later-host rollout;
- verify old tmux services cannot retain conflicting production ports after cutover;
- verify the original GPU live directory can restore service during the rollback window.

## Migration Plan

### Phase 0: establish a green, isolated checkpoint

- Confirm clean relevant worktrees.
- Commit all source and documentation changes.
- Fix the existing GPU baseline blockers as isolated commits: align Vite and the Svelte plugin so plain npm ci succeeds, add the missing greenlet runtime dependency, and replace expired fixed test timestamps with dynamic future values.
- Re-run GPU frontend check/build and backend tests from clean environments.
- Re-point the GPU development remote away from the live filesystem repository before any push.
- Freeze the live GPU repository as a read-only checkpoint source; do not change its working tree, branch, or runtime.
- Tag current GPU live, GPU development, and Storage active states in read-only local mirrors.
- Export the complete source-ref inventory and later copy those checkpoint tags into the isolated target repository.
- Run full-history secret and generated-data scans before any rewrite. If sanitization is required, combine it with the single planned history rewrite.

### Phase 1: assemble monorepo

- Create the isolated integration branch.
- Import GPU and Storage histories under application prefixes.
- Add root documentation and ownership boundaries.
- Verify ref counts, authorship, tags, mappings, and representative file history.
- Publish archival refs and the rewritten integration history while repository access remains restricted.
- Enable branch protection and CODEOWNER enforcement only after the initial history replacement succeeds.
- Make no application behavior or visual changes.

### Phase 2: normalize monorepo builds

- Make each application runnable from its own directory.
- Pin toolchain versions.
- Add root convenience commands that delegate locally.
- Lock current behavior with existing and migration tests.

### Phase 3: CI and runner-policy gate

- Verify the IRCVLab GitHub organization plan can enforce the chosen runner-group and workflow restrictions.
- Add the always-running affected-dispatcher required check.
- Add path-scoped pull-request workflows.
- Add ongoing secret and generated-data checks.
- Require successful affected checks on main.
- Do not register a production runner until the trust controls are proven enforceable.

### Phase 4: central-service releases

- Add systemd templates and atomic deployment scripts for GPU Monitor and the Storage dashboard.
- Install the deploy-only runner and narrowly scoped restart permission.
- Check existing dev-port ownership before deploying to development ports.
- Exercise whole-application rollback for GPU and independent rollback for Storage dashboard.

### Phase 5: central-service production cutover

- Deploy GPU and Storage central services independently.
- Verify health and browser behavior.
- Retain old tmux stacks stopped but recoverable for a bounded rollback window.
- Remove obsolete processes only after the rollback window closes.

### Phase 6: Storage agent release pipeline

- Implement the tag-triggered canary and sequential agent rollout after central services are stable.
- Validate per-host least privilege, timeout, rollback, and no-mutation scan contracts.
- Do not block the central dashboard monorepo cutover on this later release target.

### Phase 7: shared design tokens

- Extract verified semantic tokens only after both applications are stable.
- Validate each application visually.
- Do not refactor behavior during extraction.

## Acceptance Criteria

- One clone contains both products and complete contributor documentation.
- Each product starts, tests, builds, deploys, and rolls back independently.
- A GPU-only merge cannot restart or modify Storage runtime state.
- A Storage-only merge cannot restart or modify GPU runtime state.
- A successful merge to main automatically deploys the affected central service without deployment approval.
- Failed health checks automatically restore the previous release.
- Pull-request code never executes on the production runner.
- Workflow and deploy changes require operator CODEOWNER review.
- GPU frontend, backend, and Slack bridge deploy and rollback as one release set.
- Exact source ref inventories and commit mappings prove both divergent GPU histories and Storage history remain accessible.
- No production secret or runtime scan snapshot is tracked.
- The untouched pre-cutover GPU tmux stack is tested as a recovery path for the documented rollback window.
