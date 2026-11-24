// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { get } from 'svelte/store';
import * as hardware from './hardware.ts';
const snapshot=(time)=>({source:'ssh-ipmi',collected_at:time,bmc_ip:null,power_watts:100,sensors:[]});
const point=(time)=>({collected_at:time,power_watts:100,server_count:1,expected_count:1,complete:true});
test('hardware updates keep newer snapshots and append each completed round only once',()=>{
 hardware.hardwareSnapshots.set({});hardware.powerHistory.set([]);
 const later='2026-11-12T08:01:00Z',earlier='2026-11-12T08:00:00Z';
 hardware.applyHardwareUpdate({mode:'live',servers:{2:snapshot(later)},point:point(later)});
 hardware.applyHardwareUpdate({mode:'live',servers:{2:snapshot(earlier)},point:point(earlier)});
 hardware.applyHardwareUpdate({mode:'live',servers:{2:snapshot(later)},point:point(later)});
 assert.equal(get(hardware.hardwareSnapshots)['2'].collected_at,later);
 assert.equal(get(hardware.powerHistory).length,1);
});
test('malformed updates and impossible power points never enter the stores',()=>{
 hardware.hardwareSnapshots.set({});hardware.powerHistory.set([]);
 hardware.applyHardwareUpdate(null);
 hardware.applyHardwareUpdate({mode:'live',servers:{2:{power_watts:0}},point:{...point('bad'),power_watts:NaN}});
 assert.deepEqual(get(hardware.hardwareSnapshots),{});
 assert.deepEqual(get(hardware.powerHistory),[]);
});
test('incomplete rounds cannot expose a partial sum as complete power',()=>{
 hardware.powerHistory.set([]);
 hardware.applyHardwareUpdate({mode:'live',servers:{},point:{...point('2026-11-12T08:00:00Z'),server_count:1,expected_count:2}});
 assert.equal(get(hardware.powerHistory)[0].power_watts,null);
 assert.equal(get(hardware.powerHistory)[0].complete,false);
});
