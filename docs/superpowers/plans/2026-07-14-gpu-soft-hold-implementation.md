# GPU Soft Hold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add backward-compatible advisory GPU holds on top of Notes while keeping plain memos unchanged, preserving the existing authenticated DELETE cancellation path, and leaving collector/WebSocket payloads alone.

**Architecture:** Extend the current SQLAlchemy `Note` row so SQLite stores canonical `kind` and `gpu_indices` text without breaking legacy memo rows, then teach the notes router to validate, serialize, and omit expired rows with the same route surface the app already uses. On the frontend, add a tiny payload-normalization helper and a telemetry-freshness helper so the form, card preview, and hold-chip rendering stay explicit, sortable, and reversible without introducing exclusivity or collector changes. Keep the UI copy advisory-only: no reserved/exclusive language, no schedule lock, and no `cancelled_at` field.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic v2, SQLite bootstrap helpers, SvelteKit 5, TypeScript, Node 24 built-in `node:test`, backend commands via `cd ~/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_note_expiry backend.tests.test_notes_validation backend.tests.test_note_admin_override -v`, frontend commands via `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/notePayload.test.ts src/lib/utils/telemetryFreshness.test.ts src/lib/api.contract.test.ts src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts && npm run check && npm run build`, and browser QA via the bundled Playwright CLI wrapper over the local `15175` tunnel.

## Global Constraints
- No collector or WebSocket payload changes.
- Soft hold is advisory only; no hard exclusivity, scheduling, overlap lock, or `cancelled_at` field.
- Cancellation remains the existing authenticated DELETE path.
- Storage for `gpu_indices` is TEXT JSON.
- `kind` defaults to `memo` and `gpu_indices` defaults to `[]`.
- Memo rejects any non-empty GPU list.
- Hold accepts only unique non-negative integers and normalizes the final list ascending.
- Expired records stay omitted by the current cleanup/listing behavior.
- Plain memo behavior and rendering must continue to work unchanged.
- Browser QA runs against the remote dev service through a local `127.0.0.1:15175` tunnel.
- Do not push.

## File Map
- Modify: `backend/models.py` — add the stored note kind and canonical GPU-index text column.
- Modify: `backend/database.py` — extend the SQLite bootstrap/backfill helper.
- Modify: `backend/routers/notes.py` — add Pydantic v2 note schemas, GPU parsing/serialization, and create/list/delete wiring.
- Modify: `backend/tests/test_note_expiry.py` — extend the existing `NoteExpiryTests` coverage for defaults and legacy backfill.
- Modify: `backend/tests/test_note_admin_override.py` — add async delete-route coverage with a temporary session factory.
- Create: `backend/tests/test_notes_validation.py` — add pure validation tests for note payloads and JSON helpers.
- Modify: `frontend/src/lib/types.ts` — add note kind and GPU-index fields to the shared types.
- Modify: `frontend/src/lib/api.ts` — switch `createNote` to object input and use the payload builder.
- Create: `frontend/src/lib/utils/notePayload.ts` — normalize and validate create-note payloads.
- Modify: `frontend/src/lib/types.ts`

```ts
// frontend/src/lib/types.ts
export type NoteKind = 'memo' | 'hold';

export interface Note {
	id: number;
	server_id: number;
	username: string;
	content: string;
	created_at: string;
	expires_at: string | null;
	kind: NoteKind;
	gpu_indices: number[];
}
```
- Create: `frontend/src/lib/utils/notePayload.test.ts` — cover memo/hold payload rules.
- Create: `frontend/src/lib/utils/telemetryFreshness.ts` — expose a pure telemetry freshness helper.
- Create: `frontend/src/lib/utils/telemetryFreshness.test.ts` — cover the real age-threshold behavior.
- Modify: `frontend/src/lib/components/NoteForm.svelte` — first update the `createNote` call site, then add memo/hold UI.
- Modify: `frontend/src/lib/components/ServerCard.svelte` — render hold chips, wire the new `NoteForm` props, and keep delete controls intact.
- Create: `frontend/src/lib/components/NoteForm.contract.test.ts` — source-contract the new props, helper usage, and advisory copy.
- Create: `frontend/src/lib/components/ServerCard.note-contract.test.ts` — source-contract the hold rendering and prop wiring.
- Modify: `frontend/src/lib/styles/monitor-cards.css` — style the hold label, kind toggles, and GPU chips for both the composer and the rendered notes.
- Create: `frontend/src/lib/styles/monitor-cards.contract.test.ts` — source-contract the new CSS hooks.

## Task Dependencies
- Task 1 lands first because the storage schema is the persistence contract.
- Task 2 lands next because the router helpers and delete/list coverage define the backend wire contract.
- Task 3 lands before the full form/card UI because the frontend shared types, payload builder, and call signature must already compile.
- Task 4 lands after Task 3 because the memo/hold composer, stale-warning helper, and card rendering depend on the new note shape.
- Task 5 runs last as the combined regression and browser smoke pass.

### Task 1: Add backward-compatible Notes schema bootstrap for `kind` and `gpu_indices`

**Interfaces:**
- `class Note(Base): kind = Column(Text, nullable=False, default='memo', server_default=text("'memo'")); gpu_indices = Column(Text, nullable=False, default='[]', server_default=text("'[]'"))`
- `def ensure_notes_expiry_schema_sync(conn: Connection) -> None`

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/tests/test_note_expiry.py`

- [ ] **Step 1: Add the failing schema/default tests to the existing `NoteExpiryTests` class**

```python
# backend/tests/test_note_expiry.py
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
            db_path = Path(tmpdir) / 'defaults.db'
            engine = create_engine(f'sqlite:///{db_path}')

            try:
                with engine.begin() as conn:
                    Base.metadata.create_all(conn)
                    ensure_notes_expiry_schema_sync(conn)

                with Session(engine) as session:
                    note = Note(server_id=1, username='u', content='memo')
                    session.add(note)
                    session.flush()
                    loaded = session.execute(select(Note).where(Note.id == note.id)).scalar_one()
                    self.assertEqual(loaded.kind, 'memo')
                    self.assertEqual(loaded.gpu_indices, '[]')
            finally:
                engine.dispose()

    def test_ensure_notes_expiry_schema_adds_soft_hold_columns_and_backfills_legacy_rows(self) -> None:
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'legacy.db'
            engine = create_engine(f'sqlite:///{db_path}')

            try:
                with engine.begin() as conn:
                    conn.execute(
                        text(
                            '''
                            CREATE TABLE notes (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                server_id INTEGER NOT NULL,
                                username TEXT NOT NULL,
                                content TEXT NOT NULL,
                                created_at DATETIME,
                                expires_at DATETIME
                            )
                            '''
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

                    columns = {row[1] for row in conn.execute(text('PRAGMA table_info(notes)')).fetchall()}
                    self.assertIn('kind', columns)
                    self.assertIn('gpu_indices', columns)

                    row = conn.execute(
                        text("SELECT kind, gpu_indices FROM notes WHERE username='legacy'")
                    ).one()
                    self.assertEqual(row, ('memo', '[]'))
            finally:
                engine.dispose()
```

- [ ] **Step 2: Run the current expiry suite and confirm it fails before the model/bootstrap change**
  - Run: `cd ~/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_note_expiry.NoteExpiryTests -v`
  - Expected RED: the new defaults/backfill assertions fail because `kind` and `gpu_indices` are not yet present.

- [ ] **Step 3: Implement the exact SQLAlchemy fields and SQLite bootstrap/backfill**

```python
# backend/models.py
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index, JSON, func, text
)

class Note(Base):
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey('servers.id'), nullable=False)
    username = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    kind = Column(Text, nullable=False, default='memo', server_default=text("'memo'"))
    gpu_indices = Column(Text, nullable=False, default='[]', server_default=text("'[]'"))
    created_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
```

```python
# backend/database.py
from sqlalchemy import text
from sqlalchemy.engine import Connection

def ensure_notes_expiry_schema_sync(conn: Connection) -> None:
    result = conn.execute(text('PRAGMA table_info(notes)'))
    columns = {row[1] for row in result.fetchall()}

    if 'expires_at' not in columns:
        conn.execute(text('ALTER TABLE notes ADD COLUMN expires_at DATETIME'))
    if 'kind' not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN kind TEXT NOT NULL DEFAULT 'memo'"))
    if 'gpu_indices' not in columns:
        conn.execute(text("ALTER TABLE notes ADD COLUMN gpu_indices TEXT NOT NULL DEFAULT '[]'"))

    conn.execute(text("UPDATE notes SET kind = 'memo' WHERE kind IS NULL OR kind = ''"))
    conn.execute(text("UPDATE notes SET gpu_indices = '[]' WHERE gpu_indices IS NULL OR gpu_indices = ''"))
    conn.execute(text('CREATE INDEX IF NOT EXISTS ix_notes_expires_at ON notes (expires_at)'))
```

- [ ] **Step 4: Re-run the expiry suite and confirm the GREEN state**
  - Run: `cd ~/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_note_expiry.NoteExpiryTests -v`
  - Expected GREEN: the new model/default and schema/backfill tests pass, and the existing expiry tests stay green.

- [ ] **Step 5: Save the storage pass with a focused local commit**
  - Run: `git add backend/models.py backend/database.py backend/tests/test_note_expiry.py`
  - Run: `git commit -m "feat: add notes soft hold storage"`

### Task 2: Add Pydantic v2 validation, JSON helpers, and delete-route coverage

**Interfaces:**
- `NoteKind = Literal['memo', 'hold']`
- `def parse_gpu_indices(raw: str | None) -> list[int]`
- `def serialize_gpu_indices(indices: list[int]) -> str`
- `class NoteCreate(BaseModel): username: str; ssh_password: str; content: str; expires_at: datetime; kind: NoteKind = 'memo'; gpu_indices: list[StrictInt] = Field(default_factory=list)`
- `class NoteOut(BaseModel): id: int; server_id: int; username: str; content: str; created_at: str; expires_at: str | None; kind: NoteKind; gpu_indices: list[int]`
- `def _note_to_out(n: Note) -> NoteOut`
- `async def create_note(server_id: int, body: NoteCreate)`
- `async def delete_note(server_id: int, note_id: int, body: NoteDelete)`

**Files:**
- Modify: `backend/routers/notes.py`
- Create: `backend/tests/test_notes_validation.py`
- Modify: `backend/tests/test_note_admin_override.py`

- [ ] **Step 1: Add the pure validation tests and the real async delete-route tests before implementation**

```python
# backend/tests/test_notes_validation.py
from datetime import datetime, timezone
import unittest

from pydantic import ValidationError

from backend.routers.notes import NoteCreate, NoteOut, parse_gpu_indices, serialize_gpu_indices


FUTURE = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


class NoteValidationTests(unittest.TestCase):
    def test_parse_gpu_indices_returns_sorted_unique_values(self) -> None:
        self.assertEqual(parse_gpu_indices('[3, 1, 3, 2]'), [1, 2, 3])

    def test_memo_rejects_non_empty_gpu_list(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='memo',
                expires_at=FUTURE,
                kind='memo',
                gpu_indices=[1],
            )

    def test_hold_rejects_empty_gpu_list_and_non_integer_values(self) -> None:
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=FUTURE,
                kind='hold',
                gpu_indices=[],
            )
        with self.assertRaises(ValidationError):
            NoteCreate(
                username='u',
                ssh_password='pw',
                content='hold',
                expires_at=FUTURE,
                kind='hold',
                gpu_indices=[1, '2'],
            )

    def test_note_out_accepts_canonical_db_payload(self) -> None:
        out = NoteOut(
            id=1,
            server_id=7,
            username='u',
            content='hold',
            created_at='2026-07-15T00:00:00Z',
            expires_at=None,
            kind='hold',
            gpu_indices=parse_gpu_indices('[2, 0, 2]'),
        )
        self.assertEqual(out.gpu_indices, [0, 2])
        self.assertEqual(serialize_gpu_indices(out.gpu_indices), '[0, 2]')
```

```python
# backend/tests/test_note_admin_override.py
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
from backend.routers.notes import NoteDelete, delete_note, list_notes


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
```

- [ ] **Step 2: Run the validation tests and confirm they fail before the router change**
  - Run: `cd ~/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_notes_validation backend.tests.test_note_admin_override -v`
  - Expected RED: the new helpers, strict validators, and real delete-route tests fail until the router implements the new contract.

- [ ] **Step 3: Implement the exact Pydantic v2 schemas, GPU helpers, and note wiring**

```python
# backend/routers/notes.py
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

from ..database import AsyncSessionLocal
from ..models import Note, Server
from ..note_expiry import serialize_datetime, utc_now, validate_expires_at

NoteKind = Literal['memo', 'hold']


def parse_gpu_indices(raw: str | None) -> list[int]:
    if raw in (None, ''):
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError('gpu_indices must be valid JSON') from exc
    if not isinstance(payload, list):
        raise ValueError('gpu_indices must be a JSON array')
    indices: list[int] = []
    for value in payload:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('gpu_indices must contain only non-negative integers')
        indices.append(value)
    return sorted(dict.fromkeys(indices))


def serialize_gpu_indices(indices: list[int]) -> str:
    normalized: list[int] = []
    for value in indices:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError('gpu_indices must contain only non-negative integers')
        normalized.append(value)
    return json.dumps(sorted(dict.fromkeys(normalized)))


class NoteCreate(BaseModel):
    username: str
    ssh_password: str
    content: str
    expires_at: datetime
    kind: NoteKind = 'memo'
    gpu_indices: list[StrictInt] = Field(default_factory=list)

    @field_validator('gpu_indices', mode='before')
    @classmethod
    def _coerce_gpu_indices(cls, value):
        if value in (None, ''):
            return []
        if isinstance(value, tuple):
            return list(value)
        return value

    @model_validator(mode='after')
    def _validate_hold_contract(self):
        gpu_indices = sorted(dict.fromkeys(self.gpu_indices))
        if self.kind == 'memo' and gpu_indices:
            raise ValueError('memo notes cannot include gpu_indices')
        if self.kind == 'hold' and not gpu_indices:
            raise ValueError('hold notes require at least one gpu index')
        self.gpu_indices = gpu_indices
        return self


class NoteOut(BaseModel):
    id: int
    server_id: int
    username: str
    content: str
    created_at: str
    expires_at: Optional[str] = None
    kind: NoteKind
    gpu_indices: list[int] = Field(default_factory=list)

    @field_validator('gpu_indices', mode='before')
    @classmethod
    def _coerce_gpu_indices(cls, value):
        if isinstance(value, str) or value is None:
            return parse_gpu_indices(value)
        if isinstance(value, tuple):
            return list(value)
        return value

    @model_validator(mode='after')
    def _validate_note_out(self):
        gpu_indices = sorted(dict.fromkeys(self.gpu_indices))
        if self.kind == 'memo' and gpu_indices:
            raise ValueError('memo notes cannot include gpu_indices')
        if self.kind == 'hold' and not gpu_indices:
            raise ValueError('hold notes require at least one gpu index')
        self.gpu_indices = gpu_indices
        return self
```

```python
# backend/routers/notes.py

def _note_to_out(n: Note) -> NoteOut:
    return NoteOut(
        id=n.id,
        server_id=n.server_id,
        username=n.username,
        content=n.content,
        created_at=serialize_datetime(n.created_at) if isinstance(n.created_at, datetime) else str(n.created_at),
        expires_at=serialize_datetime(n.expires_at) if isinstance(n.expires_at, datetime) else None,
        kind=(n.kind or 'memo'),
        gpu_indices=parse_gpu_indices(n.gpu_indices),
    )


@router.post('/servers/{server_id}/notes', response_model=NoteOut, status_code=201)
async def create_note(server_id: int, body: NoteCreate):
    is_admin = _matches_admin_password(body.ssh_password)
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Server).where(Server.id == server_id))
        server = result.scalar_one_or_none()
        if not server:
            raise HTTPException(status_code=404, detail='Server not found')

        if not is_admin:
            valid = await _verify_user(server, body.username, body.ssh_password)
            if not valid:
                raise HTTPException(status_code=401, detail='SSH authentication failed')

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
```

- [ ] **Step 4: Re-run the router and validation suites and confirm the GREEN state**
  - Run: `cd ~/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_notes_validation backend.tests.test_note_admin_override -v`
  - Expected GREEN: the validators, serializers, delete-route tests, and expiry omission checks all pass.

- [ ] **Step 5: Save the router pass with a focused local commit**
  - Run: `git add backend/routers/notes.py backend/tests/test_notes_validation.py backend/tests/test_note_admin_override.py`
  - Run: `git commit -m "feat: validate advisory gpu holds"`

### Task 3: Add frontend note payload normalization and type support

**Interfaces:**
- `type NoteKind = 'memo' | 'hold'`
- `interface CreateNoteInput { username: string; ssh_password: string; content: string; expires_at: string; kind?: NoteKind; gpu_indices?: number[]; }`
- `interface NoteCreatePayload { username: string; ssh_password: string; content: string; expires_at: string; kind: NoteKind; gpu_indices: number[]; }`
- `function buildNotePayload(input: CreateNoteInput): NoteCreatePayload`
- `function createNote(serverId: number, input: CreateNoteInput): Promise<Note>`

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/utils/notePayload.ts`
- Create: `frontend/src/lib/utils/notePayload.test.ts`
- Create: `frontend/src/lib/api.contract.test.ts`
- Modify: `frontend/src/lib/components/NoteForm.svelte`

```ts
// frontend/src/lib/types.ts
export type NoteKind = 'memo' | 'hold';

export interface Note {
	id: number;
	server_id: number;
	username: string;
	content: string;
	created_at: string;
	expires_at: string | null;
	kind: NoteKind;
	gpu_indices: number[];
}
```

- [ ] **Step 1: Add the payload and API contract tests before touching the frontend runtime code**

```ts
// frontend/src/lib/utils/notePayload.test.ts
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildNotePayload } from './notePayload.ts';

test('memo payload rejects GPU indices and keeps plain memo default', () => {
	assert.deepEqual(
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z'
		}),
		{
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'memo',
			gpu_indices: []
		}
	);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'memo',
			expires_at: '2026-07-15T00:00:00Z',
			gpu_indices: [1]
		});
	}, /memo notes cannot include gpu indices/);
});

test('hold payload sorts unique indices and throws on empty, negative, or noninteger values', () => {
	assert.deepEqual(
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [3, 1, 3, 2]
		}),
		{
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [1, 2, 3]
		}
	);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: []
		});
	}, /hold notes require at least one gpu index/);
	assert.throws(() => {
		buildNotePayload({
			username: 'u',
			ssh_password: 'pw',
			content: 'hold',
			expires_at: '2026-07-15T00:00:00Z',
			kind: 'hold',
			gpu_indices: [-1]
		});
	}, /gpu_indices must contain non-negative integers/);
});
```

```ts
// frontend/src/lib/api.contract.test.ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const apiSource = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');
const typesSource = readFileSync(new URL('./types.ts', import.meta.url), 'utf8');

test('API exposes the exact note payload shape and createNote signature', () => {
	assert.match(typesSource, /export type NoteKind = 'memo' \| 'hold';/);
	assert.match(typesSource, /kind: NoteKind;/);
	assert.match(typesSource, /gpu_indices: number\[];/);
	assert.match(apiSource, /export async function createNote\(serverId: number, input: CreateNoteInput\): Promise<Note>/);
	assert.match(apiSource, /buildNotePayload\(input\)/);
});
```

- [ ] **Step 2: Run the payload/API tests and confirm they fail before implementation**
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/notePayload.test.ts src/lib/api.contract.test.ts`
  - Expected RED: the new helper and signature assertions fail because the frontend still posts the old flat arguments.

- [ ] **Step 3: Implement the strict payload builder and update the call site without UI changes yet**

```ts
// frontend/src/lib/utils/notePayload.ts
export type NoteKind = 'memo' | 'hold';

export interface CreateNoteInput {
	username: string;
	ssh_password: string;
	content: string;
	expires_at: string;
	kind?: NoteKind;
	gpu_indices?: number[];
}

export interface NoteCreatePayload {
	username: string;
	ssh_password: string;
	content: string;
	expires_at: string;
	kind: NoteKind;
	gpu_indices: number[];
}

function normalizeGpuIndices(indices: number[]): number[] {
	const normalized: number[] = [];
	for (const value of indices) {
		if (!Number.isInteger(value) || value < 0) {
			throw new Error('gpu_indices must contain non-negative integers');
		}
		normalized.push(value);
	}
	return [...new Set(normalized)].sort((a, b) => a - b);
}

export function buildNotePayload(input: CreateNoteInput): NoteCreatePayload {
	const kind = input.kind ?? 'memo';
	const gpuIndices = normalizeGpuIndices(input.gpu_indices ?? []);

	if (kind === 'memo' && gpuIndices.length > 0) {
		throw new Error('memo notes cannot include gpu indices');
	}
	if (kind === 'hold' && gpuIndices.length === 0) {
		throw new Error('hold notes require at least one gpu index');
	}

	return {
		username: input.username.trim(),
		ssh_password: input.ssh_password.trim(),
		content: input.content.trim(),
		expires_at: input.expires_at,
		kind,
		gpu_indices: gpuIndices
	};
}
```

```ts
// frontend/src/lib/api.ts
import type { EventLog, Note, ServerRecord, ServerState } from '$lib/types';
import { buildNotePayload, type CreateNoteInput } from '$lib/utils/notePayload';

export async function createNote(serverId: number, input: CreateNoteInput): Promise<Note> {
	const res = await fetchWithTimeout(`${BASE}/servers/${serverId}/notes`, {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(buildNotePayload(input))
	});
	return handleResponse<Note>(res);
}
```

```svelte
<!-- frontend/src/lib/components/NoteForm.svelte -->
<script lang="ts">
	import type { Note } from '$lib/types';
	import { createNote } from '$lib/api';

	async function handleSubmit() {
		const note = await createNote(serverId, {
			username: username.trim(),
			ssh_password: sshPassword.trim(),
			content: content.trim(),
			expires_at: expiresAtDate.toISOString()
		});
		onCreated(note);
	}
</script>
```

- [ ] **Step 4: Re-run the payload tests and the frontend static check**
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/notePayload.test.ts src/lib/api.contract.test.ts`
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check`
  - Expected GREEN: the payload helper and exact `createNote` signature pass, and the Svelte type check stays clean.

- [ ] **Step 5: Save the frontend contract pass with a focused local commit**
  - Run: `git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/utils/notePayload.ts frontend/src/lib/utils/notePayload.test.ts frontend/src/lib/api.contract.test.ts frontend/src/lib/components/NoteForm.svelte`
  - Run: `git commit -m "feat: add advisory hold payloads"`

### Task 4: Add the dense memo/hold composer, stale helper, and hold rendering in `NoteForm` and `ServerCard`

**Interfaces:**
- `interface NoteFormProps { serverId: number; gpus: GpuInfo[]; serverStatus: ServerStatus; lastSeen: string | null; onCreated: (note: Note) => void; }`
- `function isTelemetryStale(lastSeen: string | null, nowMs: number, maxAgeMs: number): boolean`
- `function toggleGpu(gpuIndex: number): void`

**Files:**
- Create: `frontend/src/lib/utils/telemetryFreshness.ts`
- Create: `frontend/src/lib/utils/telemetryFreshness.test.ts`
- Modify: `frontend/src/lib/components/NoteForm.svelte`
- Modify: `frontend/src/lib/components/ServerCard.svelte`
- Create: `frontend/src/lib/components/NoteForm.contract.test.ts`
- Create: `frontend/src/lib/components/ServerCard.note-contract.test.ts`
- Modify: `frontend/src/lib/styles/monitor-cards.css`
- Create: `frontend/src/lib/styles/monitor-cards.contract.test.ts`

- [ ] **Step 1: Add the source-contract tests and the freshness helper tests before the UI change**

```ts
// frontend/src/lib/utils/telemetryFreshness.test.ts
import test from 'node:test';
import assert from 'node:assert/strict';

import { isTelemetryStale } from './telemetryFreshness.ts';

test('isTelemetryStale uses an actual age threshold', () => {
	const nowMs = Date.parse('2026-07-15T00:00:00Z');
	assert.equal(isTelemetryStale('2026-07-14T23:58:50Z', nowMs, 60_000), true);
	assert.equal(isTelemetryStale('2026-07-14T23:59:20Z', nowMs, 60_000), false);
	assert.equal(isTelemetryStale(null, nowMs, 60_000), true);
});
```

```ts
// frontend/src/lib/components/NoteForm.contract.test.ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./NoteForm.svelte', import.meta.url), 'utf8');

test('NoteForm keeps the exact memo/hold props, stale helper, and advisory copy', () => {
	assert.match(source, /let \{\s*serverId,\s*gpus,\s*serverStatus,\s*lastSeen,\s*onCreated\s*\}\s*=\s*\$props\(\);/s);
	assert.match(source, /isTelemetryStale\(lastSeen, nowMs, \d+\)/);
	assert.match(source, /toggleGpu/);
	assert.match(source, /advisory soft hold/);
	assert.match(source, /buildNotePayload\(/);
	assert.doesNotMatch(source, /exclusive|reserved|cancelled_at/i);
});
```

```ts
// frontend/src/lib/components/ServerCard.note-contract.test.ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ServerCard.svelte', import.meta.url), 'utf8');

test('ServerCard renders advisory hold chips and wires the new NoteForm props', () => {
	assert.match(source, /note\.kind === 'hold'/);
	assert.match(source, /note\.gpu_indices/);
	assert.match(source, /advisory soft hold/);
	assert.match(source, /<NoteForm[\s\S]*serverId=\{server\.server_id\}[\s\S]*gpus=\{server\.gpus\}[\s\S]*serverStatus=\{server\.status\}[\s\S]*lastSeen=\{server\.last_seen\}[\s\S]*onCreated=\{onNoteCreated\}/);
});
```

```ts
// frontend/src/lib/styles/monitor-cards.contract.test.ts
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('./monitor-cards.css', import.meta.url), 'utf8');

test('hold footer remains chip-based and compact', () => {
	assert.match(css, /\.note-form-kind-row/);
	assert.match(css, /\.note-form-kind-toggle/);
	assert.match(css, /\.note-form-hold-chip-row/);
	assert.match(css, /\.note-form-hold-chip/);
	assert.match(css, /\.note-form-hold-warning/);
	assert.match(css, /\.monitor-note-item__kind/);
	assert.match(css, /\.monitor-note-item__gpu-chips/);
	assert.match(css, /\.monitor-note-item__gpu-chip/);
});
```

- [ ] **Step 2: Run the contracts and confirm they fail before the UI implementation**
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/telemetryFreshness.test.ts src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts`
  - Expected RED: the current form/card markup does not yet expose the advisory soft hold controls, sorted chips, or freshness helper.

- [ ] **Step 3: Implement the memo/hold composer, freshness helper, and hold rendering**

```ts
// frontend/src/lib/utils/telemetryFreshness.ts
export function isTelemetryStale(lastSeen: string | null, nowMs: number, maxAgeMs: number): boolean {
	if (!lastSeen) return true;
	const lastSeenMs = Date.parse(lastSeen);
	if (Number.isNaN(lastSeenMs)) return true;
	return nowMs - lastSeenMs >= maxAgeMs;
}
```

```svelte
<!-- frontend/src/lib/components/NoteForm.svelte -->
<script lang="ts">
	import type { GpuInfo, Note, ServerStatus } from '$lib/types';
	import { createNote } from '$lib/api';
	import { buildNotePayload, type NoteKind } from '$lib/utils/notePayload';
	import { isTelemetryStale } from '$lib/utils/telemetryFreshness';

	interface NoteFormProps {
		serverId: number;
		gpus: GpuInfo[];
		serverStatus: ServerStatus;
		lastSeen: string | null;
		onCreated: (note: Note) => void;
	}

	let { serverId, gpus, serverStatus, lastSeen, onCreated }: NoteFormProps = $props();
	const TELEMETRY_STALE_MS = 60_000;
	let nowMs = $state(Date.now());
	let kind = $state<NoteKind>('memo');
	let selectedGpuIndices = $state<number[]>([]);
	const telemetryStale = $derived(isTelemetryStale(lastSeen, nowMs, TELEMETRY_STALE_MS));

	function toggleGpu(gpuIndex: number): void {
		const next = selectedGpuIndices.includes(gpuIndex)
			? selectedGpuIndices.filter((value) => value !== gpuIndex)
			: [...selectedGpuIndices, gpuIndex];
		selectedGpuIndices = [...new Set(next)].sort((a, b) => a - b);
	}

	async function handleSubmit() {
		if (!username.trim() || !sshPassword.trim() || !content.trim()) return;

		if (!expiresAtDate) {
			error = '자동 삭제 시간을 확인하세요.';
			return;
		}

		if (expiresAtDate.getTime() <= Date.now()) {
			error = '자동 삭제 시간은 현재보다 뒤여야 합니다.';
			return;
		}

		loading = true;
		error = '';
		try {
			const note = await createNote(
				serverId,
				buildNotePayload({
					username: username.trim(),
					ssh_password: sshPassword.trim(),
					content: content.trim(),
					expires_at: expiresAtDate.toISOString(),
					kind,
					gpu_indices: kind === 'hold' ? selectedGpuIndices : []
				})
			);
			onCreated(note);
			content = '';
			expiresAtLocal = defaultExpiryLocal();
			showPrecisePicker = false;
		} catch (e) {
			error = e instanceof Error ? e.message : '작성 실패';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const timer = setInterval(() => {
			nowMs = Date.now();
		}, 1000);

		return () => clearInterval(timer);
	});
</script>

<div class="note-form-kind-row">
	<button type="button" class="note-form-kind-toggle" aria-pressed={kind === 'memo'} onclick={() => { kind = 'memo'; selectedGpuIndices = []; }}>
		메모
	</button>
	<button type="button" class="note-form-kind-toggle" aria-pressed={kind === 'hold'} onclick={() => { kind = 'hold'; }}>
		advisory soft hold
	</button>
</div>

{#if kind === 'hold'}
	<p class="note-form-hold-copy">advisory soft hold</p>
	{#if telemetryStale}
		<p class="note-form-hold-warning">Telemetry is stale; this advisory soft hold should be treated as guidance only.</p>
	{/if}
	<div class="note-form-hold-chip-row">
		{#each [...gpus].sort((a, b) => a.index - b.index) as gpu (gpu.index)}
			<button type="button" class="note-form-hold-chip" aria-pressed={selectedGpuIndices.includes(gpu.index)} onclick={() => toggleGpu(gpu.index)}>
				G{gpu.index}
			</button>
		{/each}
	</div>
{/if}
```

```svelte
<!-- frontend/src/lib/components/ServerCard.svelte -->
<NoteForm
	serverId={server.server_id}
	gpus={server.gpus}
	serverStatus={server.status}
	lastSeen={server.last_seen}
	onCreated={onNoteCreated}
/>

{#if note.kind === 'hold'}
	<div class="monitor-note-item__kind">advisory soft hold</div>
	<div class="monitor-note-item__gpu-chips">
		{#each note.gpu_indices as gpuIndex (gpuIndex)}
			<span class="monitor-note-item__gpu-chip">G{gpuIndex}</span>
		{/each}
	</div>
{/if}
```

```css
/* frontend/src/lib/styles/monitor-cards.css */
.note-form-kind-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.note-form-kind-toggle,
.note-form-hold-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.45rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--ops-border) 88%, transparent);
  background: color-mix(in srgb, var(--ops-muted) 70%, var(--ops-card));
  color: color-mix(in srgb, var(--ops-fg) 72%, transparent);
  font-size: 0.68rem;
  line-height: 1;
}

.note-form-kind-toggle[aria-pressed='true'],
.note-form-hold-chip[aria-pressed='true'] {
  border-color: color-mix(in srgb, var(--ops-primary) 22%, var(--ops-border));
  background: color-mix(in srgb, var(--ops-primary) 18%, var(--ops-card));
  color: color-mix(in srgb, var(--ops-fg) 96%, transparent);
}

.note-form-hold-copy,
.note-form-hold-warning {
  margin: 0;
  font-size: 0.68rem;
  line-height: 1.35;
}

.note-form-hold-copy {
  color: color-mix(in srgb, var(--ops-fg) 60%, transparent);
}

.note-form-hold-warning {
  color: color-mix(in srgb, #f59e0b 72%, var(--ops-fg));
}

.note-form-hold-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.note-form-hold-chip {
  padding: 0.1rem 0.4rem;
}

.monitor-note-item__kind {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.63rem;
  line-height: 1;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: color-mix(in srgb, var(--ops-fg) 52%, transparent);
}

.monitor-note-item__gpu-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}

.monitor-note-item__gpu-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 1.2rem;
  padding: 0.08rem 0.38rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--ops-border) 88%, transparent);
  background: color-mix(in srgb, var(--ops-muted) 70%, var(--ops-card));
  font-size: 0.68rem;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 4: Re-run the contracts, the frontend static check, and the frontend build**
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/telemetryFreshness.test.ts src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts`
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check`
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run build`
  - Expected GREEN: the advisory soft hold composer, stale helper, and hold-chip rendering compile and build cleanly.

- [ ] **Step 5: Save the UI pass with a focused local commit**
  - Run: `git add frontend/src/lib/components/NoteForm.svelte frontend/src/lib/components/ServerCard.svelte frontend/src/lib/utils/telemetryFreshness.ts frontend/src/lib/utils/telemetryFreshness.test.ts frontend/src/lib/components/NoteForm.contract.test.ts frontend/src/lib/components/ServerCard.note-contract.test.ts frontend/src/lib/styles/monitor-cards.css frontend/src/lib/styles/monitor-cards.contract.test.ts`
  - Run: `git commit -m "feat: render advisory gpu holds"`

### Task 5: Final verification, diff guards, and browser QA over the local tunnel

**Interfaces:**
- Verification-only; no new runtime interfaces.

**Files:**
- Verify: `backend/models.py`
- Verify: `backend/database.py`
- Verify: `backend/routers/notes.py`
- Verify: `backend/tests/test_note_expiry.py`
- Verify: `backend/tests/test_note_admin_override.py`
- Verify: `backend/tests/test_notes_validation.py`
- Verify: `frontend/src/lib/types.ts`
- Verify: `frontend/src/lib/api.ts`
- Verify: `frontend/src/lib/utils/notePayload.ts`
- Verify: `frontend/src/lib/utils/telemetryFreshness.ts`
- Verify: `frontend/src/lib/components/NoteForm.svelte`
- Verify: `frontend/src/lib/components/ServerCard.svelte`
- Verify: `frontend/src/lib/styles/monitor-cards.css`

- [ ] **Step 1: Run the backend note suites together**
  - Run: `cd ~/workspace/monitoring_v2_dev && .venv/bin/python -m unittest backend.tests.test_note_expiry backend.tests.test_notes_validation backend.tests.test_note_admin_override -v`
  - Expected GREEN: the storage, validation, delete-route, and expiry-omission coverage all pass together.

- [ ] **Step 2: Run the frontend note suites together with the exact Node 24 prefix**
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && node --experimental-strip-types --test src/lib/utils/notePayload.test.ts src/lib/utils/telemetryFreshness.test.ts src/lib/api.contract.test.ts src/lib/components/NoteForm.contract.test.ts src/lib/components/ServerCard.note-contract.test.ts src/lib/styles/monitor-cards.contract.test.ts`
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run check`
  - Run: `export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh"; nvm use --silent 24; cd frontend && npm run build`
  - Expected GREEN: the frontend note contract and build checks pass together.

- [ ] **Step 3: Assert the diff is clean and that collector/WebSocket files stayed untouched**
  - Run: `cd ~/workspace/monitoring_v2_dev && git diff --check -- docs/superpowers/plans/2026-07-14-gpu-soft-hold-implementation.md`
  - Run: `cd ~/workspace/monitoring_v2_dev && git diff --name-only -- backend/collectors backend/ws_manager.py backend/slack_bridge.py backend/slack_client.py backend/slack_gpu.py backend/slack_socket.py | cat`
  - Run: `cd ~/workspace/monitoring_v2_dev && git diff --name-only -- backend/models.py backend/database.py backend/routers/notes.py backend/tests/test_note_expiry.py backend/tests/test_note_admin_override.py backend/tests/test_notes_validation.py frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/utils/notePayload.ts frontend/src/lib/utils/telemetryFreshness.ts frontend/src/lib/components/NoteForm.svelte frontend/src/lib/components/ServerCard.svelte frontend/src/lib/styles/monitor-cards.css | sort`
  - Expected GREEN: the plan file has no whitespace errors, no collector/WebSocket files changed, and the changed-file list is limited to the note soft-hold surface.

- [ ] **Step 4: Perform browser QA through the SSH tunnel and bundled Playwright CLI wrapper**
  - Prereq: open the tunnel from the local operator shell and keep it running:
    ```bash
    ssh -p 2200 -N -L 15175:127.0.0.1:5175 ircv@166.104.167.11
    ```
  - Harness setup:
    ```bash
    export CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
    export PWCLI="$CODEX_HOME/skills/playwright/scripts/playwright_cli.sh"
    export PLAYWRIGHT_CLI_SESSION=gpu-soft-hold
    mkdir -p /Users/shchoi/workspace/output/playwright/gpu-soft-hold
    ```
  - Use the remote dev service through the local tunnel:
    ```bash
    "$PWCLI" --session gpu-soft-hold open http://127.0.0.1:15175 --headed
    "$PWCLI" --session gpu-soft-hold snapshot
    "$PWCLI" --session gpu-soft-hold run-code "await page.getByRole('button', { name: '메모' }).click();"
    "$PWCLI" --session gpu-soft-hold screenshot /Users/shchoi/workspace/output/playwright/gpu-soft-hold/full-card-memo.png
    "$PWCLI" --session gpu-soft-hold run-code "await page.getByRole('button', { name: 'advisory soft hold' }).click();"
    "$PWCLI" --session gpu-soft-hold run-code "await page.getByRole('button', { name: 'G0' }).click(); await page.getByRole('button', { name: 'G1' }).click();"
    "$PWCLI" --session gpu-soft-hold screenshot /Users/shchoi/workspace/output/playwright/gpu-soft-hold/full-card-hold.png
    ```
  - Assert in the browser:
    - The composer toggles between memo and advisory soft hold modes.
    - The hold-chip row renders the exact `G#` chips in ascending order.
    - The advisory warning appears when the seeded DOM/state is stale enough to trigger it.
    - The layout stays compact and chip-based in the hold state.
    - Do not submit credentials or assert actual note creation/deletion here; backend tests cover submit/delete behavior.

- [ ] **Step 5: Stop without push**
  - Record the backend test results, frontend build results, diff guards, and screenshot paths.
  - Do not push, do not introduce new dependencies, and do not expand the collector/WebSocket surface.

## Self-Review Checklist
- [ ] The plan covers storage, validation, frontend payloads, form/card UI, and final verification with no missing task.
- [ ] Every task names the exact files it creates or modifies.
- [ ] The plan uses the real repo paths and the real backend/frontend test commands.
- [ ] The plan includes complete RED/GREEN cycles, not vague test instructions.
- [ ] The plan keeps `NoteForm` callable during Task 3 by updating the existing `createNote` call site before the UI expansion.
- [ ] The plan uses Pydantic v2 imports and strict GPU-index validation instead of silent filtering.
- [ ] The delete-route coverage uses a real `IsolatedAsyncioTestCase` with a temporary `AsyncSessionLocal`, actual `Server`/`Note` rows, and patched auth/settings helpers.
- [ ] The plan uses a real freshness threshold for stale telemetry, not just `lastSeen` presence.
- [ ] The plan keeps plain memo behavior unchanged and says `advisory soft hold` instead of exclusive/reserved language.
- [ ] The browser QA section uses the SSH tunnel, the bundled Playwright CLI wrapper, and exact local artifact paths under `/Users/shchoi/workspace/output/playwright/gpu-soft-hold/`.
- [ ] The plan includes `git diff --check` and a collector/WebSocket diff guard.
- [ ] The plan requires no push.
