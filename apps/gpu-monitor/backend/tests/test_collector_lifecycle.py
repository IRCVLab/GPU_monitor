import asyncio
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.collectors import manager
from backend.config import get_settings
from backend.models import Base, Server
from backend.routers import servers


class FakeCollector:
    def __init__(self, server):
        self.server = server
        self._ssh = self
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.closed = False

    async def run(self):
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.stopped.set()

    def close(self):
        self.closed = True


class CollectorLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = async_sessionmaker(engine, expire_on_commit=False)

        async def get_test_db():
            async with sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(servers.router)
        app.dependency_overrides[servers.get_db] = get_test_db
        self.client = await self.enterAsyncContext(
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        )
        self.enterContext(patch.object(manager, "_collectors", {}))
        self.enterContext(patch.object(manager, "_tasks", {}))
        self.collectors = []

        def create_collector(server):
            collector = FakeCollector(server)
            self.collectors.append(collector)
            return collector

        self.enterContext(patch.object(manager, "ServerCollector", side_effect=create_collector))
        self.addAsyncCleanup(self.stop_collectors)
        self.addCleanup(get_settings.cache_clear)

    async def stop_collectors(self):
        for server_id in list(manager._tasks):
            await manager.remove_server(server_id)

    def configure(self, *, disabled):
        self.enterContext(patch.dict(os.environ, MONITORING_DISABLE_COLLECTORS=str(disabled)))
        get_settings.cache_clear()
        self.auth = {"admin_password": get_settings().admin_password}

    async def register(self):
        response = await self.client.post("/servers", json={
            **self.auth,
            "name": "GPU test server",
            "host": "gpu-test.invalid",
            "ssh_user": "monitor",
            "ssh_password": "fixture-ssh-password",
        })
        self.assertEqual(response.status_code, 201, response.text)
        saved = response.json()
        self.assertTrue(saved["has_password"])
        self.assertNotIn("ssh_password", saved)
        return saved["id"]

    async def update(self, server_id):
        response = await self.client.put(f"/servers/{server_id}", json={
            **self.auth, "host": "updated-gpu-test.invalid",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["host"], "updated-gpu-test.invalid")

    async def delete(self, server_id):
        response = await self.client.request("DELETE", f"/servers/{server_id}", json=self.auth)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual((await self.client.get("/servers")).json(), [])
        self.assertNotIn(server_id, manager._tasks)
        self.assertNotIn(server_id, manager._collectors)

    async def test_disabled_collectors_allow_crud_and_explicit_connection_tests(self):
        self.configure(disabled=True)
        server_id = await self.register()
        await self.update(server_id)

        with patch.object(servers.paramiko, "SSHClient") as ssh_type:
            raw = await self.client.post("/servers/test-connection", json={
                **self.auth,
                "server_id": server_id,
                "host": "updated-gpu-test.invalid",
                "ssh_user": "monitor",
            })
            saved = await self.client.post(f"/servers/{server_id}/test", json=self.auth)
            self.assertEqual(raw.status_code, 200, raw.text)
            self.assertEqual(raw.json(), {"ok": True, "reason": None})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json(), {"ok": True})
            self.assertEqual(ssh_type.return_value.connect.call_count, 2)
            self.assertEqual(ssh_type.return_value.close.call_count, 2)

        await self.delete(server_id)
        self.assertEqual(self.collectors, [])

    async def test_enabled_collectors_start_restart_and_stop_on_crud(self):
        self.configure(disabled=False)
        server_id = await self.register()
        self.assertEqual(len(self.collectors), 1)
        first = self.collectors[0]
        await asyncio.wait_for(first.started.wait(), 1)

        await self.update(server_id)
        self.assertEqual(len(self.collectors), 2)
        second = self.collectors[1]
        await asyncio.wait_for(second.started.wait(), 1)
        self.assertTrue(first.stopped.is_set())
        self.assertTrue(first.closed)
        self.assertEqual(second.server.host, "updated-gpu-test.invalid")

        await self.delete(server_id)
        self.assertTrue(second.stopped.is_set())
        self.assertTrue(second.closed)

    async def test_disabled_collectors_skip_batch_start(self):
        self.configure(disabled=True)
        server = Server(id=42, name="GPU test server", host="gpu-test.invalid",
                        ssh_user="monitor", ssh_password="fixture")
        await manager.start_all([server])
        self.assertEqual(self.collectors, [])
        self.assertEqual(manager._tasks, {})
