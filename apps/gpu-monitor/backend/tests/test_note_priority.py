from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from backend.database import ensure_notes_expiry_schema_sync
from backend.models import Base, Note, Server
from backend.routers.notes import NoteCreate, NoteDelete, _note_to_out, create_note, delete_note, list_notes


def future_time(*, days: int = 365) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


class NotePrioritySchemaTests(TestCase):
    def test_note_model_defaults_priority_and_display_name(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'defaults.db'
            engine = create_engine(f'sqlite:///{db_path}')

            try:
                with engine.begin() as conn:
                    Base.metadata.create_all(conn)
                    ensure_notes_expiry_schema_sync(conn)

                with Session(engine) as session:
                    server = Server(
                        name='Poseidon',
                        host='127.0.0.1',
                        port=22,
                        ssh_user='monitoring',
                        network='internal',
                    )
                    session.add(server)
                    session.flush()

                    note = Note(server_id=server.id, username='u', content='memo')
                    session.add(note)
                    session.flush()

                    loaded = session.execute(select(Note).where(Note.id == note.id)).scalar_one()
                    self.assertEqual(loaded.priority, 'normal')
                    self.assertIsNone(loaded.display_name)
            finally:
                engine.dispose()

    def test_schema_sync_adds_priority_display_name_and_backfills_legacy_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'legacy.db'
            engine = create_engine(f'sqlite:///{db_path}')

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
                                expires_at DATETIME,
                                kind TEXT NOT NULL DEFAULT 'memo',
                                gpu_indices TEXT NOT NULL DEFAULT '[]'
                            )
                            """
                        )
                    )
                    conn.execute(
                        text(
                            """
                            INSERT INTO notes (server_id, username, content, created_at, expires_at, kind, gpu_indices)
                            VALUES (1, 'legacy', 'memo', '2026-07-15 00:00:00', NULL, 'memo', '[]')
                            """
                        )
                    )

                    ensure_notes_expiry_schema_sync(conn)

                    columns = {row[1] for row in conn.execute(text('PRAGMA table_info(notes)')).fetchall()}
                    self.assertIn('priority', columns)
                    self.assertIn('display_name', columns)

                    row = conn.execute(
                        text("SELECT priority, display_name FROM notes WHERE username='legacy'")
                    ).one()
                    self.assertEqual(row, ('normal', None))
            finally:
                engine.dispose()


class NotePriorityRouteTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / 'notes.db'
        self.engine = create_async_engine(f'sqlite+aiosqlite:///{self.db_path}')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text('PRAGMA journal_mode=WAL'))
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.session_factory() as session:
            server = Server(
                name='Poseidon',
                host='127.0.0.1',
                port=22,
                ssh_user='monitoring',
                network='internal',
            )
            session.add(server)
            await session.flush()
            self.server_id = server.id
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.tmpdir.cleanup()

    async def _insert_note(self, username: str, content: str, expires_at, *, priority=None, display_name=None) -> int:
        async with self.session_factory() as session:
            note = Note(
                server_id=self.server_id,
                username=username,
                display_name=display_name,
                content=content,
                kind='memo',
                gpu_indices='[]',
                priority=priority,
                expires_at=expires_at,
            )
            session.add(note)
            await session.commit()
            await session.refresh(note)
            return note.id

    async def test_create_note_authenticates_using_username_not_display_name(self) -> None:
        verify_user = AsyncMock(return_value=True)
        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', verify_user), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            note = await create_note(
                self.server_id,
                NoteCreate(
                    username='owner',
                    display_name='Owner Alias',
                    ssh_password='pw',
                    content='memo',
                    expires_at=future_time(),
                ),
            )

        self.assertEqual(note.username, 'owner')
        self.assertEqual(note.display_name, 'Owner Alias')
        self.assertEqual(note.priority, 'normal')
        verify_user.assert_awaited_once()
        self.assertEqual(verify_user.await_args.args[1], 'owner')

    async def test_create_and_list_notes_serialize_nullable_raw_display_name(self) -> None:
        verify_user = AsyncMock(return_value=True)
        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', verify_user), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            created = await create_note(
                self.server_id,
                NoteCreate(
                    username='owner',
                    display_name='   ',
                    ssh_password='pw',
                    content='memo',
                    expires_at=future_time(),
                    priority='urgent',
                ),
            )
            listed = await list_notes(self.server_id)

        self.assertEqual(created.priority, 'urgent')
        self.assertIsNone(created.display_name)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].priority, 'urgent')
        self.assertIsNone(listed[0].display_name)
        self.assertEqual(listed[0].username, 'owner')

    async def test_list_notes_normalizes_legacy_priority_to_normal(self) -> None:
        note = Note(
            id=1,
            server_id=self.server_id,
            username='legacy',
            display_name='Legacy Alias',
            content='memo',
            kind='memo',
            gpu_indices='[]',
            priority=None,
            created_at=future_time(),
            expires_at=future_time(days=366),
        )

        serialized = _note_to_out(note)

        self.assertEqual(serialized.priority, 'normal')
        self.assertEqual(serialized.display_name, 'Legacy Alias')

    async def test_delete_note_authorizes_by_username_not_display_name(self) -> None:
        note_id = await self._insert_note('owner', 'keep', future_time(days=366), display_name='someone-else', priority='high')

        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', AsyncMock(return_value=True)), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            with self.assertRaises(HTTPException) as ctx:
                await delete_note(self.server_id, note_id, NoteDelete(username='someone-else', ssh_password='pw'))
        self.assertEqual(ctx.exception.status_code, 403)

        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', AsyncMock(return_value=True)), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            await delete_note(self.server_id, note_id, NoteDelete(username='owner', ssh_password='pw'))

        async with self.session_factory() as session:
            row = await session.execute(select(Note).where(Note.id == note_id))
            self.assertIsNone(row.scalar_one_or_none())


if __name__ == '__main__':
    import unittest

    unittest.main()
