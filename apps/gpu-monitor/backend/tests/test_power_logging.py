import unittest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.models import Base, Server
from backend.hardware import HardwareMonitor, parse_sensor_snapshot
import backend.power_logging as power

T = datetime(2026, 11, 12, 8, tzinfo=timezone.utc)

def sample(seconds, watts, cohort='1,2'):
    return {'at': T+timedelta(seconds=seconds), 'power_watts': watts, 'cohort': cohort, 'interval': 60}

class EnergyTests(unittest.TestCase):
    def test_time_weighted_integration_unequal_intervals(self):
        result = power.summarize([sample(0,100), sample(30,200), sample(120,400)], T, T+timedelta(seconds=120))
        self.assertAlmostEqual(result['energy_wh'], (150*30+300*90)/3600)
        self.assertEqual(result['covered_seconds'],120)
        self.assertEqual(result['time_weighted_avg_watts'],262.5)
        self.assertEqual(result['max_watts'],400)
    def test_missing_cohort_change_and_large_gap_are_not_bridged(self):
        values = [sample(0,100),sample(30,None),sample(60,200),sample(90,300,'1'),sample(300,400,'1')]
        result = power.summarize(values, T,T+timedelta(seconds=300))
        self.assertEqual(result['energy_wh'],0)
        self.assertEqual(result['coverage_ratio'],0)
    def test_clips_trapezoid_to_requested_boundary(self):
        result = power.summarize([sample(0,100),sample(120,300)],T+timedelta(seconds=30),T+timedelta(seconds=90))
        self.assertAlmostEqual(result['energy_wh'],200*60/3600)
        self.assertEqual(result['covered_seconds'],60)
    def test_psu_input_output_and_invalid_sensor_are_separate(self):
        snapshot = parse_sensor_snapshot('PSU1 Power In | 100 | Watts | ok\nPSU2 Power In | na | Watts | na\nPSU1 Power Out | 90 | Watts | ok\nPSU2 Power Out | 80 | Watts | ok\nInstantaneous power reading: 250 Watts',T.isoformat(),None)
        readings = power.psu_readings(snapshot)
        self.assertEqual(len(readings),4)
        self.assertIsNone(power.psu_total(readings,'input'))
        self.assertEqual(power.psu_total(readings,'output'),170)
        self.assertEqual(snapshot['power_watts'],250)

class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as db: await db.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.servers=[Server(id=1,name='A',host='a',ssh_user='monitor',network='internal')]
        async with self.sessions() as db:
            db.add_all(self.servers);await db.commit()
        self.monitor=HardwareMonitor(self.sessions)
        self.addCleanup(self.monitor.close)
    async def asyncTearDown(self): await self.engine.dispose()
    async def record(self, seconds, watts=120, ac=140):
        at=T+timedelta(seconds=seconds)
        snapshot=parse_sensor_snapshot(f'PSU1 Power In | {ac} | Watts | ok\nPSU1 Power Out | 110 | Watts | ok\nInstantaneous power reading: {watts} Watts',at.isoformat(),None)
        await self.monitor.archive_round(self.servers,{'1':snapshot},at)
    async def test_internal_history_exposes_observed_subtotal_without_inventing_full_total(self):
        self.servers.extend([
            Server(id=2,name='B',host='b',ssh_user='monitor',network='internal'),
            Server(id=3,name='C',host='c',ssh_user='monitor',network='external'),
        ])
        async with self.sessions() as db:
            db.add_all(self.servers[1:]);await db.commit()
        def reading(watts):
            return parse_sensor_snapshot(f'Instantaneous power reading: {watts} Watts',T.isoformat(),None)
        await self.monitor.archive_round(self.servers,{'1':reading(120),'3':reading(300)},T)
        history=await self.monitor.power_history(1,'internal',now=T)
        point=history['points'][0]
        self.assertEqual(point['observed_watts'],120)
        self.assertEqual((point['server_count'],point['expected_count']),(1,2))
        self.assertIsNone(point['power_watts'])
        self.assertFalse(point['complete'])
        self.assertIsNone(history['summary']['max_watts'])

    async def test_source_summary_and_rollups_keep_totals_and_are_idempotent(self):
        await self.record(0);await self.record(30);await self.record(90)
        history=await self.monitor.power_history(1,'internal',now=T+timedelta(seconds=90),source='ac_input')
        self.assertAlmostEqual(history['summary']['energy_wh'],140*90/3600)
        self.assertEqual(history['summary']['max_watts'],140)
        first=await self.monitor.power_rollups('minute',365,'internal','ac_input',now=T+timedelta(seconds=90))
        async with self.sessions() as db:
            await power.refresh_rollups(db,T,T+timedelta(seconds=90));await db.commit()
        await self.monitor.cleanup(now=T+timedelta(seconds=90))
        second=await self.monitor.power_rollups('minute',365,'internal','ac_input',now=T+timedelta(seconds=90))
        self.assertEqual(first,second)
        self.assertAlmostEqual(sum(row['energy_wh'] for row in second['buckets']),140*90/3600)
        self.assertEqual(sum(row['sample_count'] for row in second['buckets']),3)
        self.assertEqual((await self.monitor.power_history(1,'internal',now=T+timedelta(seconds=90)))['summary']['max_watts'],120)
    async def test_missing_previously_known_psu_does_not_reduce_total_silently(self):
        at=T
        both=parse_sensor_snapshot('PSU1 Power In | 100 | Watts | ok\nPSU2 Power In | 150 | Watts | ok',at.isoformat(),None)
        await self.monitor.archive_round(self.servers,{'1':both},at)
        one=parse_sensor_snapshot('PSU1 Power In | 100 | Watts | ok',(at+timedelta(seconds=60)).isoformat(),None)
        await self.monitor.archive_round(self.servers,{'1':one},at+timedelta(seconds=60))
        history=await self.monitor.power_history(1,'internal',source='ac_input',now=at+timedelta(seconds=60))
        self.assertIsNone(history['points'][-1]['power_watts'])
        self.assertEqual(history['summary']['energy_wh'],0)
    async def test_retention_preserves_hourly_energy_and_does_not_double_count(self):
        await self.record(0);await self.record(60);await self.record(120)
        now=T+timedelta(days=31)
        await self.monitor.cleanup(now=now)
        first=await self.monitor.power_rollups('hour',365,'internal','dcmi',now=now)
        await self.monitor.cleanup(now=now+timedelta(minutes=1))
        self.assertEqual(first,await self.monitor.power_rollups('hour',365,'internal','dcmi',now=now))
        self.assertAlmostEqual(sum(row['energy_wh'] for row in first['buckets']),120*120/3600)
        from backend.models import PowerRawSample
        async with self.sessions() as db: self.assertEqual(await db.scalar(select(func.count()).select_from(PowerRawSample)),0)

    async def test_psu_membership_change_is_not_integrated(self):
        await self.record(0)
        at=T+timedelta(seconds=60)
        snapshot=parse_sensor_snapshot('PSU1 Power In | 100 | Watts | ok\nPSU2 Power In | 150 | Watts | ok',at.isoformat(),None)
        await self.monitor.archive_round(self.servers,{'1':snapshot},at)
        history=await self.monitor.power_history(1,'internal',source='ac_input',now=at)
        self.assertEqual(history['summary']['covered_seconds'],0)

    async def test_legacy_backfill_preserves_real_times_and_matching_psu_only(self):
        from backend.models import HardwareRound,HardwarePowerObservation,PowerRawSample
        async with self.sessions() as db:
            round_=HardwareRound(collected_at=T.replace(tzinfo=None));db.add(round_);await db.flush()
            db.add(HardwarePowerObservation(round_id=round_.id,server_id=1,network='internal',power_watts=5972,observed_at=T.replace(tzinfo=None)))
            await db.commit()
        snapshot=parse_sensor_snapshot('PSU1 Power In | 6000 | Watts | ok',T.isoformat(),None)
        await self.monitor.initialize_power_logging({'1':snapshot})
        await self.monitor.initialize_power_logging({'1':snapshot})
        history=await self.monitor.power_history(1,'internal',now=T)
        self.assertEqual(history['points'][0]['power_watts'],5972)
        self.assertEqual(history['points'][0]['collected_at'],T.isoformat())
        ac=await self.monitor.power_history(1,'internal',source='ac_input',now=T)
        self.assertEqual(ac['points'][0]['power_watts'],6000)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(PowerRawSample).where(PowerRawSample.scope=='network:internal',PowerRawSample.source=='dcmi')),1)

    async def test_rollup_failure_never_deletes_raw(self):
        from unittest.mock import patch,AsyncMock
        from backend.models import PowerRawSample
        await self.record(0);await self.record(60)
        with patch('backend.power_logging.refresh_rollups',new=AsyncMock(side_effect=RuntimeError('storage failure'))):
            with self.assertRaises(RuntimeError):await self.monitor.cleanup(now=T+timedelta(days=31))
        async with self.sessions() as db:
            self.assertGreater(await db.scalar(select(func.count()).select_from(PowerRawSample)),0)

    async def test_checkpoint_retains_interval_crossing_hour_boundary(self):
        await self.record(3590);await self.record(3650)
        now=T+timedelta(days=30,hours=1)
        await self.monitor.cleanup(now=now)
        await self.record(3710)
        await self.monitor.cleanup(now=now+timedelta(hours=1))
        first=await self.monitor.power_rollups('hour',365,'internal','dcmi',now=now+timedelta(hours=1))
        self.assertAlmostEqual(sum(row['energy_wh'] for row in first['buckets']),120*120/3600)
        self.assertAlmostEqual(sum(row['observed_seconds'] for row in first['buckets']),120)
        # Idempotent after all raw samples have passed the retention boundary.
        await self.monitor.cleanup(now=now+timedelta(hours=2))
        second=await self.monitor.power_rollups('hour',365,'internal','dcmi',now=now+timedelta(hours=1))
        self.assertEqual(first,second)

    async def test_hourly_retained_after_minute_retention_expires(self):
        await self.record(0);await self.record(60)
        now=T+timedelta(days=366)
        await self.monitor.cleanup(now=now)
        self.assertEqual((await self.monitor.power_rollups('minute',365,'internal','dcmi',now=now))['buckets'],[])
        hourly=await self.monitor.power_rollups('hour',3650,'internal','dcmi',now=now)
        self.assertAlmostEqual(sum(row['energy_wh'] for row in hourly['buckets']),2)

    async def test_equal_count_cohort_change_emits_a_chart_break(self):
        await self.record(0)
        replacement=Server(id=2,name='B',host='b',ssh_user='monitor',network='internal')
        async with self.sessions() as db:db.add(replacement);await db.commit()
        at=T+timedelta(seconds=60)
        snapshot=parse_sensor_snapshot('Instantaneous power reading: 130 Watts',at.isoformat(),None)
        await self.monitor.archive_round([replacement],{'2':snapshot},at)
        history=await self.monitor.power_history(1,'internal',now=at)
        self.assertEqual([row['power_watts'] for row in history['points']],[120,None,130])
        self.assertEqual(history['summary']['covered_seconds'],0)

    async def test_historical_interval_change_uses_recorded_deadlines_for_chart(self):
        self.monitor.interval=30
        await self.record(0)
        self.monitor.interval=60
        await self.record(90)
        history=await self.monitor.power_history(1,'internal',now=T+timedelta(seconds=90))
        self.assertEqual([row['power_watts'] for row in history['points']],[120,None,120])
        self.assertEqual(history['summary']['covered_seconds'],0)

    async def test_network_peak_is_observed_total_not_sum_of_server_peaks(self):
        second=Server(id=2,name='B',host='b',ssh_user='monitor',network='internal')
        async with self.sessions() as db:db.add(second);await db.commit()
        servers=self.servers+[second]
        for seconds,powers in [(0,(100,200)),(60,(200,100))]:
            at=T+timedelta(seconds=seconds)
            snapshots={str(server.id):parse_sensor_snapshot(f'Instantaneous power reading: {watts} Watts',at.isoformat(),None) for server,watts in zip(servers,powers)}
            await self.monitor.archive_round(servers,snapshots,at)
        summary=(await self.monitor.power_history(1,'internal',now=T+timedelta(seconds=60)))['summary']
        self.assertEqual(summary['max_watts'],300)
        self.assertEqual(summary['time_weighted_avg_watts'],300)

    async def test_power_summary_peak_at_is_actual_source_specific_timestamp(self):
        await self.record(0,watts=100,ac=200)
        await self.record(30,watts=300,ac=150)
        await self.record(60,watts=200,ac=400)
        dcmi=await self.monitor.power_history(1,'internal',now=T+timedelta(seconds=60))
        ac=await self.monitor.power_history(1,'internal',source='ac_input',now=T+timedelta(seconds=60))
        self.assertEqual(dcmi['summary'].get('peak_at'),(T+timedelta(seconds=30)).isoformat())
        self.assertEqual(ac['summary'].get('peak_at'),(T+timedelta(seconds=60)).isoformat())

    async def test_power_downsampling_preserves_actual_peak_and_gap(self):
        from unittest.mock import patch,AsyncMock
        points=[]
        for index in range(5000):
            value=None if index==2501 else 5000 if index==2500 else 100
            points.append(dict(collected_at=(T+timedelta(seconds=30*index)).isoformat(),power_watts=value,server_count=0 if value is None else 1,expected_count=1,complete=value is not None))
        with patch('backend.power_logging.history',new=AsyncMock(return_value={'points':points,'summary':{}})):
            result=await self.monitor.power_history(168,'internal')
        self.assertLessEqual(len(result['points']),1500)
        peak=max((point for point in result['points'] if point['power_watts'] is not None),key=lambda point:point['power_watts'])
        self.assertEqual(peak['power_watts'],5000)
        self.assertEqual(peak['collected_at'],points[2500]['collected_at'])
        self.assertTrue(any(point['power_watts'] is None for point in result['points']))
