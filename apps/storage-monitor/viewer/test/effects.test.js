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
test('session errors stay visible to the viewer',async t => {
  t.mock.method(globalThis,'fetch',async url => {
    assert.equal(url,'/api/v2/session');
    return Response.json({},{status:404});
  });
  await assert.rejects(fetchResource('session',new AbortController().signal),{status:404});
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
    if (key === 'fleet') return {servers:[{server_id:'a',revision:'x'},{server_id:'b',revision:'y'}]};
    return handle(key,init);
  });
  return {store,calls,...api,tick:() => timers.get(5000)(),job:() => store.getState().rescans.a};
}
test('embedded overview renders without detail downloads and detail errors stay local',async t => {
  const detail = pending(), viewer = runViewer(t,key => key === 'detail/a' ? detail.promise : {hostname:'b'});
  await viewer.refresh();
  assert.deepEqual(viewer.calls,['session','fleet','detail/a']);
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
  const jobs = [{request_id:'old',state:'failed'},new Error('disconnected'),{request_id:'new',state:'running'},{request_id:'new',state:'succeeded'}];
  const viewer = runViewer(t,(key,init) => {
    if (key === 'detail/a') return ++details === 1 ? {hostname:'a'} : refreshed.promise;
    if (key === 'rescan/a') {
      assert.equal(init.method,'POST');
      assert.equal(init.headers['X-CSRF-Token'],'token');
      return {job:{request_id:'new',state:'running'}};
    }
    const job = jobs.shift();
    if (job instanceof Error) throw job;
    return job.state === 'running' ? {job} : {last_job:job};
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
    if (key === 'activity/a') return {last_job:{request_id:'new',state:'failed',result_code:'FAILED'}};
    const failures = [{code:'ACTIVE_JOB',remote_active_state:'running'},{code:'COOLDOWN',retry_after_sec:23},
      {code:'ACTIVE_JOB',job:{request_id:'new',state:'running'}}];
    throw Object.assign(new Error('request rejected'),{body:{error:failures[count++]}});
  });
  await viewer.refresh();
  await viewer.rescan();
  assert.equal(viewer.job().busy,false);
  assert.equal(viewer.calls.includes('activity/a'),false);
  await viewer.rescan();
  assert.match(viewer.job().message,/23s/);
  await viewer.rescan();
  assert.match(viewer.job().message,/FAILED/);
  assert.equal(viewer.job().busy,false);
});

test('the active volume capture is shared by SHA across focus and tab changes', async t => {
  const volume = {volume_id:'v',capture:{sha256:'sha'}};
  const viewer = runViewer(t, key => {
    if (key === 'detail/a') return {revision:'x',volumes:[volume],mounts:[{volume_id:'v',binding:'b'}]};
    if (key === 'capture/a') return {roots:[{binding:'b',counts:{bytes:10},tree:{name:'a',kind:'directory'}}]};
    throw new Error('unexpected request '+key);
  });
  await viewer.refresh();
  await new Promise(setImmediate);
  await viewer.refresh(true);
  viewer.store.dispatch({type:'Navigate',route:parseUrl('/?server=a&tab=users')});
  viewer.store.dispatch({type:'Navigate',route:parseUrl('/?server=a&vol=v')});
  await new Promise(setImmediate);
  assert.equal(viewer.calls.filter(key => key === 'capture/a').length,1);
  assert.equal(viewer.store.getState().resources['detail/a'].data.mounts[0].tree.name,'a');
});
