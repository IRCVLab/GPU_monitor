"""Note CRUD endpoints — SSH-credential-based auth (no account system)."""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import paramiko
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

try:
    from ..config import get_settings
    from ..database import AsyncSessionLocal
    from ..models import Note, Server
except ImportError:  # pragma: no cover - direct execution fallback
    from config import get_settings
    from database import AsyncSessionLocal
    from models import Note, Server

logger = logging.getLogger(__name__)
router = APIRouter(tags=["notes"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class NoteOut(BaseModel):
    id: int
    server_id: int
    username: str
    content: str
    created_at: str


class NoteCreate(BaseModel):
    username: str
    ssh_password: str
    content: str


class NoteDelete(BaseModel):
    username: Optional[str] = None
    ssh_password: Optional[str] = None
    admin_password: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

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
        created_at=n.created_at.isoformat() if isinstance(n.created_at, datetime) else str(n.created_at),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/servers/{server_id}/notes", response_model=list[NoteOut])
async def list_notes(server_id: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Note).where(Note.server_id == server_id).order_by(Note.created_at)
        )
        return [_note_to_out(n) for n in result.scalars().all()]


@router.post("/servers/{server_id}/notes", response_model=NoteOut, status_code=201)
async def create_note(server_id: int, body: NoteCreate):
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Server).where(Server.id == server_id))
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        valid = await _verify_user(server, body.username, body.ssh_password)
        if not valid:
            raise HTTPException(status_code=401, detail="SSH authentication failed")

        note = Note(server_id=server_id, username=body.username, content=body.content)
        db.add(note)
        await db.commit()
        await db.refresh(note)
        return _note_to_out(note)


@router.delete("/servers/{server_id}/notes/{note_id}", status_code=204)
async def delete_note(server_id: int, note_id: int, body: NoteDelete):
    settings = get_settings()

    # Admin shortcut
    is_admin = body.admin_password and body.admin_password == settings.admin_password

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
