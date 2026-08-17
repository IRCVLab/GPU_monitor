"""Slack Socket Mode runner for GPU and Storage Monitor commands."""
from __future__ import annotations

import logging
import threading

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

try:
    from .config import get_settings
    from .slack_gpu import build_gpu_command_payload
    from .slack_storage import build_storage_command_payload, fetch_storage_summary
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from slack_gpu import build_gpu_command_payload
    from slack_storage import build_storage_command_payload, fetch_storage_summary

logger = logging.getLogger(__name__)


def storage_query_for_command(command: dict) -> str | None:
    command_name = str(command.get("command") or "").lower()
    text = str(command.get("text") or "").strip()
    if command_name == "/storage":
        return text
    if command_name not in {"/gpu", "/status"}:
        return None
    head, separator, tail = text.partition(" ")
    if head.lower() != "storage":
        return None
    return tail.strip() if separator else ""


class SlackSocketService:
    def __init__(self) -> None:
        self._handler: SocketModeHandler | None = None
        self._thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return

        settings = get_settings()
        if not settings.slack_bot_token or not settings.slack_app_token:
            logger.info("Slack Socket Mode disabled: token/app_token not configured")
            return

        app = App(
            token=settings.slack_bot_token,
            request_verification_enabled=False,
        )

        @app.command("/gpu")
        @app.command("/status")
        def _handle_gpu_command(ack, command, respond):
            ack()
            try:
                storage_query = storage_query_for_command(command)
                if storage_query is not None:
                    payload = build_storage_command_payload(
                        fetch_storage_summary(settings.storage_monitor_api_url),
                        storage_query,
                    )
                    respond(**payload)
                    return
                try:
                    from .collectors.manager import get_current_state
                except ImportError:  # pragma: no cover - direct execution fallback
                    from collectors.manager import get_current_state
                payload = build_gpu_command_payload(
                    get_current_state(),
                    command.get("text", "") or "",
                )
                respond(**payload)
            except Exception as exc:  # pragma: no cover - operational guard
                logger.exception("Slack Socket Mode command failed: %s", exc)
                respond(
                    response_type="ephemeral",
                    text="GPU 상태 응답 생성에 실패했습니다.",
                )

        @app.command("/storage")
        def _handle_storage_command(ack, command, respond):
            ack()
            try:
                payload = build_storage_command_payload(
                    fetch_storage_summary(settings.storage_monitor_api_url),
                    storage_query_for_command(command) or "",
                )
                respond(**payload)
            except Exception as exc:  # pragma: no cover - operational guard
                logger.exception("Slack Storage command failed: %s", exc)
                respond(
                    response_type="ephemeral",
                    text="Storage Monitor 응답을 불러오지 못했습니다.",
                )

        self._handler = SocketModeHandler(app, settings.slack_app_token)
        self._thread = threading.Thread(
            target=self._run,
            name="slack-socket-mode",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info("Slack Socket Mode started for /gpu, /status, and /storage")

    def _run(self) -> None:
        if self._handler is None:
            return
        try:
            self._handler.start()
        except Exception as exc:  # pragma: no cover - operational guard
            logger.exception("Slack Socket Mode stopped unexpectedly: %s", exc)

    def stop(self) -> None:
        if self._handler is not None:
            try:
                self._handler.close()
            except Exception as exc:  # pragma: no cover - operational guard
                logger.debug("Slack Socket Mode close failed: %s", exc)
        self._handler = None
        self._thread = None
        self._started = False


slack_socket_service = SlackSocketService()
