import asyncio
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.models import Base, Server, HardwareRound, PowerPsuReading, PowerPsuIdentity, PowerRawSample, PowerFlowRevision
from backend.power_flow import service
from backend.tests.test_power_flow_engine import graph, neumann_graph, T

class PowerFlowApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from backend.config import Settings
        self.settings=Settings(_env_file=None,secret_key='isolated-testing-secret-key',admin_password='isolated-testing-password',database_url='sqlite+aiosqlite:///:memory:',monitoring_disable_collectors=True,monitoring_disable_slack=True)
        self.config_patch=patch('backend.config.get_settings',return_value=self.settings);self.config_patch.start();self.addCleanup(self.config_patch.stop)
        from backend.routers import power_flow
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine=create_async_engine(f'sqlite+aiosqlite:///{Path(self.temp.name)/"flow.db"}')
        self.sessions=async_sessionmaker(self.engine,expire_on_commit=False)
        async with self.engine.begin() as conn:await conn.run_sync(Base.metadata.create_all)
        async def session():
            async with self.sessions() as db:yield db
        app=FastAPI();app.include_router(power_flow.router);app.dependency_overrides[power_flow.get_db]=session
        self.router_config_patch=patch.object(power_flow,'get_settings',return_value=self.settings);self.router_config_patch.start();self.addCleanup(self.router_config_patch.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
        self.clock=patch.object(service,'now_utc',return_value=T);self.clock.start();self.addCleanup(self.clock.stop)
        self.auth={'admin_password':self.settings.admin_password}
        async with self.sessions() as db:
            db.add(Server(id=1,name='A',host='a',ssh_user='monitor'));db.add(PowerPsuIdentity(server_id=1,sensor_name='PS1 Input Power',direction='input'));await db.commit()
    async def asyncTearDown(self):
        await self.client.aclose();await self.engine.dispose()
    async def record(self,at,values):
        async with self.sessions() as db:
            round_=HardwareRound(collected_at=at.replace(tzinfo=None));db.add(round_);await db.flush()
            db.add(PowerRawSample(round_id=round_.id,scope='network:all',source='ac_input',observed_at=at.replace(tzinfo=None),power_watts=sum(v for v in values if v is not None),cohort='1',expected_count=1,server_count=1,interval_seconds=30))
            for slot,value in enumerate(values,1):db.add(PowerPsuReading(round_id=round_.id,server_id=1,sensor_name=f'PS{slot} Input Power',direction='input',power_watts=value,status='ok',observed_at=at.replace(tzinfo=None)))
            await db.commit()
    async def create(self,g=None,scenario=False):
        response=await self.client.post('/power-flow/diagrams',json=dict(name='Rack',graph=g or graph(),is_scenario=scenario,**self.auth));self.assertEqual(response.status_code,201,response.text);return response.json()
    async def test_auth_validation_404_and_utc(self):
        self.assertEqual((await self.client.post('/power-flow/diagrams',json={'name':'A','admin_password':'wrong'})).status_code,401)
        self.assertEqual((await self.client.post('/power-flow/diagrams',json={'name':'A','admin_password':'잘못된 비밀번호'})).status_code,401)
        g=graph();g['nodes'][0]['capacity_a']=-1
        self.assertEqual((await self.client.post('/power-flow/evaluate',json={'graph':g})).status_code,422)
        self.assertEqual((await self.client.get('/power-flow/diagrams/absent')).status_code,404)
        for at in ('2026-11-13T00:00:00','2026-11-13T09:00:00+09:00'):
            self.assertEqual((await self.client.post('/power-flow/evaluate',json={'graph':graph(),'at':at})).status_code,422)
        self.assertEqual((await self.client.post('/power-flow/evaluate',json={'graph':graph(),'hours':721})).status_code,422)

    async def test_thirty_day_window_replays_coincident_peaks_and_clips_actual_revision(self):
        g=graph();g['nodes'][0]['ports'].append(dict(id='two',label='two',direction='output'))
        p=copy.deepcopy(g['nodes'][2]);p.update(id='p2',psu_slot='PSU2',power_factor=1);g['nodes'].append(p)
        g['edges'].append(dict(id='two',source='b',source_port='two',target='p2',target_port='in'))
        await self.record(T-timedelta(days=31),[5000,5000])
        amp_peak_at=T-timedelta(days=20)
        watts_peak_at=T-timedelta(days=10)
        await self.record(amp_peak_at,[440,0])
        await self.record(T-timedelta(days=19),[0,660])
        await self.record(watts_peak_at,[110,660])
        await self.record(T,[110,110])
        scenario=await self.create(g,scenario=True)
        actual=await self.create(g)
        requests=(
            await self.client.post('/power-flow/evaluate',json={'graph':g,'hours':720}),
            await self.client.get(f'/power-flow/diagrams/{scenario["id"]}/state',params={'hours':720}),
        )
        for response in requests:
            self.assertEqual(response.status_code,200,response.text)
            state=response.json();self.assertEqual(state['history_status'],'projection')
            for key in ('b','s'):
                metric=state['nodes'][key]
                self.assertEqual(metric['watts'],220)
                self.assertEqual(metric['peak_watts'],770)
                self.assertEqual(metric['peak_amps'],4)
                self.assertEqual(metric['peak_watts_at'],service.iso(watts_peak_at))
                self.assertEqual(metric['peak_amps_at'],service.iso(amp_peak_at))
            self.assertEqual(state['nodes']['p']['peak_watts'],440)
            self.assertEqual(state['nodes']['p2']['peak_watts'],660)
            self.assertNotEqual(state['nodes']['b']['peak_watts'],440+660)
            self.assertNotEqual(state['nodes']['b']['peak_amps'],4+3)
            self.assertEqual(state['edges']['e']['peak_watts'],440)
            self.assertEqual(state['ports']['b:two']['peak_watts'],660)
        for params in ({},{'hours':168}):
            recent=await self.client.get(f'/power-flow/diagrams/{scenario["id"]}/state',params=params)
            self.assertEqual(recent.status_code,200,recent.text)
            self.assertEqual(recent.json()['nodes']['b']['peak_watts'],220)
        response=await self.client.get(f'/power-flow/diagrams/{actual["id"]}/state',params={'hours':720})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['history_status'],'partial')
        self.assertEqual(response.json()['nodes']['b']['peak_watts'],220)

    async def test_peak_window_rejects_hours_outside_retained_range(self):
        created=await self.create()
        for hours in (0,721):
            response=await self.client.post('/power-flow/evaluate',json={'graph':graph(),'hours':hours})
            self.assertEqual(response.status_code,422,response.text)
            response=await self.client.get(f'/power-flow/diagrams/{created["id"]}/state',params={'hours':hours})
            self.assertEqual(response.status_code,422,response.text)
    async def test_persistence_revision_conflict_clone_and_catalog(self):
        await self.record(T,[220]);created=await self.create()
        self.assertNotIn('admin_password',str(created))
        self.assertEqual((await self.client.get('/power-flow/diagrams')).json()[0]['id'],created['id'])
        self.assertEqual((await self.client.get('/power-flow/catalog')).json()['servers'][0]['psus'][0]['slot'],'PSU1')
        body=dict(base_version=1,name='Updated',graph=graph(),**self.auth)
        results=await asyncio.gather(*[self.client.put(f'/power-flow/diagrams/{created["id"]}',json=body) for _ in range(2)])
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        async with self.sessions() as db:self.assertEqual(await db.scalar(select(func.count()).select_from(PowerFlowRevision)),2)
        clone=await self.client.post(f'/power-flow/diagrams/{created["id"]}/clone',json=dict(name='Plan',**self.auth));self.assertEqual(clone.status_code,201);self.assertTrue(clone.json()['is_scenario'])
    async def test_server_rack_metadata_roundtrips_without_changing_current_or_peaks(self):
        await self.record(T,[220])
        baseline=(await self.client.post('/power-flow/evaluate',json={'graph':graph()})).json()
        grouped=graph();grouped['nodes'][1]['rack']='1'
        created=await self.create(grouped)
        diagram_url=f'/power-flow/diagrams/{created["id"]}'
        loaded=(await self.client.get(diagram_url)).json()
        self.assertEqual(loaded['graph']['nodes'][1]['rack'],'1')
        grouped['nodes'][1]['rack']='2'
        saved=await self.client.put(diagram_url,json=dict(base_version=1,name='Rack moved',graph=grouped,**self.auth))
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(saved.json()['graph']['nodes'][1]['rack'],'2')
        clone=await self.client.post(f'{diagram_url}/clone',json=dict(name='Rack copy',**self.auth))
        self.assertEqual(clone.status_code,201,clone.text)
        self.assertEqual(clone.json()['graph']['nodes'][1]['rack'],'2')
        state=(await self.client.get(f'{diagram_url}/state')).json()
        self.assertEqual(state['graph']['nodes'][1]['rack'],'2')
        for area in ('nodes','edges','ports'):
            self.assertEqual(state[area],baseline[area],area)
        self.assertEqual(state['nodes']['b']['peak_watts'],220)
        self.assertEqual(state['nodes']['b']['watts'],220)
        self.assertEqual(state['nodes']['b']['capacity_a'],16)

    async def test_history_before_topology_retention_projection_and_revision_selection(self):
        created=await self.create()
        before=T-timedelta(minutes=1)
        await self.record(before,[220]);await self.record(T,[110])
        state=(await self.client.get(f'/power-flow/diagrams/{created["id"]}/state',params={'at':before.isoformat()})).json()
        self.assertEqual(state['history_status'],'before_topology');self.assertIsNone(state['graph'])
        old=T-timedelta(days=31)
        state=(await self.client.post('/power-flow/evaluate',json={'graph':graph(),'at':old.isoformat()})).json();self.assertEqual(state['history_status'],'out_of_retention')
        projection=(await self.client.post('/power-flow/evaluate',json={'graph':graph(),'at':before.isoformat()})).json()
        self.assertEqual(projection['history_status'],'projection');self.assertEqual(projection['nodes']['b']['watts'],220)
        actual=(await self.client.get(f'/power-flow/diagrams/{created["id"]}/state')).json()
        self.assertEqual(actual['history_status'],'partial');self.assertEqual(actual['nodes']['b']['peak_watts'],110)
        later=T+timedelta(minutes=1)
        with patch.object(service,'now_utc',return_value=later):
            await self.client.put(f'/power-flow/diagrams/{created["id"]}',json=dict(base_version=1,name='Changed',graph=graph(),**self.auth))
        historical=(await self.client.get(f'/power-flow/diagrams/{created["id"]}/state',params={'at':T.isoformat()})).json()
        self.assertEqual(historical['version'],1);self.assertEqual(historical['effective_from'],created['effective_from'])
    async def test_same_round_no_fallback_independent_peaks_and_unknown(self):
        g=graph();g['nodes'][0]['ports'].append(dict(id='two',label='two',direction='output',capacity_a=16))
        p=copy.deepcopy(g['nodes'][2]);p.update(id='p2',psu_slot='PSU2',power_factor=1);g['nodes'].append(p)
        g['edges'].append(dict(id='two',source='b',source_port='two',target='p2',target_port='in'))
        await self.record(T-timedelta(seconds=60),[220,0])
        await self.record(T-timedelta(seconds=30),[0,330])
        await self.record(T,[None,440])
        result=(await self.client.post('/power-flow/evaluate',json={'graph':g})).json()['nodes']['b']
        self.assertIsNone(result['watts']);self.assertEqual(result['known_watts'],440)
        self.assertEqual(result['peak_watts'],330);self.assertEqual(result['peak_amps'],2)
        self.assertNotEqual(result['peak_watts_at'],result['peak_amps_at'])

    async def test_catalog_preserves_current_sensor_alias_and_same_round_missing(self):
        async with self.sessions() as db:
            db.add(PowerPsuIdentity(server_id=1,sensor_name='PSU1 Power In',direction='input'))
            db.add(PowerPsuIdentity(server_id=1,sensor_name='PSU1 Power Out',direction='output'))
            await db.commit()
        await self.record(T-timedelta(seconds=30),[220])
        current=(await self.client.get('/power-flow/catalog')).json()['servers'][0]['psus'][0]
        self.assertEqual(current['watts'],220)
        self.assertNotEqual(current['status'],'ambiguous')
        await self.record(T,[None])
        psus=(await self.client.get('/power-flow/catalog')).json()['servers'][0]['psus']
        self.assertEqual(len(psus),1)
        self.assertEqual(psus[0]['sensor_name'],'PS1 Input Power')
        self.assertIsNone(psus[0]['watts'])

    async def test_fast_state_skips_history_and_default_still_includes_peaks(self):
        await self.record(T,[220]);created=await self.create()
        from backend.routers import power_flow
        with patch.object(service,'_scan_peaks',side_effect=AssertionError('History must not be scanned')):
            response=await self.client.post('/power-flow/evaluate',json={'graph':graph(),'hours':720,'include_peaks':False})
            self.assertEqual(response.status_code,200,response.text)
            result=response.json();self.assertFalse(result['peaks_included'])
            self.assertEqual(result['nodes']['b']['watts'],220)
            self.assertIsNone(result['nodes']['b']['peak_watts'])
            response=await self.client.get(f'/power-flow/diagrams/{created["id"]}/state',params={'hours':720,'include_peaks':'false'})
            self.assertEqual(response.status_code,200,response.text);self.assertFalse(response.json()['peaks_included'])
            self.assertEqual(response.json()['history_status'],'pending')
            self.assertEqual(response.json()['nodes'],result['nodes'])
            self.assertEqual(response.json()['edges'],result['edges'])
            self.assertEqual(response.json()['ports'],result['ports'])
        result=(await self.client.post('/power-flow/evaluate',json={'graph':graph()})).json()
        self.assertTrue(result['peaks_included']);self.assertEqual(result['nodes']['b']['peak_watts'],220)

    async def test_fast_history_is_pending_until_historical_gaps_are_scanned(self):
        with patch.object(service,'now_utc',return_value=T-timedelta(hours=2)):
            created=await self.create()
        await self.record(T-timedelta(minutes=30),[None])
        await self.record(T,[220])
        path=f'/power-flow/diagrams/{created["id"]}/state'
        fast=(await self.client.get(path,params={'hours':1,'include_peaks':'false'})).json()
        self.assertEqual(fast['history_status'],'pending')
        self.assertFalse(fast['peaks_included']);self.assertEqual(fast['nodes']['b']['watts'],220)
        full=(await self.client.get(path,params={'hours':1})).json()
        self.assertEqual(full['history_status'],'partial');self.assertTrue(full['peaks_included'])

    async def test_four_underscore_psus_appear_in_catalog_current_and_raw_peaks(self):
        async with self.sessions() as db:
            db.add(Server(id=13,name='Neumann',host='neumann',ssh_user='monitor'))
            for slot in range(1,5):
                for direction,suffix in (('input','In'),('output','Out')):
                    db.add(PowerPsuIdentity(server_id=13,sensor_name=f'Pwr_PSU{slot}_{suffix}',direction=direction))
            for seconds,values in ((-30,(200,200,200,200)),(0,(136,144,144,136))):
                at=(T+timedelta(seconds=seconds)).replace(tzinfo=None)
                round_=HardwareRound(collected_at=at);db.add(round_);await db.flush()
                db.add(PowerRawSample(round_id=round_.id,scope='network:all',source='ac_input',observed_at=at,power_watts=sum(values),cohort='13',expected_count=1,server_count=1,interval_seconds=30))
                for slot,value in enumerate(values,1):
                    db.add(PowerPsuReading(round_id=round_.id,server_id=13,sensor_name=f'Pwr_PSU{slot}_In',direction='input',power_watts=value,status='ok',observed_at=at))
                    db.add(PowerPsuReading(round_id=round_.id,server_id=13,sensor_name=f'Pwr_PSU{slot}_Out',direction='output',power_watts=999,status='ok',observed_at=at))
            await db.commit()
        catalog=(await self.client.get('/power-flow/catalog')).json()
        psus=next(server['psus'] for server in catalog['servers'] if server['server_id']==13)
        self.assertEqual([psu['slot'] for psu in psus],['PSU1','PSU2','PSU3','PSU4'])
        self.assertEqual([psu['sensor_name'] for psu in psus],[f'Pwr_PSU{i}_In' for i in range(1,5)])
        self.assertEqual([psu['watts'] for psu in psus],[136,144,144,136])
        response=await self.client.post('/power-flow/evaluate',json={'graph':neumann_graph()})
        self.assertEqual(response.status_code,200,response.text)
        state=response.json()
        for key in ('b','strip','s'):
            self.assertEqual(state['nodes'][key]['watts'],560)
            self.assertEqual(state['nodes'][key]['peak_watts'],800)
            self.assertAlmostEqual(state['nodes'][key]['peak_amps'],800/220)
        self.assertFalse(any(psu['server_id']==13 for psu in state['unmapped_psus']))
