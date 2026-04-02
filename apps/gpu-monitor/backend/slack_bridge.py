"""Minimal HTTP bridge so Slack can keep hitting port 8000 during cutover."""
from __future__ import annotations

import json

import httpx
from fastapi import FastAPI, Request, Response

TARGET_BASE = "http://127.0.0.1:8001"

app = FastAPI(title="GPU Monitor Slack Bridge", version="1.0.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/slack/gpu")
async def slack_gpu_bridge(request: Request) -> Response:
    body = await request.body()
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() in {
            "content-type",
            "x-slack-request-timestamp",
            "x-slack-signature",
        }
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{TARGET_BASE}/slack/gpu",
            content=body,
            headers=headers,
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "slack-bridge", "target": TARGET_BASE}
