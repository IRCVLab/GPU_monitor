#!/usr/bin/env python3
"""Outbound-only storage dashboard release puller."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from storage_dashboard_release import (
    AGENT_BUNDLE_FILES,
    APPLICATION_NAME,
    ARTIFACT_FORMAT_VERSION,
    SCHEMA_VERSION,
    ActivationError,
    inspect_archive,
)


SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API_BASE = "https://api.github.com"
ARTIFACT_PREFIX = "storage-monitor-dashboard"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
AGENT_ARTIFACT_PREFIX = "viz-agent-bundle"
AGENT_BUNDLE = "storage-viz-agent.tar.gz"
AGENT_SIGNATURE = "storage-viz-agent.tar.gz.sig"
AGENT_METADATA = "storage-viz-agent.json"
AGENT_NAMESPACE = "storage-viz-agent"
AGENT_SIGNER = "storage-viz-release"
MAX_AGENT_ZIP_BYTES = 80 * 1024 * 1024
MAX_AGENT_FILE_BYTES = {AGENT_BUNDLE: 64 * 1024 * 1024, AGENT_SIGNATURE: 4096, AGENT_METADATA: 4096}
# After ci passes, agent-bundle.yml may take this long to appear before the
# puller stops waiting for it and keeps the previous agent bundle.
AGENT_RUN_GRACE_SECONDS = 1800
UNCHECKED = object()


class PullError(RuntimeError):
    pass


class CiPending(PullError):
    """main's ci.yml run for the SHA has not finished (or not started) yet: wait, nothing failed."""


@dataclass(frozen=True)
class Config:
    repository: str = "IRCVLab/GPU_monitor"
    state_dir: Path = Path("/var/lib/storage-viz-dashboard/puller")
    work_dir: Path = Path("/var/lib/storage-viz-dashboard/builder")
    authorizer: Path = Path("/usr/local/libexec/storage-release-authorizer.py")
    activate: Path = Path("/usr/local/libexec/storage-dashboard-activate.py")
    activation_state_path: Path = Path("/var/lib/storage-viz-dashboard/activation-state.json")
    restart_argv: tuple[str, ...] = ("/usr/bin/systemctl", "restart", "storage-viz-dashboard.service", "storage-viz-proxy.service")
    health_argv: tuple[str, ...] = ("/usr/local/libexec/storage-dashboard-health-check.py",)
    build_script: str = "apps/storage-monitor/deploy/build-dashboard-release.py"
    builder_user: str = "storage-viz-builder"
    node_prefix: Path = Path("/usr/local")
    repo_url: str = "https://github.com/IRCVLab/GPU_monitor.git"
    timeout_seconds: int = 900
    failure_backoff_base_seconds: int = 900
    failure_backoff_max_seconds: int = 21600
    agent_workflow: str = "agent-bundle.yml"
    # The same keys the storage hosts pin in /etc/storage-viz/allowed_signers.
    agent_allowed_signers: Path = Path("/etc/storage-viz/agent-allowed-signers")
    # A read-only token (actions: read). GitHub serves artifacts only to an authenticated client.
    github_token_file: Path = Path("/etc/storage-viz/github-token")
    # Root-owned and outside work_dir, which the unprivileged builder owns.
    agent_staging_dir: Path = Path("/var/lib/storage-viz-dashboard/agent-staging")
    release_root: Path = Path("/srv/storage-viz-dashboard/releases")


def validate_sha(value: object, name: str = "sha") -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise PullError(f"malformed {name}")
    return value


def validate_digest(value: object, name: str = "digest") -> str:
    if not isinstance(value, str) or DIGEST_RE.fullmatch(value) is None:
        raise PullError(f"malformed {name}")
    return value


def validate_repository(repository: str) -> None:
    if REPOSITORY_RE.fullmatch(repository) is None:
        raise PullError("repository must be OWNER/REPO")


def default_get_json(path: str, token: str = "") -> object:
    if not path.startswith("/"):
        raise PullError("GitHub API path must be absolute")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "storage-monitor-release-puller",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        # Unauthenticated calls share 60 per hour per IP with every other client on the host.
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(API_BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status == 403:
                raise PullError("GitHub API rate limited")
            if response.status < 200 or response.status >= 300:
                raise PullError(f"GitHub API returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise PullError("GitHub API rate limited") from error
        raise PullError(f"GitHub API HTTP failure: {error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        raise PullError(f"GitHub API evidence unavailable: {type(error).__name__}") from error


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def default_download(path: str, token_file: Path) -> bytes:
    """GET an authenticated GitHub API path that redirects to a short-lived blob URL.

    The token goes only to api.github.com. The redirect target is fetched without
    it, because the blob store rejects a second credential.
    """
    if not path.startswith("/"):
        raise PullError("GitHub API path must be absolute")
    try:
        token = token_file.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PullError("agent artifact download needs a GitHub token file") from error
    request = urllib.request.Request(API_BASE + path, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "storage-monitor-release-puller",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        urllib.request.build_opener(_NoRedirect).open(request, timeout=30)
        raise PullError("agent artifact download did not redirect")
    except urllib.error.HTTPError as error:
        location = error.headers.get("Location", "") if error.code in {301, 302, 303, 307, 308} else ""
        if not location.startswith("https://"):
            raise PullError(f"agent artifact download HTTP failure: {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PullError(f"agent artifact download unavailable: {type(error).__name__}") from error
    try:
        with urllib.request.urlopen(urllib.request.Request(location, headers={"User-Agent": "storage-monitor-release-puller"}), timeout=120) as response:
            data = response.read(MAX_AGENT_ZIP_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PullError(f"agent artifact blob unavailable: {type(error).__name__}") from error
    if len(data) > MAX_AGENT_ZIP_BYTES:
        raise PullError("agent artifact exceeds size bound")
    return data


def default_run_command(argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    binary_input = isinstance(kwargs.get("input"), (bytes, bytearray))
    if isinstance(kwargs.get("input"), bytearray):
        kwargs["input"] = bytes(kwargs["input"])
    try:
        result = subprocess.run(
            argv,
            text=not binary_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PullError(f"command execution failed: {type(error).__name__}") from error
    if not binary_input:
        return result
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        result.stdout.decode("utf-8", errors="replace"),
        result.stderr.decode("utf-8", errors="replace"),
    )


def current_main_sha(config: Config, get_json=default_get_json) -> str:
    payload = get_json(f"/repos/{config.repository}/git/ref/heads/main")
    if not isinstance(payload, dict):
        raise PullError("malformed main ref payload")
    obj = payload.get("object")
    if not isinstance(obj, dict):
        raise PullError("malformed main ref object")
    return validate_sha(obj.get("sha"), "current main sha")


def read_state_sha(config: Config) -> str | None:
    path = config.state_dir / "current-live-sha"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return validate_sha(value, "state sha")


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    fsync_dir(path.parent)


def state_path(config: Config) -> Path:
    return config.state_dir / "puller-state.json"


def failed_release_path(config: Config) -> Path:
    return config.state_dir / "failed-release.json"


def write_state_sha(config: Config, sha: str) -> None:
    validate_sha(sha)
    write_text_atomic(config.state_dir / "current-live-sha", sha + "\n")
    payload = read_puller_state(config)
    payload["current_sha"] = sha
    payload["last_attempted_sha"] = sha
    payload["last_failed_sha"] = ""
    payload["failure_count"] = 0
    write_json_atomic(state_path(config), payload)


def read_puller_state(config: Config) -> dict[str, object]:
    try:
        payload = json.loads(state_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"current_sha": "", "last_attempted_sha": "", "last_failed_sha": "", "failure_count": 0, "retry_after": 0.0}
    except (json.JSONDecodeError, OSError) as error:
        raise PullError("puller state is unreadable") from error
    if not isinstance(payload, dict):
        raise PullError("puller state is malformed")
    bounded = {}
    for key in ("current_sha", "last_attempted_sha", "last_failed_sha"):
        value = payload.get(key, "")
        if value != "":
            value = validate_sha(value, key)
        bounded[key] = value
    failures = payload.get("failure_count", 0)
    retry_after = payload.get("retry_after", 0.0)
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
        raise PullError("puller state is malformed")
    if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool) or retry_after < 0:
        raise PullError("puller state is malformed")
    bounded["failure_count"] = failures
    bounded["retry_after"] = float(retry_after)
    return bounded


def agent_pending_path(config: Config) -> Path:
    return config.state_dir / "agent-pending.json"


def read_agent_pending(config: Config) -> dict[str, object] | None:
    """The released SHA that carried an old agent bundle and still waits for its own."""
    try:
        payload = json.loads(agent_pending_path(config).read_text(encoding="utf-8"))
        sha = validate_sha(payload.get("sha"), "agent pending sha")
        checks, retry_after = payload.get("checks"), payload.get("retry_after")
        if type(checks) is not int or checks < 0 or not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool):
            return None
    except (OSError, ValueError, AttributeError, PullError):
        return None
    return {"sha": sha, "checks": checks, "retry_after": float(retry_after)}


def write_agent_pending(config: Config, sha: str, checks: int, now: float) -> None:
    delay = min(config.failure_backoff_base_seconds * (2 ** min(checks, 30)), config.failure_backoff_max_seconds)
    write_json_atomic(agent_pending_path(config), {"checks": checks, "retry_after": now + delay, "sha": sha})


def clear_agent_pending(config: Config) -> None:
    try:
        agent_pending_path(config).unlink()
    except FileNotFoundError:
        pass


def read_failed_release(config: Config) -> dict[str, object] | None:
    try:
        payload = json.loads(failed_release_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as error:
        raise PullError("failed-release state is unreadable") from error
    if not isinstance(payload, dict):
        raise PullError("failed-release state is malformed")
    sha = validate_sha(payload.get("sha"), "failed release sha")
    failures = payload.get("failures")
    retry_after = payload.get("retry_after")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 1:
        raise PullError("failed-release state is malformed")
    if not isinstance(retry_after, (int, float)) or isinstance(retry_after, bool) or retry_after < 0:
        raise PullError("failed-release state is malformed")
    return {"sha": sha, "failures": failures, "retry_after": float(retry_after)}


def write_failed_release(config: Config, sha: str, failures: int, now: float) -> None:
    validate_sha(sha)
    if failures < 1 or config.failure_backoff_base_seconds < 1:
        raise PullError("failure backoff configuration is invalid")
    if config.failure_backoff_max_seconds < config.failure_backoff_base_seconds:
        raise PullError("failure backoff configuration is invalid")
    delay = min(config.failure_backoff_base_seconds * (2 ** min(failures - 1, 30)), config.failure_backoff_max_seconds)
    retry_after = now + delay
    write_json_atomic(failed_release_path(config), {"failures": failures, "retry_after": retry_after, "sha": sha})
    state = read_puller_state(config)
    state.update({"last_attempted_sha": sha, "last_failed_sha": sha, "failure_count": failures, "retry_after": retry_after})
    write_json_atomic(state_path(config), state)


def clear_failed_release(config: Config) -> None:
    try:
        failed_release_path(config).unlink()
    except FileNotFoundError:
        pass
    else:
        fsync_dir(config.state_dir)
    state = read_puller_state(config)
    state.update({"last_failed_sha": "", "failure_count": 0, "retry_after": 0.0})
    write_json_atomic(state_path(config), state)


def workflow_run_for_sha(config: Config, sha: str, get_json=default_get_json) -> dict[str, object]:
    validate_sha(sha)
    query = urllib.parse.urlencode({"branch": "main", "event": "push", "per_page": "10"})
    payload = get_json(f"/repos/{config.repository}/actions/workflows/ci.yml/runs?{query}")
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise PullError("malformed workflow-runs evidence")
    for run in payload["workflow_runs"]:
        if not isinstance(run, dict):
            raise PullError("malformed workflow run item")
        if run.get("head_sha") == sha:
            if run.get("event") != "push" or run.get("head_branch") != "main":
                raise PullError("current SHA does not have push/main ci.yml evidence")
            if run.get("status") != "completed":
                raise CiPending(f"ci.yml run for {sha} is {run.get('status')}")
            if run.get("conclusion") != "success":
                raise PullError("current SHA does not have successful ci.yml evidence")
            return run
    # Right after a push the run may not be listed yet.
    raise CiPending(f"no ci.yml push/main workflow run listed yet for {sha}")


def check_runs_for_sha(config: Config, sha: str, get_json=default_get_json) -> list[dict[str, object]]:
    validate_sha(sha)
    checks: list[dict[str, object]] = []
    total_count: int | None = None
    page = 1
    while True:
        suffix = "per_page=100" if page == 1 else f"per_page=100&page={page}"
        payload = get_json(f"/repos/{config.repository}/commits/{sha}/check-runs?{suffix}")
        if not isinstance(payload, dict) or not isinstance(payload.get("check_runs"), list):
            raise PullError("malformed check-runs evidence")
        if total_count is None and isinstance(payload.get("total_count"), int) and not isinstance(payload.get("total_count"), bool):
            total_count = payload["total_count"]
        page_checks = payload["check_runs"]
        if not all(isinstance(item, dict) for item in page_checks):
            raise PullError("malformed check-run item")
        checks.extend(page_checks)
        if total_count is None or len(checks) >= total_count or len(page_checks) < 100:
            break
        page += 1
        if page > 20:
            raise PullError("check-runs pagination exceeded safety bound")
    if not checks:
        raise PullError("missing check-runs evidence")
    if total_count is not None and len(checks) != total_count:
        raise PullError("incomplete check-runs evidence")
    return checks


def fetch_evidence(config: Config, sha: str, get_json=default_get_json) -> tuple[dict[str, object], list[dict[str, object]], str]:
    workflow_run = workflow_run_for_sha(config, sha, get_json)
    check_runs = check_runs_for_sha(config, sha, get_json)
    current = current_main_sha(config, get_json)
    if current != sha:
        raise PullError("current main changed while collecting evidence")
    return workflow_run, check_runs, current


def authorize(config: Config, sha: str, workflow_run: dict[str, object], check_runs: list[dict[str, object]], run_command=default_run_command) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="storage-puller-auth.", dir=str(config.state_dir)) as tmpdir:
        workflow_path = Path(tmpdir) / "workflow.json"
        checks_path = Path(tmpdir) / "checks.json"
        workflow_path.write_text(json.dumps({"workflow_run": workflow_run}, sort_keys=True), encoding="utf-8")
        checks_path.write_text(json.dumps({"check_runs": check_runs}, sort_keys=True), encoding="utf-8")
        result = run_command([
            "python3",
            str(config.authorizer),
            "--repository", config.repository,
            "--workflow-run-file", str(workflow_path),
            "--checks-file", str(checks_path),
            "--current-main-sha", sha,
            "--required-check", "ci/required",
        ], timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError("release authorization denied")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PullError("release authorizer returned malformed JSON") from error
    if payload != {"authorized": True, "reason": "authorized", "sha": sha}:
        raise PullError("release authorizer returned unexpected authorization")


def log(message: str) -> None:
    print(f"storage-monitor-release-puller: {message}", file=sys.stderr)


@dataclass(frozen=True)
class AgentPlan:
    """Where the agent bundle of the next dashboard release comes from."""

    kind: str  # "none": build without one; "previous": carry the active release's; "fresh": download
    reason: str
    artifact_id: int | None = None


def agent_channel_disabled(config: Config) -> str | None:
    """Why the agent channel is off, or None when a key is pinned and a token is present."""
    try:
        lines = config.agent_allowed_signers.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    if not any(line.strip() and not line.lstrip().startswith("#") for line in lines):
        return f"no agent release key is pinned in {config.agent_allowed_signers}"
    try:
        token = config.github_token_file.read_text(encoding="utf-8").strip()
    except OSError:
        token = ""
    if not token:
        return f"no GitHub token in {config.github_token_file}"
    return None


def _iso_seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def plan_agent_bundle(config: Config, sha: str, ci_run: dict[str, object], get_json=default_get_json, now: float = 0.0) -> AgentPlan:
    """Decide the agent bundle for sha. Raises CiPending to wait for a signing job not yet finished."""
    validate_sha(sha)
    disabled = agent_channel_disabled(config)
    if disabled:
        return AgentPlan("none", disabled)
    query = urllib.parse.urlencode({"branch": "main", "event": "workflow_run", "head_sha": sha, "per_page": "10"})
    payload = get_json(f"/repos/{config.repository}/actions/workflows/{config.agent_workflow}/runs?{query}")
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise PullError("malformed agent-bundle workflow-runs evidence")
    runs = sorted(
        (r for r in payload["workflow_runs"] if isinstance(r, dict) and r.get("head_sha") == sha
         and r.get("head_branch") == "main" and r.get("event") == "workflow_run" and isinstance(r.get("id"), int)),
        key=lambda r: r["id"], reverse=True,
    )
    # Every main push triggers the signing workflow once per finished upstream workflow;
    # the runs whose upstream was not ci finish "skipped" without doing anything. A skipped
    # run says nothing about the bundle, so it must not shadow the run that signed it.
    runs = [r for r in runs if not (r.get("status") == "completed" and r.get("conclusion") == "skipped")] or runs
    if not runs:
        ci_done = _iso_seconds(ci_run.get("updated_at"))
        if ci_done is not None and now - ci_done < AGENT_RUN_GRACE_SECONDS:
            raise CiPending("agent-bundle run for current SHA has not started yet")
        return AgentPlan("previous", "no agent-bundle run for current SHA")
    run = runs[0]
    if run.get("status") != "completed":
        raise CiPending("agent-bundle run for current SHA is still running")
    if run.get("conclusion") != "success":
        return AgentPlan("previous", f"agent-bundle run concluded {run.get('conclusion')}")
    artifacts = get_json(f"/repos/{config.repository}/actions/runs/{run['id']}/artifacts?per_page=100")
    if not isinstance(artifacts, dict) or not isinstance(artifacts.get("artifacts"), list):
        raise PullError("malformed agent-bundle artifacts evidence")
    for artifact in artifacts["artifacts"]:
        if isinstance(artifact, dict) and artifact.get("name") == f"{AGENT_ARTIFACT_PREFIX}-{sha}":
            if artifact.get("expired") is not False or not isinstance(artifact.get("id"), int):
                return AgentPlan("previous", "the signed agent bundle for current SHA expired")
            return AgentPlan("fresh", "signed agent bundle for current SHA", artifact["id"])
    return AgentPlan("previous", "agent-bundle run for current SHA uploaded no bundle (is AGENT_SIGNING_KEY set?)")


def unpack_agent_artifact(data: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = sorted(info.filename for info in archive.infolist())
            if names != sorted(MAX_AGENT_FILE_BYTES):
                raise PullError(f"agent artifact holds unexpected files: {names}")
            for info in archive.infolist():
                if info.file_size > MAX_AGENT_FILE_BYTES[info.filename]:
                    raise PullError(f"agent artifact file exceeds size bound: {info.filename}")
                files[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, OSError) as error:
        raise PullError("agent artifact is not a valid zip") from error
    return files


def verify_agent_files(config: Config, files: dict[str, bytes], run_command=default_run_command) -> dict[str, object]:
    """Check signature, metadata and manifest of a bundle, in memory. Returns the metadata.

    The signature is checked with the keys the hosts pin, and the bundle's own
    RELEASE-MANIFEST must agree with the metadata.
    """
    bundle = files[AGENT_BUNDLE]
    try:
        metadata = json.loads(files[AGENT_METADATA].decode("utf-8"))
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as tar:
            member = tar.extractfile("RELEASE-MANIFEST.json")
            manifest = json.loads(member.read(1024 * 1024).decode("utf-8")) if member else None
    except (UnicodeDecodeError, json.JSONDecodeError, tarfile.TarError, KeyError, OSError, EOFError) as error:
        raise PullError("agent bundle metadata or manifest is unreadable") from error
    fields = ("application", "source_sha", "release_seq", "rollback_floor_seq", "bootstrap_version")
    if not isinstance(metadata, dict) or not isinstance(manifest, dict):
        raise PullError("agent bundle metadata is malformed")
    validate_sha(metadata.get("source_sha"), "agent source_sha")
    if (metadata.get("application") != "storage-viz-agent"
            or any(metadata.get(key) != manifest.get(key) for key in fields)
            or metadata.get("sha256") != hashlib.sha256(bundle).hexdigest()
            or metadata.get("size") != len(bundle)):
        raise PullError("agent bundle metadata does not match the bundle")
    with tempfile.TemporaryDirectory(prefix="storage-puller-agent.", dir=str(config.state_dir)) as tmpdir:
        signature = Path(tmpdir) / AGENT_SIGNATURE
        signature.write_bytes(files[AGENT_SIGNATURE])
        result = run_command([
            "ssh-keygen", "-Y", "verify", "-f", str(config.agent_allowed_signers),
            "-I", AGENT_SIGNER, "-n", AGENT_NAMESPACE, "-s", str(signature),
        ], input=bundle, timeout=60)
    if result.returncode != 0:
        raise PullError("agent bundle signature does not verify against the pinned release key")
    return metadata


def fetch_agent_artifact(config: Config, artifact_id: int, download, run_command=default_run_command) -> dict[str, bytes]:
    """Download and verify the signed bundle. A bundle that fails verification refuses the release."""
    files = unpack_agent_artifact(download(f"/repos/{config.repository}/actions/artifacts/{artifact_id}/zip"))
    try:
        verify_agent_files(config, files, run_command)
    except PullError as error:
        log(f"REFUSING the release: the CI agent bundle failed verification, a possible attack: {error}")
        raise
    return files


def _read_release_file(path: Path, cap: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(f"not a regular file: {path}")
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise OSError(f"too large: {path}")
    return data


def previous_agent_bundle(config: Config, run_command=default_run_command) -> dict[str, bytes] | None:
    """The bundle in the active dashboard release, verified again; None when there is none."""
    try:
        payload = json.loads(config.activation_state_path.read_text(encoding="utf-8"))
        release = Path(payload["release"])
        sha = release.parent.name
        if SHA_RE.fullmatch(sha) is None or release != config.release_root / sha / "storage-monitor":
            return None
        files = {name: _read_release_file(release / "agent" / name, cap) for name, cap in MAX_AGENT_FILE_BYTES.items()}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        verify_agent_files(config, files, run_command)
    except PullError as error:
        log(f"the active release's agent bundle no longer verifies, dropping it: {error}")
        return None
    return files


def obtain_agent_bundle(config: Config, plan: AgentPlan, download, run_command=default_run_command) -> dict[str, bytes] | None:
    if plan.kind == "fresh":
        return fetch_agent_artifact(config, plan.artifact_id, download, run_command)
    if plan.kind == "previous":
        files = previous_agent_bundle(config, run_command)
        log(f"{plan.reason}; {'keeping the previous verified agent bundle' if files else 'no previous agent bundle, releasing without one'}")
        return files
    log(f"{plan.reason}; releasing the dashboard without an agent bundle")
    return None


def stage_agent_bundle(config: Config, files: dict[str, bytes]) -> Path:
    """Write the verified bytes where the builder can read but not change them."""
    staging = config.agent_staging_dir
    parent = os.lstat(staging.parent)
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise PullError(f"agent staging parent {staging.parent} must be ours and not group or world writable")
    if os.path.islink(staging) or (staging.exists() and not staging.is_dir()):
        staging.unlink()
    shutil.rmtree(staging, ignore_errors=True)
    os.mkdir(staging, 0o755)
    os.chmod(staging, 0o755)
    for name, content in files.items():
        fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            os.fchmod(handle.fileno(), 0o644)  # the unit's UMask=0077 would hide them from the builder
    return staging


def builder_command(config: Config, *args: str) -> list[str]:
    return [
        "/usr/sbin/runuser", "-u", config.builder_user, "--",
        "env", "-i",
        "HOME=/var/lib/storage-viz-dashboard/builder",
        f"PATH={config.node_prefix}/bin:/usr/local/bin:/usr/bin:/bin",
        "GIT_CONFIG_NOSYSTEM=1",
        *args,
    ]


def clean_checkout(config: Config, sha: str, run_command=default_run_command) -> Path:
    validate_sha(sha)
    checkout = config.work_dir / "checkout"
    if checkout.exists():
        shutil.rmtree(checkout)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ["mkdir", "-p", str(checkout.parent)],
        ["rm", "-rf", str(checkout)],
        ["nice", "-n", "10", "ionice", "-c", "3", "git", "clone", "--no-checkout", "--filter=blob:none", config.repo_url, str(checkout)],
        ["git", "-C", str(checkout), "fetch", "--depth", "1", "origin", sha],
        ["git", "-C", str(checkout), "checkout", "--detach", sha],
        ["git", "-C", str(checkout), "clean", "-xffd"],
        ["git", "-C", str(checkout), "diff", "--quiet", "--exit-code", "--"],
    ]
    for command in commands:
        result = run_command(builder_command(config, *command), timeout=config.timeout_seconds)
        if result.returncode != 0:
            raise PullError(f"builder checkout command failed: {' '.join(command)}")
    return checkout


def build_release(config: Config, checkout: Path, sha: str, run_command=default_run_command, agent_dir: Path | None = None) -> Path:
    outdir = config.work_dir / "out" / sha
    result = run_command(builder_command(config, "rm", "-rf", str(outdir)), timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError("builder could not clean output directory")
    result = run_command(builder_command(config, "mkdir", "-p", str(outdir)), timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError("builder could not create output directory")
    script = checkout / config.build_script
    agent_args = ("--agent-bundle-dir", str(agent_dir)) if agent_dir is not None else ()
    result = run_command(
        builder_command(config, "nice", "-n", "10", "ionice", "-c", "3", "python3", str(script), "--sha", sha, "--output-dir", str(outdir), *agent_args),
        cwd=checkout,
        timeout=config.timeout_seconds,
    )
    if result.returncode != 0:
        raise PullError("builder release command failed")
    return outdir


def validate_artifact(outdir: Path, sha: str, agent_files=UNCHECKED) -> tuple[Path, str, str]:
    """Check the built archive. agent_files: the verified bundle bytes, None for none, UNCHECKED to skip."""
    archive = outdir / f"{ARTIFACT_PREFIX}-{sha}.tar.gz"
    metadata = outdir / f"{ARTIFACT_PREFIX}-{sha}.sha256.json"
    if not archive.is_file() or not metadata.is_file():
        raise PullError("release output is incomplete")
    fd = os.open(archive, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        archive_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(archive_stat.st_mode) or archive_stat.st_size > MAX_ARCHIVE_BYTES:
            raise PullError("release artifact must be a regular archive within the configured size bound")
        archive_bytes = source.read(MAX_ARCHIVE_BYTES + 1)
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise PullError("release artifact exceeds configured size bound")
    digest = hashlib.sha256(archive_bytes).hexdigest()
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PullError("release metadata is malformed") from error
    expected = {
        "application_name": APPLICATION_NAME,
        "archive": archive.name,
        "artifact_format_version": ARTIFACT_FORMAT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "sha256": digest,
        "source_sha": sha,
    }
    if payload != expected:
        raise PullError("release metadata does not match artifact")
    try:
        inspected = inspect_archive(io.BytesIO(archive_bytes), sha=sha, archive_name=archive.name)
    except ActivationError as error:
        raise PullError(f"invalid Storage release archive: {error}") from error
    if agent_files is not UNCHECKED:
        expected = {} if agent_files is None else {
            rel: hashlib.sha256(agent_files[PurePosixPath(rel).name]).hexdigest() for rel in AGENT_BUNDLE_FILES}
        carried = {rel: digest for rel, digest in inspected.files.items() if rel.startswith("agent/")}
        if carried != expected:
            raise PullError("release does not carry exactly the verified agent bundle")
    return archive, digest, inspected.runtime_digest


def activation_command(config: Config, *args: str) -> list[str]:
    return [str(config.activate), *args]


def read_activation_state(config: Config) -> dict[str, str]:
    try:
        payload = json.loads(config.activation_state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"current": "", "runtime_digest": ""}
    except (json.JSONDecodeError, OSError) as error:
        raise PullError("activation state is unreadable") from error
    if not isinstance(payload, dict):
        raise PullError("activation state is malformed")
    status = payload.get("status", "")
    if status not in {"", "active", "rolled_back"}:
        return {"current": "", "runtime_digest": ""}
    source_sha = payload.get("source_sha", "")
    archive_digest = payload.get("archive_digest", "")
    current = "" if source_sha == "" else validate_sha(source_sha, "activation state source_sha")
    if archive_digest != "":
        validate_digest(archive_digest, "activation state archive_digest")
    runtime_digest = payload.get("runtime_digest", "")
    if runtime_digest != "":
        validate_digest(runtime_digest, "activation state runtime_digest")
    return {"current": current, "runtime_digest": runtime_digest}


def _metadata_path_for_artifact(artifact: Path) -> Path:
    name = artifact.name
    suffix = ".tar.gz"
    if not name.endswith(suffix):
        raise PullError("artifact name is not a gzip tar archive")
    return artifact.with_name(name[: -len(suffix)] + ".sha256.json")


def _parse_activation_stdout(stdout: str, *, sha: str, digest: str, runtime_digest: str | None) -> dict[str, object]:
    if len(stdout.encode("utf-8")) > 8192:
        raise PullError("activation status JSON exceeds output bound")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise PullError("activation returned malformed JSON") from error
    if not isinstance(payload, dict):
        raise PullError("activation returned non-object JSON")
    if payload.get("status") != "active":
        raise PullError("activation did not report active status")
    if payload.get("source_sha") != sha:
        raise PullError("activation source_sha does not match requested SHA")
    if payload.get("archive_digest") != digest:
        raise PullError("activation archive_digest does not match artifact digest")
    if runtime_digest is not None and payload.get("runtime_digest") != runtime_digest:
        raise PullError("activation runtime_digest does not match validated runtime digest")
    release = payload.get("release", "")
    if not isinstance(release, str) or sha not in release:
        raise PullError("activation release path does not identify requested SHA")
    return payload


def _bounded_activation_error(stderr: str) -> str | None:
    if not isinstance(stderr, str) or not stderr or len(stderr) > 4096:
        return None
    try:
        payload = json.loads(stderr)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("status") != "error":
        return None
    message = payload.get("error")
    if not isinstance(message, str) or not message or len(message) > 512:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in message):
        return None
    return message


def activate_release(
    config: Config,
    sha: str,
    digest: str,
    artifact: Path,
    run_command=default_run_command,
    *,
    runtime_digest: str | None = None,
) -> dict[str, object]:
    validate_sha(sha)
    validate_digest(digest)
    metadata = _metadata_path_for_artifact(artifact)
    argv = activation_command(
        config,
        "--sha", sha,
        "--expected-digest", digest,
        "--artifact-stdin",
        "--metadata", str(metadata),
        "--restart-argv", *config.restart_argv,
        "--health-argv", *config.health_argv,
    )
    with artifact.open("rb") as input_handle:
        result = run_command(argv, stdin=input_handle, timeout=config.timeout_seconds)
    if result.returncode != 0:
        detail = _bounded_activation_error(result.stderr)
        suffix = f": {detail}" if detail else ""
        raise PullError(f"activation command failed{suffix}")
    return _parse_activation_stdout(result.stdout, sha=sha, digest=digest, runtime_digest=runtime_digest)


def read_github_token(token_file: Path) -> str:
    try:
        return token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def run_once(config: Config, *, get_json=default_get_json, run_command=default_run_command, now_fn=time.time, download=None) -> str:
    if get_json is default_get_json:
        token = read_github_token(config.github_token_file)

        def get_json(path: str) -> object:
            return default_get_json(path, token)
    if download is None:
        def download(path: str) -> bytes:
            return default_download(path, config.github_token_file)
    validate_repository(config.repository)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config.state_dir / "puller.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "locked"
        sha = current_main_sha(config, get_json)
        now = None
        pending = read_agent_pending(config)
        if pending is not None and pending["sha"] != sha:
            clear_agent_pending(config)  # a newer SHA plans its own bundle
            pending = None
        if pending is not None and read_state_sha(config) == sha:
            # Released with a carried-forward bundle: look for its own, with backoff, and
            # rebuild only once a fresh signed bundle exists.
            now = float(now_fn())
            if now < pending["retry_after"]:
                return "agent-pending"
            try:
                recheck = plan_agent_bundle(config, sha, {}, get_json, now)
            except PullError:
                recheck = None  # still running, or the evidence is unreadable this tick
            if recheck is not None and recheck.kind == "none":
                clear_agent_pending(config)
                return "already-current"
            if recheck is None or recheck.kind == "previous":
                write_agent_pending(config, sha, int(pending["checks"]) + 1, now)
                return "agent-pending"
            log(f"a fresh agent bundle now exists for {sha}; rebuilding the release with it")
        elif read_state_sha(config) == sha:
            write_state_sha(config, sha)
            clear_failed_release(config)
            return "already-current"
        failure = read_failed_release(config)
        if failure is not None and failure["sha"] != sha:
            clear_failed_release(config)
            failure = None
        activation_state = read_activation_state(config)
        if activation_state["current"] == sha and pending is None:
            write_state_sha(config, sha)
            clear_failed_release(config)
            return "reconciled-current"
        if now is None:
            now = float(now_fn())
        if failure is not None and now < failure["retry_after"]:
            return "backoff"
        try:
            workflow_run, check_runs, _ = fetch_evidence(config, sha, get_json)
        except CiPending:
            return "waiting-for-ci"
        authorize(config, sha, workflow_run, check_runs, run_command)
        try:
            plan = plan_agent_bundle(config, sha, workflow_run, get_json, now)
        except CiPending:
            return "waiting-for-agent-bundle"
        try:
            agent_files = obtain_agent_bundle(config, plan, download, run_command)
            agent_dir = stage_agent_bundle(config, agent_files) if agent_files else None
            checkout = clean_checkout(config, sha, run_command)
            outdir = build_release(config, checkout, sha, run_command, agent_dir)
            artifact, digest, runtime_digest = validate_artifact(outdir, sha, agent_files)
            workflow_run, check_runs, _ = fetch_evidence(config, sha, get_json)
            authorize(config, sha, workflow_run, check_runs, run_command)
            activation_state = read_activation_state(config)
            if activation_state["runtime_digest"] == runtime_digest:
                write_state_sha(config, sha)
                clear_failed_release(config)
                record_agent_outcome(config, sha, plan, now)
                shutil.rmtree(config.work_dir / "out", ignore_errors=True)
                shutil.rmtree(config.agent_staging_dir, ignore_errors=True)
                return "unchanged-runtime"
            activate_release(config, sha, digest, artifact, run_command, runtime_digest=runtime_digest)
        except (PullError, OSError, subprocess.SubprocessError, shutil.Error) as error:
            failures = int(failure["failures"]) + 1 if failure is not None else 1
            write_failed_release(config, sha, failures, float(now_fn()))
            if isinstance(error, PullError):
                raise
            raise PullError(f"release pipeline failed: {type(error).__name__}") from error
        write_state_sha(config, sha)
        clear_failed_release(config)
        record_agent_outcome(config, sha, plan, now)
        shutil.rmtree(config.work_dir / "out", ignore_errors=True)
        shutil.rmtree(config.agent_staging_dir, ignore_errors=True)
        return "activated"


def record_agent_outcome(config: Config, sha: str, plan: AgentPlan, now: float) -> None:
    """A release that carried the previous bundle stays pending until its own bundle arrives."""
    if plan.kind == "previous":
        write_agent_pending(config, sha, 0, now)
    else:
        clear_agent_pending(config)


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=Config.repository)
    parser.add_argument("--state-dir", type=Path, default=Config.state_dir)
    parser.add_argument("--work-dir", type=Path, default=Config.work_dir)
    parser.add_argument("--authorizer", type=Path, default=Config.authorizer)
    parser.add_argument("--activate", type=Path, default=Config.activate)
    parser.add_argument("--activation-state-path", type=Path, default=Config.activation_state_path)
    parser.add_argument("--restart-argv", nargs="+", default=list(Config.restart_argv))
    parser.add_argument("--health-argv", nargs="+", default=list(Config.health_argv))
    parser.add_argument("--builder-user", default=Config.builder_user)
    parser.add_argument("--repo-url", default=Config.repo_url)
    parser.add_argument("--timeout-seconds", type=int, default=Config.timeout_seconds)
    parser.add_argument("--failure-backoff-base-seconds", type=int, default=Config.failure_backoff_base_seconds)
    parser.add_argument("--failure-backoff-max-seconds", type=int, default=Config.failure_backoff_max_seconds)
    parser.add_argument("--agent-allowed-signers", type=Path, default=Config.agent_allowed_signers)
    parser.add_argument("--github-token-file", type=Path, default=Config.github_token_file)
    args = parser.parse_args(argv)
    return Config(
        repository=args.repository,
        state_dir=args.state_dir,
        work_dir=args.work_dir,
        authorizer=args.authorizer,
        activate=args.activate,
        activation_state_path=args.activation_state_path,
        restart_argv=tuple(args.restart_argv),
        health_argv=tuple(args.health_argv),
        builder_user=args.builder_user,
        repo_url=args.repo_url,
        timeout_seconds=args.timeout_seconds,
        failure_backoff_base_seconds=args.failure_backoff_base_seconds,
        failure_backoff_max_seconds=args.failure_backoff_max_seconds,
        agent_allowed_signers=args.agent_allowed_signers,
        github_token_file=args.github_token_file,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        print(run_once(parse_args(sys.argv[1:] if argv is None else argv)))
        return 0
    except PullError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
