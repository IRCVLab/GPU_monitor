import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend import experiment_notes
from backend.collectors.experiment_probe import ExperimentEvidence
from backend.config import Settings
from backend.models import Base, Server
from backend.routers.experiment_notes import router


class ExperimentNotesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add_all([
                Server(id=1, name="Internal", host="internal", ssh_user="monitor", network="internal"),
                Server(id=2, name="External", host="external", ssh_user="monitor", network="external"),
            ])
            await session.commit()
        self.settings = Settings(
            secret_key="safe-test-secret-key", admin_password="fixture-admin",
            experiment_notes_openai_api_key="fixture-key", experiment_notes_model="fixture-model",
        )
        self.service = experiment_notes.ExperimentNotes(self.settings, self.sessions)
        self.service.summarize = AsyncMock(return_value="TransFuser 학습")
        self.evidence = ExperimentEvidence(
            id="run-1", entrypoint="train.py", gpu_indices=(0, 2), eta_seconds=180,
            config_name="transfuser.yaml",
        )
        self.states = {
            1: {"network": "internal", "status": "online", "gpus": [{"users": ["test"]}]},
            2: {"network": "external", "status": "online", "gpus": [{"users": ["test"]}]},
        }

    async def asyncTearDown(self):
        await self.engine.dispose()

    def request_client(self):
        app = FastAPI()
        app.state.experiment_notes = self.service
        app.include_router(router)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def test_default_off_admin_guard_and_persistence(self):
        with patch("backend.routers.servers.get_settings", return_value=self.settings):
            async with self.request_client() as client:
                initial = await client.get("/experiment-notes")
                self.assertEqual(initial.json(), {"enabled": False, "configured": True, "by_server": {}})
                denied = await client.put("/experiment-notes/settings", json={"enabled": True, "admin_password": "wrong"})
                self.assertEqual(denied.status_code, 401)
                self.assertFalse(self.service.enabled)
                allowed = await client.put("/experiment-notes/settings", json={"enabled": True, "admin_password": "fixture-admin"})
                self.assertEqual(allowed.status_code, 200)
                self.assertTrue(allowed.json()["enabled"])
        restarted = experiment_notes.ExperimentNotes(self.settings, self.sessions)
        await restarted.load()
        self.assertTrue(restarted.snapshot().enabled)

    async def test_unconfigured_enable_is_rejected(self):
        self.service.summarize = None
        with patch("backend.routers.servers.get_settings", return_value=self.settings):
            async with self.request_client() as client:
                result = await client.put("/experiment-notes/settings", json={"enabled": True, "admin_password": "fixture-admin"})
        self.assertEqual(result.status_code, 409)
        self.assertFalse(self.service.enabled)

    async def test_active_jobs_get_notes_cache_summaries_and_remove_finished_jobs(self):
        await self.service.set_enabled(True)
        with (
            patch.object(experiment_notes.manager, "get_current_state", return_value=self.states),
            patch.object(experiment_notes, "collect_experiments", return_value=[self.evidence]) as probe,
        ):
            await self.service.refresh(self.service._revision)
            await self.service.refresh(self.service._revision)
            self.assertEqual([call.args[0].id for call in probe.call_args_list], [1, 1])
            self.service.summarize.assert_awaited_once_with(self.evidence)
            note = self.service.snapshot().by_server[1][0]
            self.assertEqual((note.summary, note.eta_seconds, note.gpu_indices), ("TransFuser 학습", 180, (0, 2)))
            probe.return_value = []
            await self.service.refresh(self.service._revision)
            self.assertEqual(self.service.snapshot().by_server, {})

    async def test_provider_failure_keeps_observed_name_and_does_not_guess_eta(self):
        await self.service.set_enabled(True)
        self.service.summarize.side_effect = httpx.ConnectError("fixture unavailable")
        evidence = ExperimentEvidence(id="run-2", entrypoint="train.py", gpu_indices=(1,), eta_seconds=None)
        with (
            patch.object(experiment_notes.manager, "get_current_state", return_value=self.states),
            patch.object(experiment_notes, "collect_experiments", return_value=[evidence]),
            self.assertLogs(experiment_notes.logger, level="WARNING"),
        ):
            await self.service.refresh(self.service._revision)
            await self.service.refresh(self.service._revision)
            self.service.summarize.assert_awaited_once()
        note = self.service.snapshot().by_server[1][0]
        self.assertEqual((note.summary, note.eta_seconds), ("train.py 실행 중", None))

    async def test_disabling_discards_the_in_flight_result(self):
        started = asyncio.Event()
        proceed = asyncio.Event()

        async def summarize(evidence):
            started.set()
            await proceed.wait()
            return "old result"

        self.service.summarize = summarize
        await self.service.set_enabled(True)
        with (
            patch.object(experiment_notes.manager, "get_current_state", return_value=self.states),
            patch.object(experiment_notes, "collect_experiments", return_value=[self.evidence]),
        ):
            old = asyncio.create_task(self.service.refresh(self.service._revision))
            await asyncio.wait_for(started.wait(), timeout=1)
            await self.service.set_enabled(False)
            self.assertEqual(self.service.snapshot().by_server, {})
            await self.service.set_enabled(True)
            proceed.set()
            await old
        self.assertEqual(self.service.snapshot().by_server, {})

    async def test_model_receives_only_safe_evidence_and_never_calculates_eta(self):
        response = Mock()
        response.json.return_value = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"summary":"TransFuser 학습"}'}]}],
        }
        with patch.object(experiment_notes.httpx, "AsyncClient") as client:
            post = client.return_value.__aenter__.return_value.post
            post.return_value = response
            summary = await experiment_notes.OpenAIExperimentSummary("fixture-key", "fixture-model")(self.evidence)
        self.assertEqual(summary, "TransFuser 학습")
        request = post.await_args
        self.assertEqual(request.args[0], "https://api.openai.com/v1/responses")
        body = request.kwargs["json"]
        self.assertFalse(body["store"])
        self.assertEqual(json.loads(body["input"]), {
            "entrypoint": "train.py", "config_name": "transfuser.yaml", "gpu_count": 2,
        })
        self.assertNotIn("tools", body)
