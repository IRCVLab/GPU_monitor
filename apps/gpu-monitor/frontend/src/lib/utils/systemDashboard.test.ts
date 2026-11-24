// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { buildSystemNode, summarizeSystemNodes, filterSystemNodes, timeLabel, resourceFreshness, powerHistorySegments, powerStatistics, observedPowerPeak, mergePowerHistory } from './systemDashboard.ts';

const state = {
 server_id: 2, server_name: 'Hinton', host: 'example', port: 2202,
 network: 'internal', status: 'online', status_reason: null,
 last_seen: '2026-11-12T08:00:00Z', gpus: [], storage: null,
 system: { cpu_percent: 20, ram_used: 40, ram_total: 100 },
};

test('missing hardware remains unknown instead of turning into zero', () => {
 const node = buildSystemNode(state, undefined);
 assert.equal(node.powerWatts, null);
 assert.equal(node.cpuTemperature, null);
 assert.equal(node.fanRpm, null);
 assert.equal(node.ramPercent, 40);
});

test('hardware observations retain timestamps and skip unavailable sensors', () => {
 const hardware = { collected_at: '2026-11-12T07:00:00Z', power_watts: 353,
  sensors: [
   { name: 'CPU1 Temperature', kind: 'temperature', value: 37, upper_warning: 93 },
   { name: 'CPU2 Temperature', kind: 'temperature', value: null },
   { name: 'FRNT_FAN1', kind: 'fan', value: 5880 },
  ] };
 const node = buildSystemNode(state, hardware);
 assert.equal(node.cpuTemperature, 37);
 assert.equal(node.fanRpm, 5880);
 assert.equal(node.powerWatts, 353);
 assert.equal(node.hardware.collected_at, hardware.collected_at);
});

test('summary sums only observed power and reports coverage', () => {
 const nodes = [buildSystemNode(state, { power_watts: 353, sensors: [] }),
  buildSystemNode({ ...state, server_id: 3 }, undefined)];
 const summary = summarizeSystemNodes(nodes);
 assert.equal(summary.observedPowerWatts, 353);
 assert.equal(summary.powerWatts, null);
 assert.equal(summary.powerCoverage, 1);
 assert.equal(summary.total, 2);
});

test('storage never creates System warnings', () => {
 const node = buildSystemNode({ ...state, storage: { summary: {percent: 50},
  mounts: [{ mount: '/data', percent: 98 }] } }, undefined);
 assert.equal(node.needsAttention, false);
 assert.doesNotMatch(node.issues.join(' '), /data|저장/);
});

test('search and attention filters compose', () => {
 const nodes = [buildSystemNode(state, undefined),
  buildSystemNode({ ...state, server_id: 3, server_name: 'Turing', status: 'degraded',
    status_reason: {message: 'GPU 인식 누락'} }, undefined)];
 assert.equal(filterSystemNodes(nodes, 'tur', 'all', true).length, 1);
 assert.equal(filterSystemNodes(nodes, 'hinton', 'all', true).length, 0);
});

test('old hardware observations include the date, not only a clock time', () => {
 assert.match(timeLabel('2025-11-11T00:00:00Z'), /2025.*11.*11/);
});

test('critical fan status and critical-only thermal thresholds create issues', () => {
 const hardware = { sensors: [
  {name: 'FAN1', kind: 'fan', value: 0, status: 'lcr'},
  {name: 'CPU1', kind: 'temperature', value: 99, status: 'ok', upper_critical: 95},
  {name: 'Absent DIMM', kind: 'temperature', value: null, status: 'na'},
 ] };
 const node = buildSystemNode(state, hardware);
 assert.match(node.issues.join(' '), /FAN1/);
 assert.match(node.issues.join(' '), /CPU1/);
 assert.doesNotMatch(node.issues.join(' '), /Absent DIMM/);
});

test('resource freshness checks socket state, timestamps and unknown nodes', () => {
 const now = Date.parse('2026-11-12T08:00:10Z');
 assert.equal(resourceFreshness(state,true,now).kind, 'fresh');
 assert.equal(resourceFreshness({...state,status:'unknown',last_seen:null},true,now).kind,'waiting');
 assert.equal(resourceFreshness(state,false,now).kind,'disconnected');
 assert.equal(resourceFreshness(state,true,now+120000).kind,'stale');
});

const point = (seconds, power_watts=100, complete=true) => ({collected_at:new Date(Date.parse('2026-11-12T08:00:00Z')+seconds*1000).toISOString(),power_watts,server_count:complete?2:1,expected_count:2,complete});
test('power history uses actual complete observations and leaves collection gaps', () => {
 const points = [point(0),point(60,200),point(120,null,false),point(180,300),point(600,400)];
 assert.deepEqual(powerHistorySegments(points).map(segment=>segment.length),[2,1,1]);
 assert.deepEqual(powerStatistics(points),{average:250,min:100,max:400,count:4});
 assert.deepEqual(powerStatistics([]),{average:null,min:null,max:null,count:0});
});
test('history merges chronologically without duplicate or invented timestamps', () => {
 const merged=mergePowerHistory([point(60),point(120)], [point(0),point(60),point(180)]);
 assert.deepEqual(merged.map(p=>p.collected_at),[0,60,120,180].map(t=>point(t).collected_at));
});
test('server fans exclude GPU cooling fans and PSU input never adds to DCMI total', () => {
 const node=buildSystemNode(state,{source:'ssh-ipmi',collected_at:state.last_seen,power_watts:500,sensors:[
 {name:'FRNT_FAN1',kind:'fan',value:3000},{name:'GPU_FAN1',kind:'fan',value:9000},
 {name:'PS1 Input Power',kind:'power',value:270},{name:'PS1 Output Power',kind:'power',value:250},
 {name:'CPU1 Temp',kind:'temperature',value:45},{name:'HDD Temp',kind:'temperature',value:90,status:'critical'}
 ]});
 assert.equal(node.fanRpm,3000);
 assert.equal(node.serverFans.length,1);
 assert.equal(node.gpuFans.length,1);
 assert.equal(node.psuInput.length,1);
 assert.equal(node.psuOutput.length,1);
 assert.equal(node.powerWatts,500);
 assert.equal(node.hottestTemperature,45);
 assert.equal(node.needsAttention,false);
});
test('stale snapshots are not presented as a complete current aggregate', () => {
 const hardware={source:'ssh-ipmi',collected_at:state.last_seen,power_watts:500,sensors:[]};
 const node=buildSystemNode(state,hardware);
 assert.equal(summarizeSystemNodes([node],Date.parse(state.last_seen)+1000).powerWatts,500);
 assert.equal(summarizeSystemNodes([node],Date.parse(state.last_seen)+240000).powerWatts,null);
 assert.equal(summarizeSystemNodes([buildSystemNode(state,{...hardware,source:'ssh-ipmi-snapshot'})],Date.parse(state.last_seen)).powerWatts,null);
});
test('network scope controls both visible servers and aggregate coverage',()=>{
 const hardware={source:'ssh-ipmi',collected_at:state.last_seen,power_watts:100,sensors:[]};
 const nodes=[buildSystemNode(state,hardware),buildSystemNode({...state,server_id:3,network:'external',server_name:'External'},undefined)];
 const internal=filterSystemNodes(nodes,'','internal',false),external=filterSystemNodes(nodes,'','external',false);
 assert.equal(internal.length,1);assert.equal(external.length,1);
 assert.equal(summarizeSystemNodes(internal,Date.parse(state.last_seen)).powerWatts,100);
 assert.equal(summarizeSystemNodes(filterSystemNodes(nodes,'','all',false),Date.parse(state.last_seen)).powerWatts,null);
});
test('storage-only degradation does not create a System warning',()=>{
 const node=buildSystemNode({...state,status:'degraded',status_reason:{code:'storage_collection_failed',source:'storage'}},undefined);
 assert.deepEqual(node.issues,[]);
});

test('period statistics prefer server time-weighted energy and report coverage without extrapolation',async()=>{
 const { normalizePowerSummary }=await import('./systemDashboard.ts');
 const summary=normalizePowerSummary({energy_wh:250,covered_seconds:900,coverage_ratio:.25,time_weighted_avg_watts:1000,min_watts:500,max_watts:1500,source:'dcmi'});
 assert.equal(summary.energy_wh,250);
 assert.equal(summary.coverage_ratio,.25);
 assert.equal(summary.time_weighted_avg_watts,1000);
 assert.equal(summary.source,'dcmi');
});
test('missing or invalid period statistics stay unknown, including zero-duration energy',async()=>{
 const { normalizePowerSummary }=await import('./systemDashboard.ts');
 assert.equal(normalizePowerSummary(null),null);
 assert.equal(normalizePowerSummary({energy_wh:0,covered_seconds:0,coverage_ratio:0,time_weighted_avg_watts:null,min_watts:100,max_watts:100,source:'dcmi'}).energy_wh,null);
 assert.equal(normalizePowerSummary({energy_wh:NaN,covered_seconds:60,coverage_ratio:2,time_weighted_avg_watts:-1,source:'ac_input'}).coverage_ratio,null);
});
test('actual PSU sensor names classify Power In/Out and Pwr_PSUx_In/Out separately',()=>{
 const node=buildSystemNode(state,{power_watts:600,sensors:[
 {name:'PSU1 Power In',kind:'power',value:320},{name:'PSU1 Power Out',kind:'power',value:300},
 {name:'Pwr_PSU2_In',kind:'power',value:310},{name:'Pwr_PSU2_Out',kind:'power',value:290},
 ]});
 assert.deepEqual(node.psuInput.map(sensor=>sensor.value),[320,310]);
 assert.deepEqual(node.psuOutput.map(sensor=>sensor.value),[300,290]);
 assert.equal(node.powerWatts,600);
});
test('PSU table pairs input and output by device, sorts numeric indices and preserves unknowns',async()=>{
 const { buildPsuRows }=await import('./systemDashboard.ts');
 const rows=buildPsuRows([{name:'PSU4 Power In',value:512},{name:'Pwr_PSU1_In',value:0},{name:'PSU3 Power In',value:null}],[{name:'PSU1 Power Out',value:480},{name:'Pwr_PSU2_Out',value:256}],{PSU1:1600,PSU5:800});
 assert.deepEqual(rows,[{name:'PSU1',inputWatts:0,outputWatts:480,ratedOutputWatts:1600},{name:'PSU2',inputWatts:null,outputWatts:256,ratedOutputWatts:null},{name:'PSU3',inputWatts:null,outputWatts:null,ratedOutputWatts:null},{name:'PSU4',inputWatts:512,outputWatts:null,ratedOutputWatts:null},{name:'PSU5',inputWatts:null,outputWatts:null,ratedOutputWatts:800}]);
});

test('all power readings use kW while preserving small PSU values and missing data',async()=>{
 const {powerLabel}=await import('./systemDashboard.ts');
 assert.equal(powerLabel(500),'0.5 kW');
 assert.equal(powerLabel(16),'0.016 kW');
 assert.equal(powerLabel(1500),'1.5 kW');
 assert.equal(powerLabel(0),'0 kW');
 assert.equal(powerLabel(null),'—');
});

test('temperature curves keep unknown periods empty and separate max from mean',async()=>{
 const util=await import('./systemDashboard.ts');
 assert.equal(typeof util.normalizeTemperaturePoint,'function');
 assert.equal(typeof util.temperatureHistorySegments,'function');
 const points=[
  {collected_at:point(0).collected_at,max_celsius:80,avg_celsius:50,device_count:4},
  {collected_at:point(60).collected_at,max_celsius:90,avg_celsius:55,device_count:4},
  {collected_at:point(120).collected_at,max_celsius:null,avg_celsius:null,device_count:0},
  {collected_at:point(180).collected_at,max_celsius:70,avg_celsius:45,device_count:4},
 ].map(util.normalizeTemperaturePoint);
 assert.deepEqual(util.temperatureHistorySegments(points).map(segment=>segment.length),[2,1]);
 assert.equal(points[0].max_celsius,80);assert.equal(points[0].avg_celsius,50);
 assert.equal(util.normalizeTemperaturePoint({collected_at:'bad',max_celsius:99,avg_celsius:50,device_count:1}),null);
 assert.equal(util.normalizeTemperaturePoint({...points[0],avg_celsius:Infinity}).avg_celsius,null);
});

test('chart axes follow observed ranges with padding instead of forcing zero',async()=>{
 const util=await import('./systemDashboard.ts');
 assert.equal(typeof util.observedChartDomain,'function');
 assert.deepEqual(util.observedChartDomain([50,70,80],5,2),{min:45,max:85});
 assert.deepEqual(util.observedChartDomain([7200,10500],100,100),{min:6800,max:10900});
 assert.deepEqual(util.observedChartDomain([6000,6000],100,100),{min:5900,max:6100});
 assert.deepEqual(util.observedChartDomain([0],100,100),{min:0,max:100});
 assert.deepEqual(util.observedChartDomain([],100,100),{min:0,max:100});
});

test('period peak includes the measured sum when some servers are unavailable', () => {
 const complete = { collected_at:'2026-01-01T00:00:00Z', power_watts:540, server_count:3, expected_count:3, complete:true };
 const partial = { collected_at:'2026-01-01T00:01:00Z', power_watts:null, observed_watts:620, server_count:2, expected_count:3, complete:false };
 const missing = { collected_at:'2026-01-01T00:02:00Z', power_watts:null, server_count:0, expected_count:3, complete:false };
 assert.deepEqual(observedPowerPeak([complete, partial, missing]), {watts:620, at:partial.collected_at, coverage:'2/3'});
 assert.deepEqual(observedPowerPeak([partial, {...complete, power_watts:700}]), {watts:700, at:complete.collected_at});
 assert.equal(observedPowerPeak([missing]), null);
});
