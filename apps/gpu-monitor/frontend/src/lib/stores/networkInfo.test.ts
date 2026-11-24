// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeServerState,isServerStateEqual} from './servers.ts';
const state=(rx,tx)=>normalizeServerState({server_id:2,status:'online',gpus:[],system:{cpu_percent:0,ram_used:0,ram_total:1,network_rx_bytes_per_second:rx,network_tx_bytes_per_second:tx}});
test('network rates preserve idle zeroes and unavailable samples',()=>{
 assert.equal(state(0,1024).system.network_rx_bytes_per_second,0);
 assert.equal(state(0,1024).system.network_tx_bytes_per_second,1024);
 assert.equal(state(null,undefined).system.network_rx_bytes_per_second,null);
 assert.equal(state(-1,Infinity).system.network_rx_bytes_per_second,null);
 assert.equal(state(-1,Infinity).system.network_tx_bytes_per_second,null);
});
test('network-only changes reach realtime cards',()=>{
 assert.equal(isServerStateEqual(state(10,20),state(20,20)),false);
 assert.equal(isServerStateEqual(state(10,20),state(10,20)),true);
});
