// @ts-nocheck
import test from 'node:test';import assert from 'node:assert/strict';
import {LatestRequest,mergePeaks,clearPeaks} from './telemetryLoader.ts';
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const metric=(watts=500)=>({watts,amps:watts/220,complete:true,observed_at:'2026-11-13T02:00:00Z',status:'estimated',peak_watts:null,peak_amps:null,peak_watts_at:null,peak_amps_at:null});
const state=(m)=>({at:'2026-11-13T02:00:00Z',nodes:{a:m},edges:{},ports:{},history_status:'projection'});
test('overlapping current refreshes are deduplicated',async()=>{const loader=new LatestRequest(),gate=deferred(),values=[];let calls=0;const run=()=>loader.run('same',()=>{calls++;return gate.promise;},v=>values.push(v));const first=run(),second=run();gate.resolve(5);await Promise.all([first,second]);assert.equal(calls,1);assert.deepEqual(values,[5]);});
test('new context cancels and ignores older results even when transport ignores abort',async()=>{const loader=new LatestRequest(),old=deferred(),values=[];let oldSignal;const pending=loader.run('old',s=>{oldSignal=s;return old.promise;},v=>values.push(v));await loader.run('new',async()=>2,v=>values.push(v));assert.equal(oldSignal.aborted,true);old.resolve(1);await pending;assert.deepEqual(values,[2]);});
test('unmount cancellation prevents stale application',async()=>{const loader=new LatestRequest(),gate=deferred(),values=[];const pending=loader.run('one',()=>gate.promise,v=>values.push(v));loader.cancel();gate.resolve(1);await pending;assert.deepEqual(values,[]);});
test('background peaks preserve fresh readings and incorporate a newer complete observation',()=>{const current=state(metric(500)),history=state({...metric(100),peak_watts:450,peak_amps:3,peak_watts_at:'earlier-w',peak_amps_at:'earlier-a'});const merged=mergePeaks(current,history);assert.equal(merged.nodes.a.watts,500);assert.equal(merged.nodes.a.peak_watts,500);assert.equal(merged.nodes.a.peak_watts_at,current.nodes.a.observed_at);assert.equal(merged.nodes.a.peak_amps,3);assert.equal(merged.nodes.a.peak_amps_at,'earlier-a');assert.equal(current.nodes.a.peak_watts,null);});
test('new period clears only peak fields while keeping current values and status',()=>{const current=state({...metric(500),peak_watts:600,peak_amps:3});const next=clearPeaks(current);assert.equal(next.nodes.a.watts,500);assert.equal(next.nodes.a.status,'estimated');assert.equal(next.nodes.a.peak_watts,null);assert.equal(current.nodes.a.peak_watts,600);});
test('incomplete current cannot manufacture a sampled peak',()=>{const current=state({...metric(null),complete:false}),history=state({...metric(100),peak_watts:450,peak_amps:3,peak_watts_at:'earlier',peak_amps_at:'earlier'});assert.equal(mergePeaks(current,history).nodes.a.peak_watts,450);});
test('a slow replay retains a higher peak already observed during the same window, independently for W and A',()=>{
 const current=state({...metric(200),peak_watts:900,peak_watts_at:'2026-11-13T01:59:00Z',peak_amps:5,peak_amps_at:'2026-11-13T01:58:00Z'});
 const history=state({...metric(200),peak_watts:700,peak_watts_at:'2026-11-13T01:50:00Z',peak_amps:6,peak_amps_at:'2026-11-13T01:45:00Z'});
 const next=mergePeaks(current,history,1);assert.equal(next.nodes.a.peak_watts,900);assert.equal(next.nodes.a.peak_watts_at,current.nodes.a.peak_watts_at);assert.equal(next.nodes.a.peak_amps,6);
});
test('retained observations cannot carry peaks from outside the period or saved actual revision',()=>{
 const old=state({...metric(200),peak_watts:900,peak_watts_at:'2026-11-12T01:59:00Z'}),history=state({...metric(200),peak_watts:700,peak_watts_at:'2026-11-13T01:50:00Z'});
 assert.equal(mergePeaks(old,history,1).nodes.a.peak_watts,700);
 const actual={...old,effective_from:'2026-11-13T01:55:00Z',nodes:{a:{...old.nodes.a,peak_watts_at:'2026-11-13T01:54:00Z'}}};assert.equal(mergePeaks(actual,{...history,nodes:{a:{...history.nodes.a,peak_watts_at:'2026-11-13T01:57:00Z'}}},1).nodes.a.peak_watts,700);
});
test('a cached maximum expiring from its rolling window requires a fresh replay',async()=>{
 const {peakWindowExpired}=await import('./telemetryLoader.ts');
 const cached=state({...metric(500),peak_watts:900,peak_watts_at:'2026-11-13T01:00:01Z',peak_amps:5,peak_amps_at:'2026-11-13T01:59:00Z'});
 assert.equal(peakWindowExpired(cached,1,'2026-11-13T02:00:00Z'),false);assert.equal(peakWindowExpired(cached,1,'2026-11-13T02:00:02Z'),true);
 assert.equal(peakWindowExpired(state(metric(0)),1,'2026-11-13T02:00:00Z'),false);
});
test('history collected after the live snapshot keeps its newer transient maximum in the cache',()=>{
 const live=state(metric(500)),history={...state({...metric(200),peak_watts:900,peak_watts_at:'2026-11-13T02:00:01Z',peak_amps:5,peak_amps_at:'2026-11-13T02:00:01Z'}),at:'2026-11-13T02:00:02Z'};
 const cache=mergePeaks(live,history,1);assert.equal(cache.nodes.a.peak_watts,900);assert.equal(Date.parse(cache.at),Date.parse(history.at));
 const later={...state({...metric(200),observed_at:'2026-11-13T02:00:10Z'}),at:'2026-11-13T02:00:10Z'};assert.equal(mergePeaks(later,cache,1).nodes.a.peak_watts,900);
});
test('a freshness-valid current sample cannot reenter the window after a newer history anchor expires it',()=>{
 const current=state({...metric(900),observed_at:'2026-11-13T01:00:01Z'}),history={...state({...metric(700),peak_watts:700,peak_watts_at:'2026-11-13T01:50:00Z',peak_amps:4,peak_amps_at:'2026-11-13T01:50:00Z'}),at:'2026-11-13T02:00:02Z'};
 assert.equal(mergePeaks(current,history,1).nodes.a.peak_watts,700);
});
