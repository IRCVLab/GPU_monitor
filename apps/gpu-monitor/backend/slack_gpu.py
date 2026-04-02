"""Shared Slack /gpu response builders for HTTP and Socket Mode."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_KST = ZoneInfo("Asia/Seoul")
_MAX_SERVERS = 12
_MAX_GPU_FIELDS = 8
_STATUS_ORDER = {"offline": 0, "degraded": 1, "unknown": 2, "online": 3}
_STATUS_ICON = {"online": "•", "offline": "×", "degraded": "!", "unknown": "?"}
_SCOPE_LABELS = {
    "internal": "Internal",
    "external": "External",
    "all": "All",
    "offline": "Offline",
    "degraded": "Degraded",
}


def _escape_mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _format_absolute_time(value: str | None) -> str | None:
    dt = _parse_timestamp(value)
    if dt is None:
        return None
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _format_compact_time(value: str | None) -> str | None:
    dt = _parse_timestamp(value)
    if dt is None:
        return None
    dt_kst = dt.astimezone(_KST)
    now_kst = datetime.now(tz=_KST)
    if dt_kst.date() == now_kst.date():
        return dt_kst.strftime("%H:%M:%S")
    return dt_kst.strftime("%m-%d %H:%M")


def _format_memory_gb(used_mb: int | None, total_mb: int | None) -> str:
    used = (used_mb or 0) / 1024
    total = (total_mb or 0) / 1024
    return f"{used:.1f}/{total:.1f}GB"


def _format_memory_compact(used_mb: int | None, total_mb: int | None) -> str:
    used = (used_mb or 0) / 1024
    total = (total_mb or 0) / 1024
    total_text = f"{int(total)}" if float(total).is_integer() else f"{total:.1f}"
    return f"{used:.1f}/{total_text}G"


def _format_users(users: list[str]) -> str:
    if not users:
        return "idle"
    if len(users) <= 2:
        return ", ".join(users)
    return f"{', '.join(users[:2])} +{len(users) - 2}"


def _is_gpu_active(gpu: dict) -> bool:
    users = gpu.get("users") or []
    utilization = int(gpu.get("utilization") or 0)
    power_draw = int(gpu.get("power_draw") or 0)
    memory_used = int(gpu.get("memory_used") or 0)
    return bool(users) or utilization >= 10 or power_draw >= 60 or memory_used >= 1024


def _gpu_icon(gpu: dict) -> str:
    if not _is_gpu_active(gpu):
        return "○"
    utilization = int(gpu.get("utilization") or 0)
    total = int(gpu.get("memory_total") or 0)
    used = int(gpu.get("memory_used") or 0)
    memory_ratio = (used / total) if total else 0.0
    score = max(utilization / 100.0, memory_ratio)
    if score >= 0.9:
        return "●"
    if score >= 0.6:
        return "◐"
    return "◔"


def _sort_servers(info: dict) -> tuple:
    return (
        _STATUS_ORDER.get(info.get("status", "unknown"), 9),
        int(info.get("display_order") or 0),
        str(info.get("server_name") or "").lower(),
        int(info.get("port") or 0),
    )


def _usage_text() -> str:
    return (
        "usage: /gpu [internal|external|all|offline|degraded|<server-name|host|port>]"
    )


def _filter_state(state: dict, text: str) -> tuple[list[dict], str, str | None]:
    items = list(state.values())
    query = " ".join((text or "").strip().lower().split())

    if query in ("", "internal"):
        filtered = [info for info in items if info.get("network") == "internal"]
        return sorted(filtered, key=_sort_servers), "internal", None
    if query == "external":
        filtered = [info for info in items if info.get("network") == "external"]
        return sorted(filtered, key=_sort_servers), "external", None
    if query == "all":
        return sorted(items, key=_sort_servers), "all", None
    if query == "offline":
        filtered = [info for info in items if info.get("status") == "offline"]
        return sorted(filtered, key=_sort_servers), "offline", None
    if query == "degraded":
        filtered = [info for info in items if info.get("status") == "degraded"]
        return sorted(filtered, key=_sort_servers), "degraded", None

    filtered = []
    for info in items:
        server_name = str(info.get("server_name") or "").lower()
        host = str(info.get("host") or "").lower()
        port = str(info.get("port") or "")
        host_port = f"{host}:{port}" if host and port else host
        if query in server_name or query in host or query == port or query in host_port:
            filtered.append(info)

    if filtered:
        return sorted(filtered, key=_sort_servers), f"query={query}", None
    return [], f"query={query}", f"검색 결과가 없습니다: {query}\n{_usage_text()}"


def _scope_title(scope: str) -> str:
    if scope in _SCOPE_LABELS:
        return _SCOPE_LABELS[scope]
    if scope.startswith("query="):
        return f"Search · {scope.split('=', 1)[1]}"
    return scope.title()


def _summary_stats(servers: list[dict]) -> dict:
    counts = {"online": 0, "degraded": 0, "offline": 0, "unknown": 0}
    active_gpus = 0
    total_gpus = 0
    latest_seen: str | None = None

    for info in servers:
        status = str(info.get("status") or "unknown")
        counts[status if status in counts else "unknown"] += 1
        for gpu in info.get("gpus") or []:
            total_gpus += 1
            if _is_gpu_active(gpu):
                active_gpus += 1
        seen = info.get("last_seen")
        if seen and (latest_seen is None or seen > latest_seen):
            latest_seen = seen

    return {
        "counts": counts,
        "active_gpus": active_gpus,
        "total_gpus": total_gpus,
        "latest_seen": latest_seen,
    }


def _summary_line(scope: str, servers: list[dict]) -> str:
    stats = _summary_stats(servers)
    counts = stats["counts"]
    parts = [
        f"scope={scope}",
        f"servers={len(servers)}",
        f"online={counts['online']}",
        f"degraded={counts['degraded']}",
        f"offline={counts['offline']}",
    ]
    if counts["unknown"]:
        parts.append(f"unknown={counts['unknown']}")
    if stats["total_gpus"]:
        parts.append(f"busy_gpus={stats['active_gpus']}/{stats['total_gpus']}")
    latest_seen = stats["latest_seen"]
    if latest_seen:
        parts.append(f"updated_at={_format_absolute_time(latest_seen)}")
    return " · ".join(parts)


def _server_fallback_text(info: dict) -> str:
    status = str(info.get("status") or "unknown")
    icon = _STATUS_ICON.get(status, "⚪")
    server_name = str(info.get("server_name") or f"Server {info.get('server_id')}")
    host = str(info.get("host") or "-")
    port = info.get("port")
    host_token = f"{host}:{port}" if host and port else host
    lines = [f"{icon} {server_name} {host_token}"]

    status_reason = info.get("status_reason") or {}
    reason = str(status_reason.get("message") or "").strip()
    last_seen = _format_absolute_time(info.get("last_seen"))
    offline_since = _format_absolute_time(info.get("offline_since"))

    if status == "offline":
        detail = "offline"
        if reason:
            detail += f" · reason={reason}"
        if offline_since:
            detail += f" · since={offline_since}"
        lines.append(detail)
        return "\n".join(lines)

    if status == "degraded":
        detail = "degraded"
        if reason:
            detail += f" · reason={reason}"
        if last_seen:
            detail += f" · last_seen={last_seen}"
        lines.append(detail)

    gpus = info.get("gpus") or []
    active_gpus = [gpu for gpu in gpus if _is_gpu_active(gpu)]
    idle_count = max(len(gpus) - len(active_gpus), 0)

    if active_gpus:
        for gpu in active_gpus[:4]:
            lines.append(
                f"G{gpu.get('index', '?')} "
                f"{int(gpu.get('utilization') or 0)}% "
                f"{_format_memory_gb(gpu.get('memory_used'), gpu.get('memory_total'))} "
                f"· user={_format_users(gpu.get('users') or [])}"
            )
        remaining = len(active_gpus) - 4
        if remaining > 0:
            lines.append(f"active GPUs +{remaining}")
    elif status == "online":
        lines.append(f"GPU idle {len(gpus)}/{len(gpus)}")

    if idle_count and active_gpus:
        lines.append(f"idle {idle_count}/{len(gpus)} GPUs")

    system = info.get("system") or {}
    if system:
        ram_used = (system.get("ram_used") or 0) / 1024
        ram_total = (system.get("ram_total") or 0) / 1024
        summary = (
            f"cpu={float(system.get('cpu_percent') or 0):.0f}%"
            f" · ram={ram_used:.0f}/{ram_total:.0f}GB"
        )
        if last_seen:
            summary += f" · last_seen={last_seen}"
        lines.append(summary)
    elif last_seen:
        lines.append(f"last_seen={last_seen}")

    return "\n".join(lines)


def _gpu_chip_text(gpu: dict) -> str:
    index = gpu.get("index", "?")
    utilization = int(gpu.get("utilization") or 0)
    memory = _format_memory_compact(gpu.get("memory_used"), gpu.get("memory_total"))
    users = _format_users(gpu.get("users") or [])
    user_text = _escape_mrkdwn(users)
    return f"`{_gpu_icon(gpu)} G{index} {utilization}% {memory}` {user_text}"


def _server_meta_line(info: dict, active_count: int, total_count: int) -> str:
    system = info.get("system") or {}
    chips: list[str] = []
    if system:
        cpu_percent = float(system.get("cpu_percent") or 0.0)
        chips.append(f"`CPU {cpu_percent:.0f}%`")
        ram_used = (system.get("ram_used") or 0) / 1024
        ram_total = (system.get("ram_total") or 0) / 1024
        chips.append(f"`RAM {ram_used:.0f}/{ram_total:.0f}G`")
    if total_count:
        chips.append(f"`GPU {active_count}/{total_count}`")

    last_seen = _format_compact_time(info.get("last_seen"))
    if last_seen:
        chips.append(f"`{last_seen}`")

    reason = str((info.get("status_reason") or {}).get("message") or "").strip()
    if reason and info.get("status") == "degraded":
        chips.append(f"_degraded: {_escape_mrkdwn(reason)}_")

    return " ".join(chips)


def _chunk_text_lines(items: list[str], size: int = 2) -> list[str]:
    lines: list[str] = []
    for idx in range(0, len(items), size):
        lines.append("  ·  ".join(items[idx:idx + size]))
    return lines


def _server_block(info: dict) -> dict:
    status = str(info.get("status") or "unknown")
    icon = _STATUS_ICON.get(status, "?")
    server_name = _escape_mrkdwn(str(info.get("server_name") or f"Server {info.get('server_id')}"))
    host = _escape_mrkdwn(str(info.get("host") or "-"))
    port = info.get("port")
    host_token = f"{host}:{port}" if host and port else host
    headline = f"{icon} *{server_name}* `{host_token}`"

    if status == "offline":
        reason = str((info.get("status_reason") or {}).get("message") or "").strip()
        since = _format_compact_time(info.get("offline_since"))
        detail_parts: list[str] = []
        if reason:
            detail_parts.append(f"_{_escape_mrkdwn(reason)}_")
        if since:
            detail_parts.append(f"`{since}`")
        detail = " • ".join(detail_parts) if detail_parts else "_Offline_"
        return {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{headline}\n{detail}"},
        }

    gpus = info.get("gpus") or []
    active_gpus = [gpu for gpu in gpus if _is_gpu_active(gpu)]
    idle_count = max(len(gpus) - len(active_gpus), 0)
    text_lines = [headline]
    meta_line = _server_meta_line(info, len(active_gpus), len(gpus))
    if meta_line:
        text_lines.append(meta_line)

    gpu_lines = _chunk_text_lines(
        [_gpu_chip_text(gpu) for gpu in active_gpus[:_MAX_GPU_FIELDS]],
        size=2,
    )
    remaining_active = len(active_gpus) - min(len(active_gpus), _MAX_GPU_FIELDS)
    if gpu_lines:
        text_lines.extend(gpu_lines)
    elif len(gpus):
        text_lines.append(f"`Idle {len(gpus)}/{len(gpus)} GPU`")
    if idle_count and active_gpus:
        text_lines.append(f"_idle {idle_count}/{len(gpus)}_")
    if remaining_active > 0:
        text_lines.append(f"_+{remaining_active} active GPU_")

    return {
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(text_lines)},
    }


def build_gpu_command_payload(state: dict, text: str) -> dict:
    servers, scope, error_text = _filter_state(state, text)

    if error_text:
        return {
            "response_type": "ephemeral",
            "text": error_text,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": error_text},
                }
            ],
        }

    summary_line = _summary_line(scope, servers)
    stats = _summary_stats(servers)
    latest_seen = stats["latest_seen"]
    updated_text = _format_compact_time(latest_seen) if latest_seen else "-"

    fallback_lines = [f"[GPU] {summary_line}"]
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"GPU Snapshot · {_scope_title(scope)}"},
        },
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"`{len(servers)} servers`"},
                {"type": "mrkdwn", "text": f"`{stats['active_gpus']}/{stats['total_gpus'] or 0} busy`"},
                {
                    "type": "mrkdwn",
                    "text": f"`on {stats['counts']['online']} / deg {stats['counts']['degraded']} / off {stats['counts']['offline']}`",
                },
                {"type": "mrkdwn", "text": f"`upd {updated_text}`"},
            ],
        },
    ]

    if not servers:
        empty_text = f"조건에 맞는 서버가 없습니다.\n{_usage_text()}"
        fallback_lines.append(empty_text)
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": empty_text},
            }
        )
        return {
            "response_type": "ephemeral",
            "text": "\n".join(fallback_lines),
            "blocks": blocks,
        }

    shown = servers[:_MAX_SERVERS]
    for info in shown:
        fallback_lines.append(_server_fallback_text(info))
        blocks.append(_server_block(info))

    remaining = len(servers) - len(shown)
    if remaining > 0:
        more_text = f"_+{remaining} server(s) omitted. refine with /gpu offline, /gpu degraded, /gpu <server-name>_"
        fallback_lines.append(f"+{remaining} servers omitted")
        blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": more_text}],
            }
        )

    return {
        "response_type": "ephemeral",
        "text": "\n".join(fallback_lines),
        "blocks": blocks,
    }
