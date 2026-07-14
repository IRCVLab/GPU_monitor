from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from backend.database import ensure_notes_expiry_schema_sync
from backend.models import Base, Note, Server
from backend.note_expiry import (
    expired_notes_delete_statement,
    serialize_datetime,
    validate_expires_at,
)


class NoteExpiryTests(unittest.TestCase):
    def test_note_model_defaults_to_memo_and_empty_gpu_json(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "defaults.db"
            engine = create_engine(f"sqlite:///{db_path}")

            try:
                with engine.begin() as conn:
                    Base.metadata.create_all(conn)
                    ensure_notes_expiry_schema_sync(conn)

                with Session(engine) as session:
                    server = Server(
                        name="Poseidon",
                        host="127.0.0.1",
                        port=22,
                        ssh_user="monitoring",
                        network="internal",
                    )
                    session.add(server)
                    session.flush()

                    note = Note(server_id=server.id, username="u", content="memo")
                    session.add(note)
                    session.flush()

                    loaded = session.execute(select(Note).where(Note.id == note.id)).scalar_one()
                    self.assertEqual(loaded.kind, "memo")
                    self.assertEqual(loaded.gpu_indices, "[]")
            finally:
                engine.dispose()

    def test_ensure_notes_expiry_schema_adds_soft_hold_columns_and_backfills_legacy_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            engine = create_engine(f"sqlite:///{db_path}")

            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE notes (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                server_id INTEGER NOT NULL,
                                username TEXT NOT NULL,
                                content TEXT NOT NULL,
                                created_at DATETIME,
                                expires_at DATETIME
                            )
                            """
                        )
                    )
                    conn.execute(
                        text(
                            """
                            INSERT INTO notes (server_id, username, content, created_at, expires_at)
                            VALUES (1, 'legacy', 'memo', '2026-07-14 00:00:00', NULL)
                            """
                        )
                    )

                    ensure_notes_expiry_schema_sync(conn)

                    columns = {row[1] for row in conn.execute(text("PRAGMA table_info(notes)")).fetchall()}
                    self.assertIn("kind", columns)
                    self.assertIn("gpu_indices", columns)

                    row = conn.execute(
                        text("SELECT kind, gpu_indices FROM notes WHERE username='legacy'")
                    ).one()
                    self.assertEqual(row, ("memo", "[]"))
            finally:
                engine.dispose()

    def test_validate_expires_at_normalizes_to_utc(self) -> None:
        now = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
        expires_at = datetime(2026, 5, 17, 9, 0, tzinfo=timezone(timedelta(hours=9)))

        normalized = validate_expires_at(expires_at, now=now)

        self.assertEqual(
            normalized,
            datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc),
        )

    def test_validate_expires_at_rejects_past_timestamp(self) -> None:
        now = datetime(2026, 5, 16, 0, 0, tzinfo=timezone.utc)
        expires_at = now - timedelta(seconds=1)

        with self.assertRaises(ValueError):
            validate_expires_at(expires_at, now=now)

    def test_serialize_datetime_marks_utc(self) -> None:
        dt = datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)

        self.assertEqual(serialize_datetime(dt), "2026-05-17T00:00:00Z")

    def test_delete_expired_notes_removes_only_expired_rows(self) -> None:
        now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)

        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "notes.db"
            engine = create_engine(f"sqlite:///{db_path}")

            try:
                Base.metadata.create_all(engine)

                with Session(engine) as session:
                    server = Server(
                        name="Poseidon",
                        host="127.0.0.1",
                        port=22,
                        ssh_user="monitoring",
                        network="internal",
                    )
                    session.add(server)
                    session.flush()

                    session.add_all(
                        [
                            Note(
                                server_id=server.id,
                                username="old",
                                content="expired",
                                expires_at=now - timedelta(minutes=1),
                            ),
                            Note(
                                server_id=server.id,
                                username="fresh",
                                content="active",
                                expires_at=now + timedelta(hours=1),
                            ),
                            Note(
                                server_id=server.id,
                                username="legacy",
                                content="no-expiry",
                                expires_at=None,
                            ),
                        ]
                    )
                    session.commit()

                with Session(engine) as session:
                    deleted = session.execute(expired_notes_delete_statement(now)).rowcount or 0
                    session.commit()
                    self.assertEqual(deleted, 1)

                with Session(engine) as session:
                    rows = session.execute(text("SELECT username FROM notes ORDER BY username"))
                    self.assertEqual(rows.scalars().all(), ["fresh", "legacy"])
            finally:
                engine.dispose()

    def test_ensure_notes_expiry_schema_adds_missing_column(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            engine = create_engine(f"sqlite:///{db_path}")

            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            """
                            CREATE TABLE notes (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                server_id INTEGER NOT NULL,
                                username TEXT NOT NULL,
                                content TEXT NOT NULL,
                                created_at DATETIME
                            )
                            """
                        )
                    )
                    ensure_notes_expiry_schema_sync(conn)

                    result = conn.execute(text("PRAGMA table_info(notes)"))
                    columns = {row[1] for row in result.fetchall()}

                self.assertIn("expires_at", columns)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
