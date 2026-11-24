"""Classify collector failures into the status payload shared with dashboards and alerts."""

import socket
from typing import TypedDict

import paramiko

try:
    from ..telemetry_time import now_utc
except ImportError:  # pragma: no cover - direct execution fallback
    from telemetry_time import now_utc


class StatusReason(TypedDict):
    code: str
    source: str
    message: str
    retryable: bool
    updated_at: str


def _status_reason_payload(
    code: str,
    source: str,
    message: str,
    retryable: bool,
) -> StatusReason:
    return {
        "code": code,
        "source": source,
        "message": message,
        "retryable": retryable,
        "updated_at": now_utc().isoformat(),
    }


def inventory_failure(inventory: dict) -> StatusReason:
    missing = inventory.get("missing_indices") or []
    if missing:
        detail = f"missing GPU indices: {missing}"
    else:
        detail = (
            f"visible GPU count {inventory.get('visible_count')} is less than "
            f"expected {inventory.get('expected_count')}"
        )
    return _status_reason_payload(
        "gpu_device_missing",
        "gpu",
        f"GPU inventory mismatch detected ({detail})",
        True,
    )


def collection_failure(gpu_failed: bool, system_failed: bool) -> StatusReason | None:
    if gpu_failed and system_failed:
        return _status_reason_payload(
            "collector_error",
            "collector",
            "GPU 및 시스템 메트릭 수집 실패",
            True,
        )
    if gpu_failed:
        return _status_reason_payload(
            "gpu_collect_failed",
            "gpu",
            "GPU 메트릭 수집 실패",
            True,
        )
    if system_failed:
        return _status_reason_payload(
            "system_collect_failed",
            "system",
            "시스템 메트릭 수집 실패",
            True,
        )
    return None


def connection_failure(exc: Exception) -> StatusReason:
    if isinstance(exc, paramiko.AuthenticationException):
        return _status_reason_payload("auth_failed", "connect", "SSH 인증 실패", False)

    if isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
        return _status_reason_payload("connection_refused", "connect", "SSH 연결 거부", True)

    if isinstance(exc, (socket.timeout, TimeoutError)):
        return _status_reason_payload("timeout", "connect", "SSH 연결 시간 초과", True)

    if isinstance(exc, paramiko.SSHException):
        return _status_reason_payload("ssh_error", "connect", "SSH 연결 오류", True)

    if isinstance(exc, ConnectionRefusedError):
        return _status_reason_payload("connection_refused", "connect", "SSH 연결 거부", True)

    message = str(exc).lower()
    if "no ssh credentials configured" in message:
        return _status_reason_payload("missing_credentials", "connect", "SSH 인증 정보 없음", False)
    if "timed out" in message:
        return _status_reason_payload("timeout", "connect", "SSH 연결 시간 초과", True)
    if "authentication failed" in message:
        return _status_reason_payload("auth_failed", "connect", "SSH 인증 실패", False)
    if "refused" in message:
        return _status_reason_payload("connection_refused", "connect", "SSH 연결 거부", True)

    return _status_reason_payload("ssh_error", "collector", "서버 연결 오류", True)
