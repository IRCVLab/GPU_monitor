"""Note CRUD endpoints — SSH-credential-based auth (no account system)."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Literal, Optional

import paramiko
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator
from sqlalchemy import or_, select

try:
    from ..config import get_settings
    from ..database import AsyncSessionLocal
    from ..models import Note, Server
    from ..note_expiry import serialize_datetime, utc_now, validate_expires_at
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from database import AsyncSessionLocal
    from models import Note, Server
    from note_expiry import serialize_datetime, utc_now, validate_expires_at

logger = logging.getLogger(__name__)
router = APIRouter(tags=["notes"])

NoteKind = Literal["memo", "hold"]


def parse_gpu_indices(raw: str | None) -> list[int]:
    if raw in (None, ""):
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("gpu_indices must be valid JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("gpu_indices must be a JSON array")
    return _canonical_gpu_indices(payload)


def serialize_gpu_indices(indices: list[int]) -> str:
    return json.dumps(_canonical_gpu_indices(indices))


def _canonical_gpu_indices(indices) -> list[int]:
    normalized: list[int] = []
    for value in indices:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("gpu_indices must contain only non-negative integers")
        normalized.append(value)
    return sorted(dict.fromkeys(normalized))


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class NoteOut(BaseModel):
    id: int
    server_id: int
    username: str
    content: str
    created_at: str
    expires_at: Optional[str] = None
    kind: NoteKind
    gpu_indices: list[int] = Field(default_factory=list)

    @field_validator("gpu_indices", mode="before")
    @classmethod
    def _coerce_gpu_indices(cls, value):
        if isinstance(value, str) or value is None:
            return parse_gpu_indices(value)
        if isinstance(value, tuple):
            value = list(value)
        if isinstance(value, list):
            return _canonical_gpu_indices(value)
        return value

    @model_validator(mode="after")
    def _validate_note_out(self):
        gpu_indices = _canonical_gpu_indices(self.gpu_indices)
        if self.kind == "memo" and gpu_indices:
            raise ValueError("memo notes cannot include gpu_indices")
        if self.kind == "hold" and not gpu_indices:
            raise ValueError("hold notes require at least one gpu index")
        self.gpu_indices = gpu_indices
        return self


class NoteCreate(BaseModel):
    username: str
    ssh_password: str
    content: str
    expires_at: datetime
    kind: NoteKind = "memo"
    gpu_indices: list[StrictInt] = Field(default_factory=list)

    @field_validator("gpu_indices", mode="before")
    @classmethod
    def _coerce_gpu_indices(cls, value):
        if value in (None, ""):
            return []
        if isinstance(value, tuple):
            return list(value)
        return value

    @model_validator(mode="after")
    def _validate_hold_contract(self):
        gpu_indices = _canonical_gpu_indices(self.gpu_indices)
        if self.kind == "memo" and gpu_indices:
            raise ValueError("memo notes cannot include gpu_indices")
        if self.kind == "hold" and not gpu_indices:
            raise ValueError("hold notes require at least one gpu index")
        self.gpu_indices = gpu_indices
        return self


class NoteDelete(BaseModel):
    username: Optional[str] = None
    ssh_password: Optional[str] = None
    admin_password: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _matches_admin_password(secret: Optional[str]) -> bool:
    if not secret:
        return False
    settings = get_settings()
    return secret == settings.admin_password


def _try_ssh(host: str, port: int, user: str, password: str) -> bool:
    """Return True if SSH login succeeds."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        return True
    except Exception:
        return False
    finally:
        client.close()


async def _verify_user(server: Server, username: str, ssh_password: str) -> bool:
    """Try SSH login against the target server only."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _try_ssh,
        server.host,
        server.port,
        username,
        ssh_password,
    )


def _note_to_out(n: Note) -> NoteOut:
    return NoteOut(
        id=n.id,
        server_id=n.server_id,
        username=n.username,
        content=n.content,
        created_at=serialize_datetime(n.created_at) if isinstance(n.created_at, datetime) else str(n.created_at),
        expires_at=serialize_datetime(n.expires_at) if isinstance(n.expires_at, datetime) else None,
        kind=n.kind or "memo",
        gpu_indices=n.gpu_indices,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/servers/{server_id}/notes", response_model=list[NoteOut])
async def list_notes(server_id: int):
    now = utc_now()
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Note)
            .where(
                Note.server_id == server_id,
                or_(Note.expires_at.is_(None), Note.expires_at > now),
            )
            .order_by(Note.created_at)
        )
        return [_note_to_out(n) for n in result.scalars().all()]


@router.post("/servers/{server_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(server_id: int, body: NoteCreate):
    is_admin = _matches_admin_password(body.ssh_password)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Server).where(Server.id == server_id))
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        if not is_admin:
            valid = await _verify_user(server, body.username, body.ssh_password)
            if not valid:
                raise HTTPException(status_code=401, detail="SSH authentication failed")

        try:
            expires_at = validate_expires_at(body.expires_at)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        note = Note(
            server_id=server_id,
            username=body.username,
            content=body.content,
            expires_at=expires_at,
            kind=body.kind,
            gpu_indices=serialize_gpu_indices(body.gpu_indices),
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        return _note_to_out(note)


@router.delete("/servers/{server_id}/notes/{note_id}", status_code=204)
async def delete_note(server_id: int, note_id: int, body: NoteDelete):
    # Admin shortcut: explicit admin_password or the same password input used in the UI.
    is_admin = _matches_admin_password(body.admin_password) or _matches_admin_password(body.ssh_password)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Server).where(Server.id == server_id))
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        if not is_admin:
            if not body.username or not body.ssh_password:
                raise HTTPException(
                    status_code=401,
                    detail="Provide username + ssh_password or admin_password",
                )
            valid = await _verify_user(server, body.username, body.ssh_password)
            if not valid:
                raise HTTPException(status_code=401, detail="SSH authentication failed")

        result = await db.execute(
            select(Note).where(Note.id == note_id, Note.server_id == server_id)
        )
        note = result.scalar_one_or_none()
        if not note:
            raise HTTPException(status_code=404, detail="Note not found")

        if not is_admin and note.username != body.username:
            raise HTTPException(status_code=403, detail="Cannot delete another user's note")

        await db.delete(note)
        await db.commit()
