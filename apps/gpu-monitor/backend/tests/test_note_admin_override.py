from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.models import Base, Note, Server
from backend.routers.notes import NoteDelete, _matches_admin_password, delete_note, list_notes


class NoteAdminOverrideTests(TestCase):
    def test_matches_admin_password_returns_true_for_admin_secret(self) -> None:
        with patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            self.assertTrue(_matches_admin_password('ircv_admin'))

    def test_matches_admin_password_returns_false_for_other_secret(self) -> None:
        with patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            self.assertFalse(_matches_admin_password('123123'))

    def test_matches_admin_password_returns_false_for_missing_secret(self) -> None:
        with patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            self.assertFalse(_matches_admin_password(None))


class NoteDeleteRouteTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / 'notes.db'
        self.engine = create_async_engine(f'sqlite+aiosqlite:///{self.db_path}')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
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

    async def _insert_note(self, username: str, content: str, expires_at) -> int:
        async with self.session_factory() as session:
            note = Note(
                server_id=self.server_id,
                username=username,
                content=content,
                kind='memo',
                gpu_indices='[]',
                expires_at=expires_at,
            )
            session.add(note)
            await session.commit()
            await session.refresh(note)
            return note.id

    async def test_owner_delete_succeeds(self) -> None:
        note_id = await self._insert_note('owner', 'keep', datetime.now(timezone.utc) + timedelta(hours=1))
        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', return_value=True), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            await delete_note(self.server_id, note_id, NoteDelete(username='owner', ssh_password='pw'))
        async with self.session_factory() as session:
            row = await session.execute(select(Note).where(Note.id == note_id))
            self.assertIsNone(row.scalar_one_or_none())

    async def test_admin_delete_succeeds(self) -> None:
        note_id = await self._insert_note('owner', 'keep', datetime.now(timezone.utc) + timedelta(hours=1))
        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', return_value=False), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            await delete_note(self.server_id, note_id, NoteDelete(admin_password='ircv_admin'))
        async with self.session_factory() as session:
            row = await session.execute(select(Note).where(Note.id == note_id))
            self.assertIsNone(row.scalar_one_or_none())

    async def test_non_owner_delete_returns_403(self) -> None:
        note_id = await self._insert_note('owner', 'keep', datetime.now(timezone.utc) + timedelta(hours=1))
        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes._verify_user', return_value=True), patch('backend.routers.notes.get_settings', return_value=SimpleNamespace(admin_password='ircv_admin')):
            with self.assertRaises(HTTPException) as ctx:
                await delete_note(self.server_id, note_id, NoteDelete(username='someone-else', ssh_password='pw'))
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_list_notes_omits_expired_rows(self) -> None:
        now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
        await self._insert_note('fresh', 'visible', now + timedelta(minutes=5))
        await self._insert_note('old', 'hidden', now - timedelta(minutes=5))
        with patch('backend.routers.notes.AsyncSessionLocal', self.session_factory), patch('backend.routers.notes.utc_now', return_value=now):
            notes = await list_notes(self.server_id)
        self.assertEqual([note.username for note in notes], ['fresh'])
