"""Slack Socket Mode runner for /gpu and /status takeover."""
from __future__ import annotations

import logging
import threading

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

try:
    from .config import get_settings
    from .slack_gpu import build_gpu_command_payload
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from slack_gpu import build_gpu_command_payload

logger = logging.getLogger(__name__)


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

        self._handler = SocketModeHandler(app, settings.slack_app_token)
        self._thread = threading.Thread(
            target=self._run,
            name="slack-socket-mode",
            daemon=True,
        )
        self._thread.start()
        self._started = True
        logger.info("Slack Socket Mode started for /gpu and /status")

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
