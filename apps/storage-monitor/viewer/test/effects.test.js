import test from 'node:test';
import assert from 'node:assert/strict';
import {createStore} from '../store.js';
import {initialState,reducer} from '../state.js';
import {createRequests,bindEffects} from '../effects.js';
import {fetchResource} from '../api.js';
import {parseUrl} from '../router.js';
const pending = () => Promise.withResolvers();
test('new requests cancel only the same key and superseded successes and failures are ignored', async () => {
  const store = createStore(reducer,initialState());
  const calls = [];
  const request = createRequests(store,(key,signal) => {
    const task = pending();
    calls.push({key,signal,...task});
    return task.promise;
  });
  const first = request('detail/a'), other = request('detail/b'), fresh = request('detail/a');
  assert.deepEqual(calls.map(c => c.signal.aborted),[true,false,false]);
  calls[2].resolve({hostname:'a'});
  await fresh;
  calls[0].reject(new Error('old error'));
  calls[1].resolve({hostname:'b'});
  await Promise.all([first,other]);
  assert.equal(store.getState().resources['detail/a'].data.hostname,'a');
  assert.equal(store.getState().resources['detail/b'].data.hostname,'b');
});
test('only session 404 selects static data, preserving manifest order and sample fallbacks',async t => {
  const requests = [];
  const responses = [[404,{}],[200,[{id:'beta',sample_data:true},{id:'alpha',sample_data:true}]],
    [404,{}],[200,{hostname:'beta'}],[503,{}],[404,{}]];
  t.mock.method(globalThis,'fetch',async url => {
    requests.push(url);
    const [status,data] = responses.shift();
    return Response.json(data,{status});
  });
  const {signal} = new AbortController();
  const {source} = await fetchResource('session',signal);
  assert.equal(source,'static');
  const fleet = await fetchResource('fleet',signal,{source});
  assert.deepEqual(fleet.servers.map(row => row.id),['beta','alpha']);
  assert.equal(fleet.data_mode,'sample');
  assert.equal((await fetchResource('detail/beta',signal,{source,file:'beta'})).hostname,'beta');
  await assert.rejects(fetchResource('session',signal),{status:503});
  await assert.rejects(fetchResource('fleet',signal),{status:404});
  assert.deepEqual(requests,['/api/session','data/hosts.json','data/beta.json','data/beta.sample.json','/api/session','/api/servers']);
});

function runViewer(t,handle) {
  const timers = new Map();
  t.mock.method(globalThis,'setInterval',(fn,ms) => timers.set(ms,fn));
  globalThis.window = new EventTarget();
  globalThis.document = new EventTarget();
  const store = createStore(reducer,initialState(parseUrl('/?server=a')));
  const calls = [];
  const api = bindEffects(store,async (key,signal,init) => {
    calls.push(key);
    if (key === 'session') return {can_rescan:true,csrf_token:'token'};
    if (key === 'fleet') return {servers:[{id:'a',overview_snapshot:{}},{id:'b',overview_snapshot:{}}]};
    if (key.startsWith('activity/')) return {state:'idle'};
    return handle(key,init);
  });
  return {store,calls,...api,tick:() => timers.get(5000)(),job:() => store.getState().rescans.a};
}
test('embedded overview renders without detail downloads and detail errors stay local',async t => {
  const detail = pending(), viewer = runViewer(t,key => key === 'detail/a' ? detail.promise : {hostname:'b'});
  await viewer.refresh();
  assert.deepEqual(viewer.calls,['session','fleet','detail/a','activity/a']);
  assert.equal(viewer.store.getState().resources['detail/a'].status,'loading');
  viewer.store.dispatch({type:'Navigate',route:parseUrl('/?server=b')});
  await new Promise(setImmediate);
  detail.reject(new Error('temporarily unavailable'));
  await new Promise(setImmediate);
  const state = viewer.store.getState();
  assert.equal(state.route.serverId,'b');
  assert.equal(state.resources['detail/b'].data.hostname,'b');
  assert.equal(state.resources['detail/a'].status,'error');
});
test('rescan follows its job through a stale receipt and a transient error, keeps the button busy through refresh',async t => {
  const refreshed = pending();
  let details = 0;
  const jobs = [{id:'old',state:'failed'},new Error('disconnected'),{id:'new',state:'running'},{id:'new',state:'succeeded'}];
  const viewer = runViewer(t,(key,init) => {
    if (key === 'detail/a') return ++details === 1 ? {hostname:'a'} : refreshed.promise;
    if (key === 'rescan/a') {
      assert.equal(init.method,'POST');
      assert.equal(init.headers['X-CSRF-Token'],'token');
      return {job:{id:'new',state:'running'}};
    }
    const job = jobs.shift();
    if (job instanceof Error) throw job;
    return {job};
  });
  await viewer.refresh();
  await viewer.rescan();
  assert.equal(viewer.job().phase,'reconnecting');
  await viewer.tick();
  assert.equal(viewer.job().busy,true);
  await viewer.tick();
  assert.equal(viewer.job().phase,'running');
  const final = viewer.tick();
  await new Promise(setImmediate);
  assert.equal(viewer.job().phase,'refreshing');
  refreshed.resolve({hostname:'a'});
  await final;
  assert.equal(viewer.job().busy,false);
});
test('conflict without local job never polls a persisted receipt, cooldown and fast failure remain retryable',async t => {
  let count = 0;
  const viewer = runViewer(t,key => {
    if (key === 'detail/a') return {hostname:'a'};
    if (key === 'job/a') return {job:{id:'new',state:'failed',result_code:'FAILED'}};
    const failures = [{error:'ACTIVE_JOB',remote_active_state:'running'},{error:'COOLDOWN',retry_after_seconds:23},
      {error:'ACTIVE_JOB',job:{id:'new',state:'running'}}];
    throw Object.assign(new Error('request rejected'),{body:failures[count++]});
  });
  await viewer.refresh();
  await viewer.rescan();
  assert.equal(viewer.job().busy,false);
  assert.equal(viewer.calls.includes('job/a'),false);
  await viewer.rescan();
  assert.match(viewer.job().message,/23s/);
  await viewer.rescan();
  assert.match(viewer.job().message,/FAILED/);
  assert.equal(viewer.job().busy,false);
});
