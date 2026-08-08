from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile
from urllib.parse import quote, unquote

from sqlalchemy.engine import make_url


class LiveDatabaseError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseSnapshot:
    server_count: int
    server_names: tuple[str, ...]
    note_count: int
    integrity_ok: bool


def sqlite_path_from_url(database_url: str) -> Path:
    try:
        url = make_url(database_url)
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise LiveDatabaseError(f"Invalid SQLite database URL: {exc}") from exc

    if not url.drivername.startswith("sqlite"):
        raise LiveDatabaseError(f"Expected a SQLite database URL, got {url.drivername!r}")

    database = url.database
    if not database or database == ":memory:":
        raise LiveDatabaseError("SQLite database URL must point to a file-backed database")

    return Path(unquote(database)).expanduser()


def _readonly_sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(path))}?mode=ro"


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(_readonly_sqlite_uri(path), uri=True)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def inspect_live_database(path: Path) -> DatabaseSnapshot:
    if not path.exists():
        raise LiveDatabaseError(f"Live database missing at {path}")
    if not path.is_file():
        raise LiveDatabaseError(f"Live database path is not a file: {path}")

    try:
        with _connect_readonly(path) as conn:
            integrity_rows = conn.execute("PRAGMA integrity_check").fetchall()
            if len(integrity_rows) != 1 or integrity_rows[0][0] != "ok":
                raise LiveDatabaseError(f"Live database corrupt at {path}: {integrity_rows[0][0]}")

            if not _table_exists(conn, "servers"):
                raise LiveDatabaseError(f"Live database missing servers table at {path}")

            server_columns = _table_columns(conn, "servers")
            if "name" not in server_columns:
                raise LiveDatabaseError(f"Live database servers table missing name column at {path}")

            order_by = "id"
            if "display_order" in server_columns and "id" in server_columns:
                order_by = "display_order, id"
            elif "id" not in server_columns:
                order_by = "name"

            server_names = tuple(
                row[0] for row in conn.execute(f"SELECT name FROM servers ORDER BY {order_by}").fetchall()
            )

            note_count = 0
            if _table_exists(conn, "notes"):
                note_count = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
    except LiveDatabaseError:
        raise
    except sqlite3.DatabaseError as exc:
        raise LiveDatabaseError(f"Live database corrupt or unreadable at {path}") from exc
    except OSError as exc:
        raise LiveDatabaseError(f"Unable to inspect live database at {path}") from exc

    return DatabaseSnapshot(
        server_count=len(server_names),
        server_names=server_names,
        note_count=note_count,
        integrity_ok=True,
    )


def backup_live_database(source: Path, backup_dir: Path, keep: int) -> Path:
    if keep < 1:
        raise LiveDatabaseError("Backup retention must be at least 1")
    if not source.exists():
        raise LiveDatabaseError(f"Live database missing at {source}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    published_path = backup_dir / f"gpu-monitor-{timestamp}.db"
    temp_fd, temp_name = tempfile.mkstemp(prefix=".gpu-monitor-", suffix=".db", dir=backup_dir)
    os.close(temp_fd)
    temp_path = Path(temp_name)

    try:
        os.chmod(temp_path, 0o600)
        with _connect_readonly(source) as source_conn, sqlite3.connect(temp_path) as backup_conn:
            source_conn.backup(backup_conn)
            backup_conn.commit()
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, published_path)
        os.chmod(published_path, 0o600)
    except sqlite3.DatabaseError as exc:
        raise LiveDatabaseError(f"Failed to back up live database at {source}") from exc
    finally:
        if temp_path.exists():
            temp_path.unlink()

    backups = sorted(backup_dir.glob("gpu-monitor-*.db"))
    for stale_path in backups[:-keep]:
        stale_path.unlink()

    return published_path


def prepare_live_database(
    database_url: str,
    expected_server_count: int,
    backup_dir: str | None,
    backup_keep: int,
) -> DatabaseSnapshot:
    path = sqlite_path_from_url(database_url)
    snapshot = inspect_live_database(path)

    if expected_server_count > 0:
        if snapshot.server_count == 0:
            raise LiveDatabaseError(
                f"Live database has 0 registered servers at {path}; expected {expected_server_count}"
            )
        if snapshot.server_count < expected_server_count:
            raise LiveDatabaseError(
                "Live database has "
                f"{snapshot.server_count} registered servers at {path}; expected {expected_server_count}"
            )

    normalized_backup_dir = (backup_dir or "").strip()
    if normalized_backup_dir:
        backup_live_database(path, Path(normalized_backup_dir).expanduser(), backup_keep)

    return snapshot
