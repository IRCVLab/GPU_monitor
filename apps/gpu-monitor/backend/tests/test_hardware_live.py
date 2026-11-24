import json
import unittest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.collectors.gpu import parse_gpustat, parse_nvidia_smi
from backend.models import Base, Server
import backend.hardware as hardware

class FanTests(unittest.TestCase):
    def test_optional_gpustat_fan(self):
        for raw, expected in [(42, 42), (0, 0), (None, None), ('N/A', None), (float('nan'), None)]:
            gpu = parse_gpustat(json.dumps({'gpus': [{'index': 0, 'fan.speed': raw}]})).gpus[0]
            self.assertEqual(getattr(gpu, 'fan_percent', 'missing'), expected)
    def test_nvidia_fan_appended_and_legacy_csv_preserved(self):
        row = '0, A100, 10, 100, 1000, 40, 100, GPU-1'
        for suffix, expected in [('', None), (', 37', 37), (', [N/A]', None), (', 0', 0)]:
            self.assertEqual(getattr(parse_nvidia_smi(row + suffix).gpus[0], 'fan_percent', 'missing'), expected)

class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            self.servers = [Server(id=1, name='A', host='a', ssh_user='monitor', network='internal'), Server(id=2, name='B', host='b', ssh_user='monitor', network='internal'), Server(id=3, name='C', host='c', ssh_user='monitor', network='external'), Server(id=4, name='Poseidon', host='d', ssh_user='monitor', network='internal')]
            db.add_all(self.servers)
            await db.commit()
        self.now = datetime.now(timezone.utc)
    async def asyncTearDown(self): await self.engine.dispose()
    def snapshot(self, power, at=None):
        return hardware.parse_sensor_snapshot(f'Instantaneous power reading: {power} Watts' if power is not None else '', (at or self.now).isoformat(), None)
    async def test_partial_round_never_becomes_zero_or_partial_total(self):
        service = hardware.HardwareMonitor(self.sessions)
        self.addCleanup(service.close)
        await service.archive_round(self.servers, {'1': self.snapshot(100), '2': self.snapshot(None)}, self.now)
        result = await service.power_history(1, 'internal', now=self.now)
        self.assertEqual(len(result['points']), 1)
        point = result['points'][0]
        self.assertEqual((point['server_count'], point['expected_count'], point['complete'], point['power_watts']), (1, 2, False, None))
        self.assertIsNone((await service.power_history(1, 'external', now=self.now))['points'][0]['power_watts'])
    async def test_network_filter_complete_real_total_and_seed_only_once(self):
        service = hardware.HardwareMonitor(self.sessions)
        self.addCleanup(service.close)
        observations = {'1': self.snapshot(100), '2': self.snapshot(200)}
        await service.seed_snapshots(self.servers, observations)
        await service.seed_snapshots(self.servers, observations)
        result = await service.power_history(1, 'internal', now=self.now)
        self.assertEqual(len(result['points']), 1)
        self.assertEqual(result['points'][0]['power_watts'], 300)
        self.assertTrue(result['points'][0]['complete'])
        self.assertIsNone((await service.power_history(1, 'all', now=self.now))['points'][0]['power_watts'])
    async def test_different_snapshot_times_are_never_merged_and_old_data_expires(self):
        service = hardware.HardwareMonitor(self.sessions)
        self.addCleanup(service.close)
        await service.seed_snapshots(self.servers, {'1': self.snapshot(100, self.now-timedelta(minutes=20)), '2': self.snapshot(200)})
        result = await service.power_history(1, 'internal', now=self.now)
        self.assertEqual(len(result['points']), 3)
        self.assertTrue(all(p['power_watts'] is None for p in result['points']))
        await service.archive_round(self.servers, {'1': self.snapshot(5, self.now-timedelta(days=31))}, self.now-timedelta(days=31))
        await service.cleanup(now=self.now)
        self.assertEqual(len((await service.power_history(168, 'internal', now=self.now))['points']), 3)
    async def test_real_snapshot_batch_keeps_individual_observation_times(self):
        service = hardware.HardwareMonitor(self.sessions)
        self.addCleanup(service.close)
        await service.seed_snapshots(self.servers, {'1': self.snapshot(100, self.now-timedelta(seconds=20)), '2': self.snapshot(200)})
        result = await service.power_history(1, 'internal', now=self.now)
        self.assertEqual(len(result['points']), 1)
        self.assertEqual(result['points'][0]['power_watts'], 300)
        from backend.models import HardwarePowerObservation
        async with self.sessions() as db:
            observations = (await db.execute(select(HardwarePowerObservation).where(HardwarePowerObservation.observed_at.is_not(None)).order_by(HardwarePowerObservation.server_id))).scalars().all()
        self.assertEqual((observations[1].observed_at-observations[0].observed_at).total_seconds(), 20)

    async def test_concurrency_bound_and_event_loop_remain_independent(self):
        import asyncio
        import threading
        import time
        active = 0
        maximum = 0
        lock = threading.Lock()
        class FakeSSH:
            def __init__(self, server): pass
            def connect(self):
                nonlocal active, maximum
                with lock:
                    active += 1
                    maximum = max(maximum, active)
                time.sleep(0.03)
            def run_bounded(self, command, timeout): return ''
            def close(self):
                nonlocal active
                with lock: active -= 1
        service = hardware.HardwareMonitor(self.sessions, ssh_factory=FakeSSH, concurrency=1)
        self.addCleanup(service.close)
        ticks = 0
        async def ticker():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.005)
                ticks += 1
        await asyncio.gather(service.collect_round(), ticker())
        self.assertEqual(maximum, 1)
        self.assertEqual(active, 0)
        self.assertEqual(ticks, 5)

    async def test_unexpected_single_host_exception_does_not_discard_cohort(self):
        class FakeSSH:
            def __init__(self, server):
                if server.id == 2: raise RuntimeError('unexpected per-host failure')
            def connect(self): pass
            def run_bounded(self, command, timeout): return 'Instantaneous power reading: 100 Watts'
            def close(self): pass
        service = hardware.HardwareMonitor(self.sessions, ssh_factory=FakeSSH)
        self.addCleanup(service.close)
        result = await service.collect_round()
        self.assertEqual(result['servers']['1']['power_watts'], 100)
        self.assertIsNone(result['servers']['2']['power_watts'])
        self.assertFalse(result['point']['complete'])
        self.assertEqual(result['point']['server_count'], 1)
        result = await service.power_history(1, 'internal')
        self.assertIsNone(result['points'][-1]['power_watts'])

    async def test_isolated_ssh_and_failed_dcmi_preserves_sensors(self):
        calls = []
        class FakeSSH:
            def __init__(self, server): self.server = server; calls.append(('new', server.id))
            def connect(self): calls.append(('connect', self.server.id))
            def run_bounded(self, command, timeout):
                calls.append((command, self.server.id))
                if 'dcmi' in command: raise RuntimeError('unsupported')
                return 'CPU1 | 35 | degrees C | ok'
            def close(self): calls.append(('close', self.server.id))
        service = hardware.HardwareMonitor(self.sessions, ssh_factory=FakeSSH, concurrency=1)
        self.addCleanup(service.close)
        payload = await service.collect_round()
        self.assertEqual(payload['mode'], 'live')
        self.assertIsNone(payload['servers']['1']['power_watts'])
        self.assertEqual(payload['servers']['1']['sensors'][0]['value'], 35)
        self.assertEqual([x for x in calls if x[0] == 'new'], [('new', 1), ('new', 2)])
        self.assertEqual(len([x for x in calls if x[0] == 'close']), 2)
        self.assertIsNone(payload['point']['power_watts'])
        self.assertEqual(payload['servers']['1']['source'], 'ssh-ipmi')
        history = await service.power_history(1, 'internal')
        self.assertEqual(payload['point']['collected_at'], history['points'][-1]['collected_at'])

class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_route_and_validated_history_window(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport
        from backend.config import Settings
        settings = Settings(_env_file=None, secret_key='isolated-testing-secret-key', admin_password='isolated-testing-password', database_url='sqlite+aiosqlite:///:memory:', monitoring_disable_collectors=True, monitoring_disable_slack=True)
        with patch('backend.config.get_settings', return_value=settings):
            from backend.routers.hardware import router
        app = FastAPI()
        app.include_router(router)
        class Monitor:
            interval = 60
            current = {'mode': 'live', 'servers': {'1': {'power_watts': 200}}, 'point': None}
            async def power_history(self, hours, network, *, source='dcmi'):
                return {'points': [], 'started_at': None, 'filter': network}
            async def server_history(self,hours,network):
                return {'servers':{},'network':network}
            async def temperature_history(self,hours,network):
                return {'cpu':[],'gpu':[],'network':network}
            async def power_rollups(self,resolution,days,network,source,after=None):
                return {'resolution':resolution,'days':days,'source':source,'buckets':[]}
        app.state.hardware_monitor = Monitor()
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            result = await client.get('/system/hardware')
            self.assertEqual(result.json()['mode'], 'live')
            self.assertEqual(result.json()['interval'], 60)
            result = await client.get('/system/power-history?hours=1&network=external')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['filter'], 'external')
            for query in ('hours=0', 'hours=169', 'network=unknown'):
                self.assertEqual((await client.get('/system/power-history?' + query)).status_code, 422)
            result=await client.get('/system/server-history?hours=168&network=all')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['network'],'all')
            for query in ('hours=0','hours=169','network=unknown'):
                self.assertEqual((await client.get('/system/server-history?'+query)).status_code,422)
            result=await client.get('/system/temperature-history?hours=168&network=all')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['network'],'all')
            for query in ('hours=0','hours=169','network=unknown'):
                self.assertEqual((await client.get('/system/temperature-history?'+query)).status_code,422)
            result=await client.get('/system/power-rollups?resolution=hour&days=3650&source=ac_input')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['source'],'ac_input')
            for query in ('resolution=minute&days=366','resolution=day','days=3651','source=psu_output','after=2026-11-12T00:00:00'):
                self.assertEqual((await client.get('/system/power-rollups?'+query)).status_code,422)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_disabled_and_global_collector_disable_prevent_hardware_task(self):
        from unittest.mock import patch, AsyncMock
        from backend.config import Settings
        from tempfile import TemporaryDirectory
        from pathlib import Path
        with TemporaryDirectory() as tmp:
            settings = Settings(_env_file=None, secret_key='isolated-testing-secret-key', admin_password='isolated-testing-password', database_url=f'sqlite+aiosqlite:///{Path(tmp) / "test.db"}', monitoring_disable_collectors=True, monitoring_disable_slack=True)
            self.assertFalse(settings.hardware_collect_enabled)
            with patch('backend.config.get_settings', return_value=settings):
                import backend.main as main
                for enabled in (False, True):
                    settings.hardware_collect_enabled = enabled
                    with patch.object(main, 'get_settings', return_value=settings), patch.object(main, 'init_db', new=AsyncMock()), patch.object(main, '_log_cleanup_loop', new=AsyncMock()), patch.object(main, '_note_cleanup_loop', new=AsyncMock()), patch('backend.hardware.HardwareMonitor.run', new=AsyncMock()) as run:
                        async with main.lifespan(main.app):
                            self.assertIsNone(main.app.state.hardware_monitor.current)
                        run.assert_not_called()

class HardwareDeadlineTests(unittest.TestCase):
    def test_hung_command_ack_is_interrupted_by_transport_deadline(self):
        import threading
        import time
        from backend.collectors.ssh_client import SSHClient
        closed = threading.Event()
        class Channel:
            def settimeout(self, timeout): pass
            def exec_command(self, command): closed.wait(2)
            def close(self): closed.set()
        class Transport:
            def open_session(self, timeout): return Channel()
        class Client:
            def get_transport(self): return Transport()
            def close(self): closed.set()
        ssh = SSHClient(None)
        ssh._client = Client()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            ssh.run_bounded(hardware.SENSOR_COMMAND, timeout=0.03)
        self.assertLess(time.monotonic()-started, 0.5)
        self.assertTrue(closed.is_set())

    def test_hung_channel_never_waits_for_unready_exit_status(self):
        import time
        from backend.collectors.ssh_client import SSHClient
        class Channel:
            closed = False
            def exec_command(self, command): pass
            def recv_ready(self): return False
            def recv_stderr_ready(self): return False
            def exit_status_ready(self): return False
            def recv_exit_status(self): raise AssertionError('must not wait for exit')
            def close(self): self.closed = True
        channel = Channel()
        class Transport:
            def open_session(self, timeout): return channel
        class Client:
            def get_transport(self): return Transport()
            def close(self): channel.close()
        ssh = SSHClient(None)
        ssh._client = Client()
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            ssh.run_bounded(hardware.SENSOR_COMMAND, timeout=0.03)
        self.assertLess(time.monotonic()-started, 0.5)
        self.assertTrue(channel.closed)

    def test_exit_status_never_waits_before_channel_reports_ready(self):
        from backend.collectors.ssh_client import SSHClient
        class Channel:
            def __init__(self): self.output = b'actual sensor output'
            def exec_command(self, command): pass
            def recv_ready(self): return bool(self.output)
            def recv(self, size):
                data, self.output = self.output, b''
                return data
            def recv_stderr_ready(self): return False
            def exit_status_ready(self): return not self.output
            def recv_exit_status(self):
                if self.output: raise AssertionError('unbounded wait')
                return 0
            def close(self): pass
        class Transport:
            def open_session(self, timeout): return Channel()
        class Client:
            def get_transport(self): return Transport()
            def close(self): pass
        ssh = SSHClient(None)
        ssh._client = Client()
        self.assertEqual(ssh.run_bounded(hardware.SENSOR_COMMAND, timeout=0.1), 'actual sensor output')
