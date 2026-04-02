"""Server CRUD endpoints."""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import paramiko
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from ..collectors.ssh_client import SSHClient
    from ..config import get_settings
    from ..crypto import decrypt, encrypt
    from ..database import get_db
    from ..models import Server
except ImportError:  # pragma: no cover - direct execution fallback
    from collectors.ssh_client import SSHClient
    from config import get_settings
    from crypto import decrypt, encrypt
    from database import get_db
    from models import Server

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/servers", tags=["servers"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ServerOut(BaseModel):
    id: int
    name: str
    host: str
    port: int
    ssh_user: str
    has_password: bool
    has_key: bool
    network: str
    display_order: int
    registered_by: Optional[str]
    created_at: str

    class Config:
        from_attributes = True


class ServerCreate(BaseModel):
    admin_password: str
    name: str
    host: str
    port: int = 22
    ssh_user: str
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None
    network: str = "internal"
    display_order: int = 0
    registered_by: Optional[str] = None


class ServerUpdate(BaseModel):
    admin_password: str
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None
    network: Optional[str] = None
    display_order: Optional[int] = None
    registered_by: Optional[str] = None


class AdminRequest(BaseModel):
    admin_password: str


class ReorderItem(BaseModel):
    id: int
    order: int


class ReorderRequest(BaseModel):
    admin_password: str
    items: list[ReorderItem]


class TestConnectionRequest(BaseModel):
    server_id: Optional[int] = None
    admin_password: Optional[str] = None
    host: str
    port: int = 22
    ssh_user: str
    ssh_password: Optional[str] = None
    ssh_private_key: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_admin(provided: str) -> None:
    if provided != get_settings().admin_password:
        raise HTTPException(status_code=401, detail="Invalid admin password")


def _server_to_out(s: Server) -> ServerOut:
    return ServerOut(
        id=s.id,
        name=s.name,
        host=s.host,
        port=s.port,
        ssh_user=s.ssh_user,
        has_password=bool(s.ssh_password),
        has_key=bool(s.ssh_private_key),
        network=s.network,
        display_order=s.display_order,
        registered_by=s.registered_by,
        created_at=s.created_at.isoformat() if isinstance(s.created_at, datetime) else str(s.created_at),
    )


def _sync_test_connection_raw(
    host: str, port: int, ssh_user: str,
    ssh_password: Optional[str], ssh_private_key: Optional[str],
) -> dict:
    """SSH connection test using plaintext credentials (no DB lookup)."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs: dict = dict(
            hostname=host,
            port=port or 22,
            username=ssh_user,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        if ssh_private_key:
            kwargs["pkey"] = SSHClient._load_private_key(ssh_private_key)
        elif ssh_password:
            kwargs["password"] = ssh_password
        else:
            return {"ok": False, "reason": "No credentials provided"}
        client.connect(**kwargs)
        return {"ok": True, "reason": None}
    except paramiko.AuthenticationException:
        return {"ok": False, "reason": "인증 실패 — 사용자명/비밀번호/키를 확인하세요"}
    except Exception as exc:
        return {"ok": False, "reason": f"연결 실패: {exc}"}
    finally:
        client.close()


def _sync_test_connection(server: Server) -> dict:
    """Run SSH connection test synchronously (for executor).
    Uses SSHClient._load_private_key for consistent key-type support."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        kwargs: dict = dict(
            hostname=server.host,
            port=server.port or 22,
            username=server.ssh_user,
            timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )
        if server.ssh_private_key:
            key_str = decrypt(server.ssh_private_key)
            kwargs["pkey"] = SSHClient._load_private_key(key_str)
        elif server.ssh_password:
            kwargs["password"] = decrypt(server.ssh_password)
        else:
            return {"ok": False, "reason": "No credentials configured"}
        client.connect(**kwargs)
        return {"ok": True}
    except paramiko.AuthenticationException:
        return {"ok": False, "reason": "인증 실패 — 사용자명/비밀번호/키를 확인하세요"}
    except Exception as exc:
        return {"ok": False, "reason": f"연결 실패: {exc}"}
    finally:
        client.close()


def _resolve_test_credentials(
    body: TestConnectionRequest,
    server: Optional[Server] = None,
) -> tuple[str, int, str, Optional[str], Optional[str]]:
    ssh_password = body.ssh_password
    ssh_private_key = body.ssh_private_key

    if server is not None and not ssh_password and not ssh_private_key:
        if server.ssh_private_key:
            ssh_private_key = decrypt(server.ssh_private_key)
        elif server.ssh_password:
            ssh_password = decrypt(server.ssh_password)

    return body.host, body.port, body.ssh_user, ssh_password, ssh_private_key


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ServerOut])
async def list_servers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Server).order_by(Server.display_order, Server.id))
    return [_server_to_out(s) for s in result.scalars().all()]


@router.post("", response_model=ServerOut, status_code=201)
async def create_server(body: ServerCreate, db: AsyncSession = Depends(get_db)):
    _check_admin(body.admin_password)

    server = Server(
        name=body.name,
        host=body.host,
        port=body.port,
        ssh_user=body.ssh_user,
        ssh_password=encrypt(body.ssh_password) if body.ssh_password else None,
        ssh_private_key=encrypt(body.ssh_private_key) if body.ssh_private_key else None,
        network=body.network,
        display_order=body.display_order,
        registered_by=body.registered_by,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)

    # Wire into running collector manager
    try:
        try:
            from ..collectors.manager import add_server
        except ImportError:  # pragma: no cover - direct execution fallback
            from collectors.manager import add_server
        await add_server(server)
    except Exception as exc:
        logger.error("Failed to start collector for new server %d: %s", server.id, exc)

    return _server_to_out(server)


@router.post("/test-connection")
async def test_connection_before_save(
    body: TestConnectionRequest,
    db: AsyncSession = Depends(get_db),
):
    """저장 없이 SSH 연결 가능 여부를 사전 검증한다."""
    server: Optional[Server] = None
    if body.server_id is not None:
        if not body.admin_password:
            raise HTTPException(status_code=401, detail="Invalid admin password")
        _check_admin(body.admin_password)
        result = await db.execute(select(Server).where(Server.id == body.server_id))
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

    host, port, ssh_user, ssh_password, ssh_private_key = _resolve_test_credentials(body, server)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _sync_test_connection_raw,
        host,
        port,
        ssh_user,
        ssh_password,
        ssh_private_key,
    )


@router.put("/reorder")
async def reorder_servers(body: ReorderRequest, db: AsyncSession = Depends(get_db)):
    _check_admin(body.admin_password)
    for item in body.items:
        result = await db.execute(select(Server).where(Server.id == item.id))
        server = result.scalar_one_or_none()
        if server:
            server.display_order = item.order
    await db.commit()
    return {"ok": True}


@router.put("/{server_id}", response_model=ServerOut)
async def update_server(server_id: int, body: ServerUpdate, db: AsyncSession = Depends(get_db)):
    _check_admin(body.admin_password)
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if body.name is not None:
        server.name = body.name
    if body.host is not None:
        server.host = body.host
    if body.port is not None:
        server.port = body.port
    if body.ssh_user is not None:
        server.ssh_user = body.ssh_user
    if body.ssh_password is not None:
        server.ssh_password = encrypt(body.ssh_password)
        server.ssh_private_key = None   # 비밀번호로 전환 시 키 삭제
    if body.ssh_private_key is not None:
        server.ssh_private_key = encrypt(body.ssh_private_key)
        server.ssh_password = None      # 키로 전환 시 비밀번호 삭제
    if body.network is not None:
        server.network = body.network
    if body.display_order is not None:
        server.display_order = body.display_order
    if body.registered_by is not None:
        server.registered_by = body.registered_by

    await db.commit()
    await db.refresh(server)

    # Restart collector with updated config
    try:
        try:
            from ..collectors.manager import update_server as collector_update
        except ImportError:  # pragma: no cover - direct execution fallback
            from collectors.manager import update_server as collector_update
        await collector_update(server)
    except Exception as exc:
        logger.error("Failed to restart collector for server %d: %s", server_id, exc)

    return _server_to_out(server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: int, body: AdminRequest, db: AsyncSession = Depends(get_db)):
    _check_admin(body.admin_password)
    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Stop collector before deleting from DB
    try:
        try:
            from ..collectors.manager import remove_server
        except ImportError:  # pragma: no cover - direct execution fallback
            from collectors.manager import remove_server
        await remove_server(server_id)
    except Exception as exc:
        logger.error("Failed to stop collector for server %d: %s", server_id, exc)

    await db.delete(server)
    await db.commit()


@router.post("/{server_id}/test")
async def test_server_connection(
    server_id: int,
    body: AdminRequest,
    db: AsyncSession = Depends(get_db),
):
    _check_admin(body.admin_password)

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync_test_connection, server)
