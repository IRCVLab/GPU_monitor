import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from backend.collectors import system
from backend.models import Base,Server
from backend.hardware import HardwareMonitor,parse_sensor_snapshot

T=datetime(2026,11,12,8,tzinfo=timezone.utc)
BASE='1,1048576,2097152,0,0,0,100,200,1,1,0,1,0,0,0,4'

def net(rx,tx,at,interfaces=None):
    interfaces=interfaces or f'eno1:{rx}:{tx}'
    return system.parse_system(BASE+f',{rx},{tx},{at},{interfaces}')

class NetworkRateTests(unittest.TestCase):
    def test_append_only_parser_and_real_elapsed_rates(self):
        old=system.parse_system(BASE)
        self.assertIsNone(getattr(old,'network_rx_bytes_per_second','missing'))
        previous=net(100,200,10);current=net(300,800,12)
        rate=system.calculate_network_io_rate(previous,current)
        self.assertEqual(rate.rx_bytes_per_second,100)
        self.assertEqual(rate.tx_bytes_per_second,300)
    def test_first_reset_missing_interface_change_and_per_interface_reset_are_null(self):
        previous=net(100,200,10)
        for current in [None,system.parse_system(BASE),net(50,250,12),net(150,250,10),net(150,250,12,'eno2:150:250'),net(150,250,'nan')]:
            self.assertIsNone(system.calculate_network_io_rate(previous,current))
        self.assertIsNone(system.calculate_network_io_rate(None,previous))
        # Aggregate counters increase, but one physical interface resets.
        previous=net(100,100,10,'eno1:50:50;eno2:50:50')
        current=net(200,200,12,'eno1:10:10;eno2:190:190')
        self.assertIsNone(system.calculate_network_io_rate(previous,current))
    def test_remote_reader_uses_physical_device_links_and_rejects_missing_interfaces(self):
        namespace={}
        exec(system.NETWORK_READER_SOURCE,namespace)
        from io import StringIO
        files='/proc/net/dev'
        raw='eno1: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\nlo: 9000 0 0 0 0 0 0 0 9000 0 0 0 0 0 0 0\nbond0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0\nveth0: 9999 0 0 0 0 0 0 0 9999 0 0 0 0 0 0 0'
        with patch('glob.glob',return_value=['/sys/class/net/eno1','/sys/class/net/lo','/sys/class/net/bond0','/sys/class/net/veth0']),patch('os.path.islink',side_effect=lambda path:path=='/sys/class/net/eno1/device'),patch('builtins.open',return_value=StringIO(raw)),patch('time.monotonic',return_value=12):
            self.assertEqual(namespace['read_network_counters'](),(100,200,12,'eno1:100:200'))

class ServerHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as db:await db.run_sync(Base.metadata.create_all)
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        self.servers=[Server(id=1,name='A',host='a',ssh_user='monitor',network='internal'),Server(id=2,name='B',host='b',ssh_user='monitor',network='external'),Server(id=3,name='Poseidon',host='c',ssh_user='monitor',network='internal')]
        async with self.sessions() as db:db.add_all(self.servers);await db.commit()
        self.monitor=HardwareMonitor(self.sessions);self.addCleanup(self.monitor.close)
    async def asyncTearDown(self):await self.engine.dispose()
    async def test_bulk_histories_are_actual_filtered_and_separated(self):
        from backend.models import NetworkMetric,GpuMetric
        snapshot=parse_sensor_snapshot('CPU1 Temp | 45 | degrees C | ok\nCPU2 Temp | 55 | degrees C | ok\nPSU1 Power In | 300 | Watts | ok\nPSU1 Power Out | 280 | Watts | ok\nInstantaneous power reading: 250 Watts',T.isoformat(),None)
        await self.monitor.archive_round(self.servers,{'1':snapshot},T)
        async with self.sessions() as db:
            db.add(NetworkMetric(server_id=1,network_rx_bytes_per_second=100,network_tx_bytes_per_second=200,collected_at=T.replace(tzinfo=None),interval_seconds=60))
            db.add(GpuMetric(server_id=1,gpu_index=0,temperature=65,collected_at=T.replace(tzinfo=None)));await db.commit()
        result=await self.monitor.server_history(1,'internal',now=T)
        self.assertEqual(set(result['servers']),{'1'})
        card=result['servers']['1']
        for key,value in [('power',250),('cpu_temperature',55),('gpu_temperature',65),('network_rx',100),('network_tx',200)]:self.assertEqual(card[key][0]['value'],value)
        self.assertEqual(card['psu']['PSU1'][0]['value'],300)
        external=await self.monitor.server_history(1,'external',now=T)
        self.assertEqual(set(external['servers']),{'2'})
        self.assertEqual(external['servers']['2']['network_rx'],[])
        self.assertEqual(external['servers']['2']['power'],[])
        self.assertEqual(external['servers']['2']['cpu_temperature'],[])
    async def test_network_archives_without_gpu_and_prunes(self):
        from backend.models import NetworkMetric
        from backend.config import Settings
        from unittest.mock import AsyncMock
        settings=Settings(_env_file=None,secret_key='isolated-testing-secret-key',admin_password='isolated-testing-password',database_url='sqlite+aiosqlite:///:memory:',monitoring_disable_collectors=True,monitoring_disable_slack=True)
        with patch('backend.config.get_settings',return_value=settings):
            from backend.collectors.server_collector import ServerCollector
        collector=ServerCollector(self.servers[0])
        with patch('backend.collectors.server_collector.AsyncSessionLocal',self.sessions),patch('backend.collectors.server_collector.get_settings',return_value=settings):
            await collector._archive({'gpus':[],'system':{'network_rx_bytes_per_second':100,'network_tx_bytes_per_second':200},'system_collected_at':(T-timedelta(days=8)).isoformat()})
            async with self.sessions() as db:
                row=(await db.execute(select(NetworkMetric))).scalar_one()
                self.assertEqual(row.network_rx_bytes_per_second,100)
                self.assertEqual(row.collected_at,(T-timedelta(days=8)).replace(tzinfo=None))
            await collector._cleanup_history()
            async with self.sessions() as db:self.assertEqual(await db.scalar(select(func.count()).select_from(NetworkMetric)),0)

    async def test_collector_ws_rates_warm_up_and_reset_after_missing_system_sample(self):
        from backend.config import Settings
        from types import SimpleNamespace
        settings=Settings(_env_file=None,secret_key='isolated-testing-secret-key',admin_password='isolated-testing-password',database_url='sqlite+aiosqlite:///:memory:',monitoring_disable_collectors=True,monitoring_disable_slack=True)
        with patch('backend.config.get_settings',return_value=settings):
            from backend.collectors.server_collector import ServerCollector
        collector=ServerCollector(self.servers[0]);collector._ssh=SimpleNamespace(is_connected=True)
        values=[BASE+',100,200,10,eno1:100:200',BASE+',300,800,12,eno1:300:800',None,BASE+',500,1000,16,eno1:500:1000']
        with patch.object(collector,'_sync_collect_gpu',side_effect=RuntimeError('GPU unavailable')),patch.object(collector,'_sync_collect_storage',return_value=None),patch.object(collector,'_sync_collect_system',side_effect=values):
            first,_,_=await collector._collect_once()
            second,_,_=await collector._collect_once()
            missing,_,_=await collector._collect_once()
            after,_,_=await collector._collect_once()
        self.assertIsNone(first['system']['network_rx_bytes_per_second'])
        self.assertEqual(second['system']['network_rx_bytes_per_second'],100)
        self.assertEqual(second['system']['network_tx_bytes_per_second'],300)
        self.assertIsNone(missing['system'])
        self.assertIsNone(after['system']['network_rx_bytes_per_second'])

class SeriesTests(unittest.TestCase):
    def test_bound_preserves_peak_zero_and_missing_edges(self):
        from backend.server_history import Series
        end=T+timedelta(hours=24)
        builder=Series(T,end)
        for index in range(2881):
            value=10000 if index==1001 else None if index==1002 else 0
            builder.add(T+timedelta(seconds=30*index),value,30)
        points=builder.finish()
        self.assertLessEqual(len(points),120)
        self.assertEqual(max(point['value'] for point in points if point['value'] is not None),10000)
        self.assertTrue(any(point['value']==0 for point in points))
        self.assertTrue(any(point['value'] is None for point in points))
    def test_remote_script_remains_shell_valid_and_appends_csv_fields(self):
        import shlex
        command=shlex.split(system.SYSTEM_CMD_PROC)
        self.assertEqual(command[:2],['python3','-c'])
        compile(command[2],'<system-proc>','exec')
        self.assertIn('/proc/net/dev',command[2])
        self.assertIn('{cpu_count},{network_rx},{network_tx},{network_time},{network_interfaces}',command[2])

    def test_decreasing_interval_gap_stays_between_observations_and_in_window(self):
        from backend.server_history import Series
        end=T+timedelta(seconds=120)
        builder=Series(T,end)
        builder.add(T,100,3600)
        builder.add(end,200,30)
        points=builder.finish()
        times=[datetime.fromisoformat(point['collected_at']) for point in points]
        self.assertEqual(times,sorted(times))
        self.assertTrue(all(T<=at<=end for at in times))
        self.assertEqual(len(points),3)
        self.assertIsNone(points[1]['value'])
        self.assertLess(times[0],times[1]);self.assertLess(times[1],times[2])
