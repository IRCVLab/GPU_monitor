import unittest
from datetime import datetime,timedelta,timezone
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from backend.models import Base,Server,GpuMetric
from backend.hardware import HardwareMonitor,parse_sensor_snapshot

T=datetime(2026,11,12,8,tzinfo=timezone.utc)

class TemperatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as db:await db.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.servers=[Server(id=1,name='A',host='a',ssh_user='monitor',network='internal'),Server(id=2,name='B',host='b',ssh_user='monitor',network='internal'),Server(id=3,name='C',host='c',ssh_user='monitor',network='external'),Server(id=4,name='Poseidon',host='d',ssh_user='monitor',network='internal')]
        async with self.sessions() as db:db.add_all(self.servers);await db.commit()
        self.monitor=HardwareMonitor(self.sessions)
        self.addCleanup(self.monitor.close)
    async def asyncTearDown(self):await self.engine.dispose()
    async def record(self,seconds,readings):
        at=T+timedelta(seconds=seconds)
        snapshots={str(server):parse_sensor_snapshot(raw,at.isoformat(),None) for server,raw in readings.items()}
        await self.monitor.archive_round(self.servers,snapshots,at)
    async def gpu(self,seconds,server,index,temp):
        async with self.sessions() as db:
            db.add(GpuMetric(server_id=server,gpu_index=index,temperature=temp,collected_at=(T+timedelta(seconds=seconds)).replace(tzinfo=None)));await db.commit()
    async def test_cpu_mean_counts_devices_and_excludes_other_sensors_and_poseidon(self):
        await self.record(0,{1:'CPU1 Temp | 30 | degrees C | ok\nCPU2 Temperature | 50 | degrees C | ok\nDIMM1 Temp | 100 | degrees C | ok\nNVMe Temp | 90 | degrees C | ok',2:'temp_cpu | 70 | degrees C | ok',3:'CPU1 Temp | 90 | degrees C | ok',4:'CPU1 Temp | 120 | degrees C | ok'})
        result=await self.monitor.temperature_history(1,'internal',now=T)
        self.assertEqual(len(result['cpu']),1)
        self.assertEqual((result['cpu'][0]['max_celsius'],result['cpu'][0]['avg_celsius'],result['cpu'][0]['device_count']),(70,50,3))
        self.assertEqual((await self.monitor.temperature_history(1,'external',now=T))['cpu'][0]['avg_celsius'],90)
        self.assertEqual((await self.monitor.temperature_history(1,'all',now=T))['cpu'][0]['avg_celsius'],60)
    async def test_missing_and_invalid_temps_are_nullable_and_have_no_fabricated_history(self):
        self.assertEqual(await self.monitor.temperature_history(1,'all',now=T),{'cpu':[],'gpu':[]})
        await self.record(0,{1:'CPU1 Temp | na | degrees C | na\nCPU2 Temp | 999 | degrees C | ok\nCPU3 Temp | nan | degrees C | ok'})
        result=await self.monitor.temperature_history(1,'internal',now=T)
        self.assertIsNone(result['cpu'][0]['max_celsius']);self.assertIsNone(result['cpu'][0]['avg_celsius'])
        self.assertEqual(result['cpu'][0]['device_count'],0)
    async def test_gpu_uses_existing_archive_with_device_weighted_mean_and_network_filter(self):
        for server,index,temp in [(1,0,30),(1,1,50),(2,0,70),(3,0,90),(4,0,120),(2,1,None),(2,2,999),(2,3,0)]:await self.gpu(0,server,index,temp)
        result=await self.monitor.temperature_history(1,'internal',now=T)
        self.assertEqual((result['gpu'][0]['avg_celsius'],result['gpu'][0]['max_celsius'],result['gpu'][0]['device_count']),(50,70,3))
        self.assertEqual((await self.monitor.temperature_history(1,'external',now=T))['gpu'][0]['avg_celsius'],90)
    async def test_elapsed_and_explicit_missing_gaps_are_preserved(self):
        await self.record(0,{1:'CPU1 Temp | 40 | degrees C | ok'})
        await self.record(60,{})
        await self.record(600,{1:'CPU1 Temp | 80 | degrees C | ok'})
        await self.gpu(0,1,0,40);await self.gpu(600,1,0,80)
        result=await self.monitor.temperature_history(1,'internal',now=T+timedelta(seconds=600))
        self.assertTrue(any(point['max_celsius'] is None for point in result['cpu'][1:-1]))
        self.assertTrue(any(point['max_celsius'] is None for point in result['gpu'][1:-1]))
    async def test_cpu_raw_readings_pruned_with_hardware_retention(self):
        from backend.models import CpuTemperatureObservation
        await self.record(0,{1:'CPU1 Temp | 40 | degrees C | ok'})
        async with self.sessions() as db:self.assertGreater(await db.scalar(select(func.count()).select_from(CpuTemperatureObservation)),0)
        await self.monitor.cleanup(now=T+timedelta(days=31))
        async with self.sessions() as db:self.assertEqual(await db.scalar(select(func.count()).select_from(CpuTemperatureObservation)),0)

class DownsampleTests(unittest.TestCase):
    def test_max_peak_and_missing_gaps_survive_bounded_reduction(self):
        from backend.temperature_history import downsample
        points=[]
        for index in range(5000):
            value=None if index%101==0 else 120 if index==2500 else 40
            points.append({'collected_at':(T+timedelta(seconds=index*30)).isoformat(),'max_celsius':value,'avg_celsius':value,'device_count':0 if value is None else 2})
        reduced=downsample(points)
        self.assertLessEqual(len(reduced),1500)
        self.assertEqual(max(point['max_celsius'] for point in reduced if point['max_celsius'] is not None),120)
        self.assertTrue(any(point['max_celsius'] is None for point in reduced))
