"""Compact Slack response builder backed by the Storage Monitor summary API."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_SERVERS = 12
_MAX_VOLUMES = 8
_MAX_DETAILED_VOLUMES = 8
_MAX_PATH_LENGTH = 48
_MAX_SERVER_NAME_LENGTH = 80
_MAX_MEDIA_LENGTH = 16
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class StorageSummaryError(RuntimeError):
    """Raised when the bounded loopback Storage Monitor request is unusable."""


def _escape_mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fetch_storage_summary(api_url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    parsed = urlsplit(api_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise StorageSummaryError("Storage Monitor API URL must be loopback HTTP")

    request = Request(api_url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise StorageSummaryError("Storage Monitor API is unavailable") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise StorageSummaryError("Storage Monitor response exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StorageSummaryError("Storage Monitor response is not valid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), list):
        raise StorageSummaryError("Storage Monitor response has an invalid shape")
    return payload


def _health_issue(server: Mapping[str, Any]) -> str | None:
    if server.get("snapshot_availability") != "available":
        return "snapshot unavailable"

    pull_status = server.get("latest_pull_status")
    if pull_status == "unreachable":
        return "collector unreachable"
    if pull_status == "invalid_snapshot":
        return "invalid snapshot"
    if pull_status not in {None, "succeeded"}:
        return "collector unavailable"

    scan_result = server.get("latest_scan_result")
    if scan_result == "failed":
        return "scan failed"
    if scan_result == "partial":
        return "partial scan"

    if server.get("configuration_sync") == "drifted":
        return "configuration drift"
    if server.get("freshness") != "fresh":
        return "snapshot stale"

    active_job = server.get("active_job")
    if isinstance(active_job, Mapping) and active_job.get("state") in {"queued", "running"}:
        return "scan in progress"
    return None


def _format_bytes(value: Any) -> str:
    try:
        size = max(int(value), 0)
    except (TypeError, ValueError):
        size = 0

    units = ((1024**4, "T"), (1024**3, "G"), (1024**2, "M"), (1024, "K"))
    for divisor, suffix in units:
        if size >= divisor:
            amount = size / divisor
            if suffix == "T" and not amount.is_integer():
                return f"{amount:.1f}{suffix}"
            return f"{amount:.0f}{suffix}"
    return f"{size}B"


def _fill_icon(used_pct: Any) -> str:
    try:
        percent = max(0, min(int(used_pct), 100))
    except (TypeError, ValueError):
        percent = 0
    if percent >= 92:
        return "●"
    if percent >= 80:
        return "◕"
    if percent >= 50:
        return "◑"
    if percent >= 25:
        return "◔"
    return "○"


def _compact_path(value: Any) -> str:
    path = str(value or "-")
    if len(path) <= _MAX_PATH_LENGTH:
        return path
    left = (_MAX_PATH_LENGTH - 1) // 2
    right = _MAX_PATH_LENGTH - left - 1
    return f"{path[:left]}…{path[-right:]}"


def _truncate_text(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return f"{value[:max_length - 1]}…"


def _mount_text(mount: Mapping[str, Any]) -> str:
    path = _escape_mrkdwn(_compact_path(mount.get("path") or mount.get("mountpoint")))
    try:
        used_pct = max(0, min(int(mount.get("df_use_pct") or 0), 100))
    except (TypeError, ValueError):
        used_pct = 0
    media = _truncate_text(
        str(mount.get("storage_media") or mount.get("block_media") or "").upper(),
        _MAX_MEDIA_LENGTH,
    )
    parts = [f"{_fill_icon(used_pct)} {path}", f"{used_pct}%", _format_bytes(mount.get("df_avail"))]
    if media and media != "UNKNOWN":
        parts.append(_escape_mrkdwn(media))
    return f"`{' · '.join(parts)}`"


def _server_volumes(server: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    overview = server.get("overview_snapshot")
    if not isinstance(overview, Mapping):
        return []
    mounts = overview.get("mounts")
    if not isinstance(mounts, list):
        return []

    roots = overview.get("selected_roots")
    capacity_by_mount_id: dict[str, str] = {}
    if isinstance(roots, list):
        for root in roots:
            if not isinstance(root, Mapping):
                continue
            mount_id = str(root.get("mount_id") or "").strip()
            capacity_id = str(root.get("capacity_id") or "").strip()
            capacity_match = re.fullmatch(r"dev-([1-9]\d*)-(0|[1-9]\d*)", capacity_id)
            major_minor = str(root.get("major_minor") or "").strip()
            major_minor_match = re.fullmatch(r"([1-9]\d*):(0|[1-9]\d*)", major_minor)
            identity = None
            if capacity_match:
                identity = f"capacity:{capacity_id}"
            elif major_minor_match:
                identity = f"major_minor:{int(major_minor_match.group(1))}:{int(major_minor_match.group(2))}"
            if mount_id and identity and mount_id not in capacity_by_mount_id:
                capacity_by_mount_id[mount_id] = identity

    groups: dict[str, dict[str, Any]] = {}
    for index, mount in enumerate(mounts):
        if not isinstance(mount, Mapping):
            continue
        mount_id = str(mount.get("mount_id") or "").strip()
        capacity_identity = capacity_by_mount_id.get(mount_id)
        key = capacity_identity or f"mount:{mount_id or index}"
        path = str(mount.get("path") or mount.get("mountpoint") or "-")
        group = groups.get(key)
        if group is None:
            group = dict(mount)
            group["_paths"] = [path]
            groups[key] = group
            continue
        paths = group["_paths"]
        if path not in paths:
            paths.append(path)

    volumes: list[Mapping[str, Any]] = []
    for group in groups.values():
        paths = group.pop("_paths")
        group["path"] = " + ".join(paths)
        volumes.append(group)
    return volumes


def _server_block(server: Mapping[str, Any], *, detailed: bool) -> dict[str, Any]:
    name = _escape_mrkdwn(
        _truncate_text(
            str(server.get("display_name") or server.get("id") or "Storage server"),
            _MAX_SERVER_NAME_LENGTH,
        )
    )
    volumes = _server_volumes(server)
    issue = _health_issue(server)
    icon = "!" if issue else "•"
    unit = "volume" if len(volumes) == 1 else "volumes"
    text_lines = [f"{icon} *{name}*  ·  *{len(volumes)} {unit}*"]

    if issue:
        text_lines.append(f"_{_escape_mrkdwn(issue)}_")
    else:
        max_items = _MAX_DETAILED_VOLUMES if detailed else _MAX_VOLUMES
        limit = min(len(volumes), max_items)
        chips = [_mount_text(volume) for volume in volumes[:limit]]
        for index in range(0, len(chips), 2):
            text_lines.append("  ·  ".join(chips[index:index + 2]))
        remaining = len(volumes) - limit
        if remaining > 0:
            text_lines.append(f"_+{remaining} volumes omitted_")

    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(text_lines)},
    }


def _server_fallback_text(server: Mapping[str, Any], *, detailed: bool) -> str:
    name = _truncate_text(
        str(server.get("display_name") or server.get("id") or "Storage server"),
        _MAX_SERVER_NAME_LENGTH,
    )
    volumes = _server_volumes(server)
    issue = _health_issue(server)
    if issue:
        return f"! {name} · {issue}"
    max_items = _MAX_DETAILED_VOLUMES if detailed else _MAX_VOLUMES
    limit = min(len(volumes), max_items)
    mount_text = " · ".join(
        f"{_compact_path(volume.get('path') or volume.get('mountpoint'))} "
        f"{int(volume.get('df_use_pct') or 0)}% {_format_bytes(volume.get('df_avail'))}"
        for volume in volumes[:limit]
    )
    return f"• {name} · {mount_text}" if mount_text else f"• {name} · no volumes"


def _filter_servers(servers: list[Any], text: str) -> tuple[list[Mapping[str, Any]], str, bool]:
    valid = [server for server in servers if isinstance(server, Mapping)]
    query = " ".join((text or "").strip().lower().split())
    if query in {"", "all"}:
        return valid, "All", False
    if query == "issues":
        return [server for server in valid if _health_issue(server)], "Issues", False

    matches = [
        server
        for server in valid
        if query in str(server.get("id") or "").lower()
        or query in str(server.get("display_name") or "").lower()
    ]
    return matches, f"Search · {query}", True


def build_storage_command_payload(data: Mapping[str, Any], text: str) -> dict[str, Any]:
    raw_servers = data.get("servers") if isinstance(data, Mapping) else []
    servers, scope, detailed = _filter_servers(raw_servers if isinstance(raw_servers, list) else [], text)
    issue_count = sum(1 for server in servers if _health_issue(server))
    volume_count = sum(len(_server_volumes(server)) for server in servers)
    header_text = _truncate_text(f"Storage Monitor · {scope}", 150)
    fallback_scope = _truncate_text(scope, 150)

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"{len(servers)} servers"},
                {"type": "mrkdwn", "text": f"{volume_count} volumes"},
                {"type": "mrkdwn", "text": "`○ empty`  `● full`"},
            ],
        },
    ]
    if issue_count:
        blocks[1]["elements"].append(
            {"type": "mrkdwn", "text": f"*{issue_count} issue{'s' if issue_count != 1 else ''}*"}
        )

    fallback = [f"[Storage] {fallback_scope} · {len(servers)} servers · {volume_count} volumes"]
    if not servers:
        message = "조건에 맞는 스토리지 서버가 없습니다.\nusage: /storage [all|issues|<server>]"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": message}})
        fallback.append(message)
    else:
        shown = servers[:_MAX_SERVERS]
        for server in shown:
            blocks.append(_server_block(server, detailed=detailed))
            fallback.append(_server_fallback_text(server, detailed=detailed))
        remaining = len(servers) - len(shown)
        if remaining > 0:
            message = f"_+{remaining} servers omitted. use /storage <server>_"
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": message}]})
            fallback.append(f"+{remaining} servers omitted")

    return {
        "response_type": "ephemeral",
        "text": "\n".join(fallback),
        "blocks": blocks,
    }
