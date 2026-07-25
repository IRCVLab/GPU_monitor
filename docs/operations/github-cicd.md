# GitHub CI/CD and GPU Live release

This repository supports local development, optional pull requests, and trusted-team direct pushes to `main`. GPU Live deployment is outbound-only from the server.

The current release contract is:

`local development -> optional PR or trusted direct main push -> main CI -> outbound server puller -> exact successful SHA live activation`

## Current GPU Live deployment contract

GPU Live is not deployed by a GitHub-hosted runner opening SSH to the server. The server owns the release loop:

1. `gpu-monitor-release-puller.timer` uses a persistent five-minute calendar cadence with bounded jitter after an operator explicitly enables it.
2. `gpu-monitor-release-puller.service` calls the public GitHub API over outbound HTTPS only.
3. The puller reads the current `main` SHA, matching `ci.yml` push/main workflow evidence, and check-run evidence.
4. The puller reuses the canonical `scripts/authorize_gpu_release.py` installed on the server.
5. Authorization requires successful `ci/required` for the exact current `main` SHA and fails closed if `main` changes during evidence collection.
6. A dedicated non-login `gpu-monitor-builder` user creates a clean exact-SHA checkout and builds the immutable GPU artifact.
7. The root puller validates artifact digest/manifest evidence, then invokes local activation as `gpu-deploy-live`.
8. Local activation runs the existing upload, activate, and status path for Live.
9. After an authorized SHA fails during checkout, build, upload, or activation, the puller persists `failed-release.json` and exponentially backs off retries from 15 minutes up to 6 hours. A new `main` SHA clears that failure state automatically.
10. If `main` changes or final authorization fails after the inactive upload, the puller discards that exact inactive object under the activation lock so abandoned candidates cannot consume the incoming quota.

The local activation sequence is:

```text
upload live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
activate live <40-lowercase-hex-sha> <64-lowercase-hex-sha256>
status live
```

Failed CI, missing `ci/required`, public API failure, authorization denial, changed `main`, checkout/build failure, artifact validation failure, activation failure, or health-check failure leaves the current Live release unchanged.

The failure backoff prevents a deterministic bad release from repeatedly restarting Live every five minutes. For an intentional manual retry of the unchanged SHA, remove `/var/lib/gpu-monitor/puller/failed-release.json` and start `gpu-monitor-release-puller.service`; otherwise the next eligible retry happens automatically. Do not remove `current-live-sha`.

## Development and branch policy

Local development is the default. Pull requests are optional when review is useful. Trusted repository writers may push directly to `main`; this is a trust policy for a small team, not a defense against malicious or compromised trusted writers.

A direct `main` push can become deployable only after the server independently observes successful `ci` and successful `ci/required` for that exact SHA. A failed `main` CI run may remain in Git history but does not change Live.

When the GitHub plan allows enforceable branch protection, the stronger future control is protected `main` with required review, CODEOWNER review, required `ci/required`, administrator enforcement, and no force pushes.

## Runner, secret, and transport policy

- Pull-request CI uses GitHub-hosted runners.
- GPU Live deployment does not use GitHub-hosted inbound SSH.
- GPU Live deployment does not use self-hosted runners.
- GPU Live deployment does not require `gpu-live` GitHub environment secrets.
- The old `.github/workflows/deploy-gpu-live.yml` GitHub-hosted SSH workflow has been removed. The obsolete `gpu-live` GitHub environment/secrets are deleted after outbound rollout verification.
- Server communication with GitHub is outbound HTTPS to the public GitHub API.
- Server Live activation is local and uses the existing `activate-release.sh` path as `gpu-deploy-live`.
- Secret values stay in GitHub or server secret stores; never commit them or include them in runbooks, logs, artifacts, or reports.

## Installer and operator enablement

The server installer installs release assets, the puller, the canonical authorizer, and systemd units. It does not enable or start the puller timer/service.

After installation, the operator must verify at least:

- installed `gpu-monitor-release-puller.py`, `activate-release.sh`, and `authorize_gpu_release.py` paths;
- `gpu-monitor-builder` is a dedicated non-login builder and cannot read `/etc/gpu-monitor/live.env`;
- `gpu-deploy-live` remains the Live activation identity;
- `/etc/gpu-monitor/live.env` contains required Live runtime secrets and server-local settings;
- the managed Node runtime is present;
- manual emergency `status live` and `rollback live` are available if the SSH forced-command key is retained;
- `gpu-monitor-release-puller.service` and `gpu-monitor-release-puller.timer` are installed but not active until explicitly enabled.

Only after verification should the operator enable the polling path, for example:

```bash
sudo systemctl enable --now gpu-monitor-release-puller.timer
```

That command is an operator action on the server; it is not run by the installer and was not run during documentation updates.

## Emergency status and rollback

The existing SSH forced-command wrapper may remain for manual emergency operations. It is not the GitHub automatic deployment transport.

Supported emergency forms:

```text
status live
rollback live
```

Rollback is local to GPU Live. Storage is not rolled back or restarted by GPU Live rollback.

## Storage boundary

GPU and Storage are independent products. The GPU Live puller deploys only GPU Monitor. Storage agents and dashboards remain manual/tagged or app-local according to their own operational runbooks, and must not auto-deploy from GPU `main` release polling.

A failed GPU activation must not roll back Storage. Shared repository governance changes are not a runtime rollback mechanism.

## Legacy and migration notes

`scripts/check_deploy_prerequisites.py` is the legacy branch-protected/self-hosted readiness model. It reports repository protection, CODEOWNER enforcement, runner availability, and server reachability status, but it is not the current GPU Live authorization gate.

The previous GitHub-hosted SSH deployment design attempted to build in GitHub Actions and connect inbound to the GPU server. That path failed operationally because the campus firewall blocked inbound SSH from GitHub-hosted runners. The outbound-only puller removes the blocked network dependency: the server initiates outbound HTTPS to GitHub, builds locally, and activates locally.

Migration procedure:

1. Keep local development and optional PR/direct-main CI as the source integration path.
2. Install outbound puller assets on the server without enabling or starting the timer/service.
3. Verify builder isolation, canonical authorizer installation, Live runtime secrets, managed Node, and manual emergency rollback/status.
4. Confirm `.github/workflows/deploy-gpu-live.yml` is absent and remove the obsolete `gpu-live` environment secrets/environment after outbound rollout verification.
5. Explicitly enable/start `gpu-monitor-release-puller.timer` only after operator verification.
6. Observe the first real Live activation from a successful `main` SHA and record only actual evidence.

Rollout is not yet recorded as complete in this documentation set.
