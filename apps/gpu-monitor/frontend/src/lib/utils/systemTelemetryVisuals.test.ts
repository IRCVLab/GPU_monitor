// @ts-nocheck
import test from 'node:test';import assert from 'node:assert/strict';
import * as visuals from './systemTelemetryVisuals.ts';
test('RPM animation is bounded, faster for high RPM, and stopped for zero, missing or stale data',()=>{
 assert.equal(visuals.fanDuration(0,true),null);assert.equal(visuals.fanDuration(null,true),null);assert.equal(visuals.fanDuration(9000,false),null);
 assert.ok(visuals.fanDuration(12000,true)<visuals.fanDuration(3000,true));assert.ok(visuals.fanDuration(100000,true)>=.35);
});
test('thermal thresholds change functional color and missing values stay neutral',()=>{
 assert.equal(visuals.temperatureTone(null),'unknown');assert.equal(visuals.temperatureTone(45),'normal');assert.equal(visuals.temperatureTone(80),'warning');assert.equal(visuals.temperatureTone(95),'critical');
 assert.equal(visuals.temperatureTone(70,65,85),'warning');
});
test('PSU bars use actual capacity when supplied and otherwise explicitly represent input share',()=>{
 assert.deepEqual(visuals.psuLoad(250,1000),{ratio:.25,kind:'share'});assert.deepEqual(visuals.psuLoad(250,1000,500),{ratio:.5,kind:'capacity'});
 assert.deepEqual(visuals.psuLoad(null,1000),{ratio:null,kind:'share'});assert.equal(visuals.psuLoad(0,1000).ratio,0);
});
test('telemetry geometry keeps actual time spacing, valid zero, isolated points and gaps',()=>{
 const at=seconds=>new Date(seconds*1000).toISOString();
 const result=visuals.telemetrySegments([{collected_at:at(0),value:0},{collected_at:at(60),value:10},{collected_at:at(120),value:null},{collected_at:at(180),value:20},{collected_at:at(600),value:30}],0,600000,100,40);
 assert.deepEqual(result.map(s=>s.length),[2,1,1]);assert.equal(result[0][0].x,0);assert.equal(result[0][0].y,38);assert.equal(result[0][1].x,10);assert.equal(result[2][0].x,100);
 assert.deepEqual(visuals.telemetrySegments([],0,600000,100,40),[]);
});
test('power delta uses actual watt difference, preserves zero baseline and never invents a previous value',()=>{
 assert.equal(visuals.telemetryDelta([{collected_at:'2026-01-01T00:00:00Z',value:100},{collected_at:'2026-01-01T00:01:00Z',value:125}]),25);
 assert.equal(visuals.telemetryDelta([{collected_at:'2026-01-01T00:00:00Z',value:0},{collected_at:'2026-01-01T00:01:00Z',value:10}]),10);
 assert.equal(visuals.telemetryDelta([]),null);
});
test('mini chart window fits actual recent data while retaining real spacing and a one-minute single-point span',()=>{
 const now=Date.parse('2026-11-12T08:00:00Z');const point=(age,value=1)=>({collected_at:new Date(now-age).toISOString(),value});
 assert.deepEqual(visuals.telemetryWindow([point(180000),point(60000)],now),{start:now-180000,end:now});
 assert.deepEqual(visuals.telemetryWindow([point(1000)],now),{start:now-60000,end:now});
 assert.deepEqual(visuals.telemetryWindow([point(3600000),point(60000)],now),{start:now-3600000,end:now});
 assert.deepEqual(visuals.telemetryWindow([point(7200000),point(300000,null),point(120000)],now),{start:now-120000,end:now});
 assert.deepEqual(visuals.telemetryWindow([],now),{start:now-3600000,end:now});
});

test('GPU activity matches idle baseline and counts users, utilization or allocated memory',()=>{
 const now=Date.parse('2026-11-12T08:00:00Z');const idle={users:[],utilization:5,memory_used:10,memory_total:100};
 const state={status:'online',last_seen:new Date(now-1000).toISOString(),gpus:[idle,{...idle,users:['kim']},{...idle,utilization:6},{...idle,memory_used:11}]};
 assert.deepEqual(visuals.gpuActivity(state,now),{active:3,total:4});
});
test('stale or incomplete GPU inventory reports unknown activity with physical total',()=>{
 const now=Date.parse('2026-11-12T08:00:00Z');const state={status:'online',last_seen:new Date(now).toISOString(),gpus:[{users:['kim'],utilization:50,memory_used:10,memory_total:100}]};
 assert.deepEqual(visuals.gpuActivity({...state,last_seen:new Date(now-61000).toISOString()},now),{active:null,total:1});
 assert.deepEqual(visuals.gpuActivity({...state,status:'offline'},now),{active:null,total:1});
 assert.deepEqual(visuals.gpuActivity({...state,status:'unknown'},now),{active:null,total:1});
 assert.deepEqual(visuals.gpuActivity({...state,gpu_inventory:{pci_count:4}},now),{active:null,total:4});
 assert.deepEqual(visuals.gpuActivity({...state,last_seen:null},now),{active:null,total:1});
});
test('fresh zero-GPU and degraded servers retain truthful activity counts',()=>{
 const now=Date.parse('2026-11-12T08:00:00Z');
 assert.deepEqual(visuals.gpuActivity({status:'online',last_seen:new Date(now).toISOString(),gpus:[],gpu_inventory:{pci_count:0}},now),{active:0,total:0});
 assert.deepEqual(visuals.gpuActivity({status:'degraded',last_seen:new Date(now).toISOString(),gpus:[{users:[],utilization:0,memory_used:0,memory_total:0}]},now),{active:0,total:1});
});
