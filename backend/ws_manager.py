"""WebSocket connection manager for broadcasting live GPU metrics."""
import asyncio
import json
import logging
from fastapi import WebSocket

logger = logging.getLogger(__name__)
_SEND_TIMEOUT_SECONDS = 2.0


class ConnectionManager:
    def __init__(self) -> None:
        self._active: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._active.append(websocket)
        logger.info("WebSocket client connected (total=%d)", len(self._active))

    async def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self._active:
            self._active.remove(websocket)
        logger.info("WebSocket client disconnected (total=%d)", len(self._active))

    async def _send_payload(self, websocket: WebSocket, payload: str) -> bool:
        try:
            await asyncio.wait_for(websocket.send_text(payload), timeout=_SEND_TIMEOUT_SECONDS)
            return True
        except Exception:
            return False

    async def broadcast(self, data: dict) -> None:
        active = list(self._active)
        if not active:
            return

        payload = json.dumps(data, separators=(",", ":"))
        results = await asyncio.gather(
            *(self._send_payload(ws, payload) for ws in active),
            return_exceptions=False,
        )

        dead = [ws for ws, ok in zip(active, results) if not ok]
        for ws in dead:
            if ws in self._active:
                self._active.remove(ws)


ws_manager = ConnectionManager()
