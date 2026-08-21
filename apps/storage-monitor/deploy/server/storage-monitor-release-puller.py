#!/usr/bin/env python3
"""Outbound-only storage dashboard release puller."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API_BASE = "https://api.github.com"
APPLICATION_NAME = "storage-monitor"
ARTIFACT_PREFIX = "storage-monitor-dashboard"


class PullError(RuntimeError):
    pass


@dataclass(frozen=True)
class Config:
    repository: str = "IRCVLab/GPU_monitor"
    state_dir: Path = Path("/var/lib/storage-viz-dashboard/puller")
    work_dir: Path = Path("/var/lib/storage-viz-dashboard/builder")
    authorizer: Path = Path("/usr/local/libexec/storage-release-authorizer.py")
    activate: Path = Path("/usr/local/libexec/storage-dashboard-activate.py")
    build_script: str = "apps/storage-monitor/deploy/build-dashboard-release.py"
    builder_user: str = "storage-viz-builder"
    node_prefix: Path = Path("/usr/local")
    repo_url: str = "https://github.com/IRCVLab/GPU_monitor.git"
    timeout_seconds: int = 900
    failure_backoff_base_seconds: int = 900
    failure_backoff_max_seconds: int = 21600


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


def default_get_json(path: str) -> object:
    if not path.startswith("/"):
        raise PullError("GitHub API path must be absolute")
    request = urllib.request.Request(
        API_BASE + path,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "storage-monitor-release-puller",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
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
            if run.get("status") != "completed" or run.get("conclusion") != "success":
                raise PullError("current SHA does not have successful ci.yml evidence")
            return run
    raise PullError("missing successful ci.yml push/main workflow run for current SHA")


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
    required = [run for run in check_runs if run.get("name") == "ci/required" and run.get("head_sha") == sha]
    if not required:
        raise PullError("missing ci/required check for current SHA")
    if not any(run.get("status") == "completed" and run.get("conclusion") == "success" for run in required):
        raise PullError("ci/required check is not successful for current SHA")
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


def builder_command(config: Config, *args: str) -> list[str]:
    return [
        "runuser", "-u", config.builder_user, "--",
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


def build_release(config: Config, checkout: Path, sha: str, run_command=default_run_command) -> Path:
    outdir = config.work_dir / "out" / sha
    result = run_command(builder_command(config, "rm", "-rf", str(outdir)), timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError("builder could not clean output directory")
    result = run_command(builder_command(config, "mkdir", "-p", str(outdir)), timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError("builder could not create output directory")
    script = checkout / config.build_script
    result = run_command(
        builder_command(config, "nice", "-n", "10", "ionice", "-c", "3", str(script), "--sha", sha, "--output-dir", str(outdir)),
        timeout=config.timeout_seconds,
    )
    if result.returncode != 0:
        raise PullError("builder release command failed")
    return outdir


def validate_artifact(outdir: Path, sha: str) -> tuple[Path, str]:
    archive = outdir / f"{ARTIFACT_PREFIX}-{sha}.tar.gz"
    metadata = outdir / f"{ARTIFACT_PREFIX}-{sha}.sha256.json"
    if not archive.is_file() or not metadata.is_file():
        raise PullError("release output is incomplete")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PullError("release metadata is malformed") from error
    expected = {
        "application_name": APPLICATION_NAME,
        "archive": archive.name,
        "artifact_format_version": 1,
        "schema_version": 1,
        "sha256": digest,
        "source_sha": sha,
    }
    if payload != expected:
        raise PullError("release metadata does not match artifact")
    validate_digest(digest, "artifact digest")
    return archive, digest


def activation_command(config: Config, *args: str) -> list[str]:
    return [str(config.activate), *args]


def upload_artifact(config: Config, sha: str, digest: str, artifact: Path, run_command=default_run_command) -> None:
    validate_sha(sha)
    validate_digest(digest)
    argv = activation_command(config, "upload", "live", sha, digest)
    with artifact.open("rb") as input_handle:
        result = run_command(argv, stdin=input_handle, timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError(f"activation command failed: {' '.join(argv)}")


def live_status(config: Config, run_command=default_run_command) -> dict[str, str]:
    argv = activation_command(config, "status", "live")
    result = run_command(argv, timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError(f"activation command failed: {' '.join(argv)}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PullError("live status returned malformed JSON") from error
    if not isinstance(payload, dict) or payload.get("environment") != "live":
        raise PullError("live status returned unexpected environment")
    normalized = {}
    for key in ("current", "previous"):
        value = payload.get(key)
        if value == "":
            normalized[key] = ""
            continue
        if not isinstance(value, str) or not value.startswith("releases/"):
            raise PullError(f"malformed live status {key}")
        normalized[key] = validate_sha(value.removeprefix("releases/"), f"live status {key}")
    current_sha256 = payload.get("current_sha256", "")
    normalized["current_sha256"] = "" if current_sha256 == "" else validate_digest(current_sha256, "live status current_sha256")
    return normalized


def activate_uploaded(config: Config, sha: str, digest: str, run_command=default_run_command) -> None:
    validate_sha(sha)
    validate_digest(digest)
    argv = activation_command(config, "activate", "live", sha, digest)
    result = run_command(argv, timeout=config.timeout_seconds)
    if result.returncode != 0:
        raise PullError(f"activation command failed: {' '.join(argv)}")
    if live_status(config, run_command)["current"] != sha:
        raise PullError("live status does not match activated SHA")


def run_once(config: Config, *, get_json=default_get_json, run_command=default_run_command, now_fn=time.time) -> str:
    validate_repository(config.repository)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config.state_dir / "puller.lock"
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "locked"
        sha = current_main_sha(config, get_json)
        if read_state_sha(config) == sha:
            write_state_sha(config, sha)
            clear_failed_release(config)
            return "already-current"
        failure = read_failed_release(config)
        if failure is not None and failure["sha"] != sha:
            clear_failed_release(config)
            failure = None
        now = float(now_fn())
        if failure is not None and now < failure["retry_after"]:
            return "backoff"
        if live_status(config, run_command)["current"] == sha:
            write_state_sha(config, sha)
            clear_failed_release(config)
            return "reconciled-current"
        workflow_run, check_runs, _ = fetch_evidence(config, sha, get_json)
        authorize(config, sha, workflow_run, check_runs, run_command)
        try:
            checkout = clean_checkout(config, sha, run_command)
            outdir = build_release(config, checkout, sha, run_command)
            artifact, digest = validate_artifact(outdir, sha)
            workflow_run, check_runs, _ = fetch_evidence(config, sha, get_json)
            authorize(config, sha, workflow_run, check_runs, run_command)
            if live_status(config, run_command)["current_sha256"] == digest:
                write_state_sha(config, sha)
                clear_failed_release(config)
                shutil.rmtree(config.work_dir / "out", ignore_errors=True)
                return "unchanged-artifact"
            upload_artifact(config, sha, digest, artifact, run_command)
            activate_uploaded(config, sha, digest, run_command)
        except (PullError, OSError, subprocess.SubprocessError, shutil.Error) as error:
            failures = int(failure["failures"]) + 1 if failure is not None else 1
            write_failed_release(config, sha, failures, float(now_fn()))
            if isinstance(error, PullError):
                raise
            raise PullError(f"release pipeline failed: {type(error).__name__}") from error
        write_state_sha(config, sha)
        clear_failed_release(config)
        shutil.rmtree(config.work_dir / "out", ignore_errors=True)
        return "activated"


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=Config.repository)
    parser.add_argument("--state-dir", type=Path, default=Config.state_dir)
    parser.add_argument("--work-dir", type=Path, default=Config.work_dir)
    parser.add_argument("--authorizer", type=Path, default=Config.authorizer)
    parser.add_argument("--activate", type=Path, default=Config.activate)
    parser.add_argument("--builder-user", default=Config.builder_user)
    parser.add_argument("--repo-url", default=Config.repo_url)
    parser.add_argument("--timeout-seconds", type=int, default=Config.timeout_seconds)
    parser.add_argument("--failure-backoff-base-seconds", type=int, default=Config.failure_backoff_base_seconds)
    parser.add_argument("--failure-backoff-max-seconds", type=int, default=Config.failure_backoff_max_seconds)
    args = parser.parse_args(argv)
    return Config(
        repository=args.repository,
        state_dir=args.state_dir,
        work_dir=args.work_dir,
        authorizer=args.authorizer,
        activate=args.activate,
        builder_user=args.builder_user,
        repo_url=args.repo_url,
        timeout_seconds=args.timeout_seconds,
        failure_backoff_base_seconds=args.failure_backoff_base_seconds,
        failure_backoff_max_seconds=args.failure_backoff_max_seconds,
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
