// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';

test('server mini histories retain real timestamps, idle zero and explicit missing gaps',async()=>{
 const util=await import('./systemTelemetry.ts');
 const result=util.normalizeServerHistories({servers:{2:{power:[{collected_at:'2026-11-12T10:00:00Z',value:0},{collected_at:'2026-11-12T10:01:00Z',value:null},{collected_at:'invalid',value:900}],network_rx:[{collected_at:'2026-11-12T10:00:00Z',value:1024}],psu:{PSU1:[{collected_at:'2026-11-12T10:00:00Z',value:16}]}}}});
 assert.deepEqual(result[2].power.map(point=>point.value),[0,null]);
 assert.equal(result[2].network_rx[0].value,1024);
 assert.equal(result[2].psu.PSU1[0].value,16);
 assert.deepEqual(result[2].gpu_temperature,[]);
});
