"""Slack slash command handler for /gpu."""
import hashlib
import hmac
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

try:
    from ..config import get_settings
    from ..slack_gpu import build_gpu_command_payload
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from slack_gpu import build_gpu_command_payload

router = APIRouter(prefix="/slack", tags=["slack"])


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

async def _verify_slack_signature(request: Request, raw_body: bytes) -> None:
    settings = get_settings()
    signing_secret = settings.slack_signing_secret
    if not signing_secret:
        raise HTTPException(status_code=503, detail="Slack signing secret not configured")

    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    slack_signature = request.headers.get("X-Slack-Signature", "")

    # Reject stale requests (replay protection)
    try:
        if abs(time.time() - float(timestamp)) > 300:
            raise HTTPException(status_code=403, detail="Request timestamp too old")
    except (ValueError, TypeError):
        raise HTTPException(status_code=403, detail="Invalid timestamp")

    base_string = f"v0:{timestamp}:{raw_body.decode()}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), base_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(computed, slack_signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")


@router.post("/gpu")
async def slack_gpu_command(request: Request):
    settings = get_settings()

    if not settings.slack_signing_secret:
        raise HTTPException(status_code=503, detail="Slack signing secret required")

    raw_body = await request.body()
    await _verify_slack_signature(request, raw_body)
    form = parse_qs(raw_body.decode())
    text = (form.get("text") or [""])[0]

    try:
        try:
            from ..collectors.manager import get_current_state
        except ImportError:  # pragma: no cover - direct execution fallback
            from collectors.manager import get_current_state
        state = get_current_state()
    except ImportError:
        state = {}

    return build_gpu_command_payload(state, text or "")
