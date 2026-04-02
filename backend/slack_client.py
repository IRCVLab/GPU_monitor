"""Slack notification helpers with spam prevention."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

try:
    from .config import get_settings
    from .database import AsyncSessionLocal
    from .models import SlackAlertLog
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from database import AsyncSessionLocal
    from models import SlackAlertLog

logger = logging.getLogger(__name__)

_OFFLINE_COOLDOWN_MINUTES = 10
_RECOVERY_MIN_DOWNTIME_SECONDS = 300  # 5 minutes
_KST = ZoneInfo("Asia/Seoul")


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _escape_mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _parse_timestamp(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _format_absolute_time(value: str | datetime | None) -> str | None:
    dt = _parse_timestamp(value)
    if dt is None:
        return None
    return dt.astimezone(_KST).strftime("%Y-%m-%d %H:%M:%S KST")


def _format_duration(seconds: int) -> str:
    minutes, seconds = divmod(max(seconds, 0), 60)
    hours, minutes = divmod(minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if hours or minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _reason_field(status_reason: dict | None) -> str | None:
    if not status_reason:
        return None
    code = str(status_reason.get("code") or "").strip()
    message = str(status_reason.get("message") or "").strip()
    source = str(status_reason.get("source") or "").strip()

    reason = ""
    if code and message:
        reason = f"reason={code} ({message})"
    elif code:
        reason = f"reason={code}"
    elif message:
        reason = f"reason={message}"

    if reason and source:
        return f"{reason} · source={source}"
    return reason or None


def _join_fields(fields: list[str]) -> str:
    return " · ".join(field for field in fields if field)


def _build_slack_payload(
    *,
    server_name: str,
    server_id: int,
    server_host: str,
    server_port: int | None,
    network: str,
    event_type: str,
    severity: str,
    headline: str,
    icon: str,
    detail_fields: list[str],
) -> tuple[str, list[dict[str, Any]]]:
    safe_server_name = _escape_mrkdwn(server_name)
    summary_fallback = f"[GPU][{severity}] {server_name} {headline}"
    summary_block = f"{icon} *[GPU][{severity}]* {safe_server_name} {headline}"
    host_token = server_host or "-"
    if server_host and server_port is not None:
        host_token = f"{server_host}:{server_port}"
    context_line = _join_fields([
        f"server={server_name}",
        f"host={host_token}",
        f"network={network or '-'}",
        f"event={event_type}",
    ])
    detail_line = _join_fields([f"server_id={server_id}", *detail_fields])
    safe_context_line = _escape_mrkdwn(context_line)
    safe_detail_line = _escape_mrkdwn(detail_line) if detail_line else ""

    text_lines = [summary_fallback, context_line]
    if detail_line:
        text_lines.append(detail_line)

    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": summary_block,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": safe_context_line,
            },
        },
    ]
    if detail_line:
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": safe_detail_line,
                    }
                ],
            }
        )

    return "\n".join(text_lines), blocks


async def _post_message(text: str, blocks: list[dict[str, Any]] | None = None) -> bool:
    settings = get_settings()
    token = settings.slack_bot_token
    channel = settings.slack_log_channel

    if not token or not channel:
        return False

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "channel": channel,
                    "text": text,
                    "blocks": blocks or [],
                    "unfurl_links": False,
                    "unfurl_media": False,
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Slack postMessage request failed: %s", exc)
        return False

    if not data.get("ok"):
        logger.warning("Slack postMessage failed: %s", data.get("error"))
        return False

    return True


async def _last_alert_at(server_id: int, event_type: str) -> datetime | None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(SlackAlertLog.sent_at)
            .where(
                SlackAlertLog.server_id == server_id,
                SlackAlertLog.event_type == event_type,
            )
            .order_by(SlackAlertLog.sent_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        # sent_at is naive UTC from func.now(); make it timezone-aware
        if row.tzinfo is None:
            return row.replace(tzinfo=timezone.utc)
        return row


async def _log_alert(server_id: int, event_type: str) -> None:
    async with AsyncSessionLocal() as session:
        session.add(SlackAlertLog(server_id=server_id, event_type=event_type))
        await session.commit()


async def _send_event(
    *,
    server_name: str,
    server_id: int,
    server_host: str = "",
    server_port: int | None = None,
    network: str = "",
    event_type: str,
    severity: str,
    headline: str,
    icon: str,
    detail_fields: list[str],
    cooldown_minutes: int | None = None,
) -> bool:
    if cooldown_minutes is not None:
        last = await _last_alert_at(server_id, event_type)
        if last and (_utcnow() - last) < timedelta(minutes=cooldown_minutes):
            return False

    text, blocks = _build_slack_payload(
        server_name=server_name,
        server_id=server_id,
        server_host=server_host,
        server_port=server_port,
        network=network,
        event_type=event_type,
        severity=severity,
        headline=headline,
        icon=icon,
        detail_fields=detail_fields,
    )
    if not await _post_message(text, blocks):
        return False

    await _log_alert(server_id, event_type)
    return True


async def notify_offline(
    server_name: str,
    server_id: int,
    server_host: str = "",
    server_port: int | None = None,
    network: str = "",
    detected_at: str | None = None,
    last_seen: str | None = None,
    status_reason: dict | None = None,
) -> None:
    """Send offline alert if no offline alert was sent in the last 10 minutes."""
    try:
        detected_at_text = _format_absolute_time(detected_at) or _format_absolute_time(_utcnow())
        last_seen_text = _format_absolute_time(last_seen)
        detail_fields = [
            f"detected_at={detected_at_text}",
        ]
        if last_seen_text:
            detail_fields.append(f"last_seen={last_seen_text}")
        reason_field = _reason_field(status_reason)
        if reason_field:
            detail_fields.append(reason_field)

        if await _send_event(
            server_name=server_name,
            server_id=server_id,
            server_host=server_host,
            server_port=server_port,
            network=network,
            event_type="server_offline",
            severity="CRITICAL",
            headline="offline",
            icon=":red_circle:",
            detail_fields=detail_fields,
            cooldown_minutes=_OFFLINE_COOLDOWN_MINUTES,
        ):
            logger.info("Slack offline alert sent for %s", server_name)
    except Exception as exc:
        logger.error("notify_offline failed for %s: %s", server_name, exc)


async def notify_recovery(
    server_name: str,
    server_id: int,
    downtime_seconds: int,
    server_host: str = "",
    server_port: int | None = None,
    network: str = "",
    recovered_at: str | None = None,
) -> None:
    """Send recovery alert only if downtime exceeded 5 minutes."""
    try:
        if downtime_seconds < _RECOVERY_MIN_DOWNTIME_SECONDS:
            return

        recovered_at_text = _format_absolute_time(recovered_at) or _format_absolute_time(_utcnow())
        detail_fields = [
            f"recovered_at={recovered_at_text}",
            f"downtime={_format_duration(downtime_seconds)}",
        ]
        if await _send_event(
            server_name=server_name,
            server_id=server_id,
            server_host=server_host,
            server_port=server_port,
            network=network,
            event_type="server_online",
            severity="INFO",
            headline="recovered",
            icon=":large_green_circle:",
            detail_fields=detail_fields,
        ):
            logger.info("Slack recovery alert sent for %s", server_name)
    except Exception as exc:
        logger.error("notify_recovery failed for %s: %s", server_name, exc)


async def notify_degraded(
    server_name: str,
    server_id: int,
    server_host: str = "",
    server_port: int | None = None,
    network: str = "",
    detected_at: str | None = None,
    status_reason: dict | None = None,
) -> None:
    """Send degraded alert if no degraded alert was sent in the last 10 minutes."""
    try:
        detected_at_text = _format_absolute_time(detected_at) or _format_absolute_time(_utcnow())
        detail_fields = [
            f"detected_at={detected_at_text}",
        ]
        reason_field = _reason_field(status_reason)
        if reason_field:
            detail_fields.append(reason_field)

        if await _send_event(
            server_name=server_name,
            server_id=server_id,
            server_host=server_host,
            server_port=server_port,
            network=network,
            event_type="server_degraded",
            severity="WARNING",
            headline="degraded",
            icon=":large_yellow_circle:",
            detail_fields=detail_fields,
            cooldown_minutes=_OFFLINE_COOLDOWN_MINUTES,
        ):
            logger.info("Slack degraded alert sent for %s", server_name)
    except Exception as exc:
        logger.error("notify_degraded failed for %s: %s", server_name, exc)


async def notify_connection_alert(
    server_name: str,
    server_id: int,
    elapsed_seconds: int,
    server_host: str = "",
    server_port: int | None = None,
    network: str = "",
    detected_at: str | None = None,
    last_seen: str | None = None,
    status_reason: dict | None = None,
) -> None:
    """Send a critical alert once a server has stayed unreachable for several minutes."""
    try:
        detected_at_text = _format_absolute_time(detected_at) or _format_absolute_time(_utcnow())
        last_seen_text = _format_absolute_time(last_seen)
        detail_fields = [
            f"detected_at={detected_at_text}",
            f"elapsed={_format_duration(elapsed_seconds)}",
        ]
        if last_seen_text:
            detail_fields.append(f"last_seen={last_seen_text}")
        reason_field = _reason_field(status_reason)
        if reason_field:
            detail_fields.append(reason_field)

        if await _send_event(
            server_name=server_name,
            server_id=server_id,
            server_host=server_host,
            server_port=server_port,
            network=network,
            event_type="connection_alert",
            severity="CRITICAL",
            headline="connection alert",
            icon=":rotating_light:",
            detail_fields=detail_fields,
            cooldown_minutes=_OFFLINE_COOLDOWN_MINUTES,
        ):
            logger.info("Slack connection alert sent for %s", server_name)
    except Exception as exc:
        logger.error("notify_connection_alert failed for %s: %s", server_name, exc)
