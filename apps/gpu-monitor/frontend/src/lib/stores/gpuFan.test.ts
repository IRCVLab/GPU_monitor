// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeServerState, isServerStateEqual } from './servers.ts';

const state = fan_percent => normalizeServerState({server_id:2,status:'online',gpus:[{index:0,fan_percent}]});
test('GPU fan percentage preserves stopped fans and does not invent unavailable readings',()=>{
 assert.equal(state(0).gpus[0].fan_percent,0);
 assert.equal(state(43).gpus[0].fan_percent,43);
 assert.equal(state(undefined).gpus[0].fan_percent,null);
 assert.equal(state(150).gpus[0].fan_percent,null);
});
test('fan-only updates reach the server store',()=>{
 assert.equal(isServerStateEqual(state(20),state(25)),false);
 assert.equal(isServerStateEqual(state(20),state(20)),true);
});
