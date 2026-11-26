import {fetchResource} from './api.js';
import {monotonicSeconds} from './format.js';
import {detailKey} from './state.js';
import {pendingScanGeneration,readyScanGeneration} from './views/scan-status.js';

export function createRequests(store, fetcher = fetchResource) {
  const pending = new Map();
  let sequence = 0;
  return async function request(key, options) {
    pending.get(key)?.abort();
    const controller = new AbortController();
    pending.set(key,controller);
    const requestId = ++sequence;
    store.dispatch({type:'DataRequested',key,requestId});
    try {
      const data = await fetcher(key,controller.signal,options);
      if (controller.signal.aborted) return;
      store.dispatch({type:'DataLoaded',key,requestId,data,received:monotonicSeconds()});
      return data;
    } catch (error) {
      if (!controller.signal.aborted)
        store.dispatch({type:'DataFailed',key,requestId,code:error.body?.error ?? error.message,problem:error.body});
    } finally {
      if (pending.get(key) === controller) pending.delete(key);
    }
  };
}

export function bindEffects(store, fetcher = fetchResource) {
  const request = createRequests(store,fetcher);
  const state = store.getState;
  const resource = key => state().resources[key];
  function detail(id, force) {
    const key = detailKey(id);
    if (resource(key)?.status !== 'loading' && (force || !resource(key)?.data)) {
      const source = resource('session')?.data?.source;
      const file = resource('fleet')?.data?.servers.find(row => row.id === id)?.file;
      return request(key,{source,file});
    }
  }
  const tracking = id => state().rescans[id];
  const observe = (serverId,job) => store.dispatch({type:'RescanObserved',serverId,job});
  function openServer(id) {
    if (!id || !resource('fleet')?.data?.servers.some(row => row.id === id)) return;
    detail(id,false);
    if (resource('session')?.data?.source !== 'static') request('activity/' + id);
  }
  async function refresh(force = false) {
    if (!resource('session')?.data && !await request('session')) return;
    const fleet = await request('fleet',{source:resource('session')?.data?.source});
    if (!fleet) return;
    const current = state().route.serverId;
    for (const row of fleet.servers) {
      const cached = resource(detailKey(row.id))?.data;
      if (!Object.hasOwn(row,'overview_snapshot') || current === row.id) {
        const changed = row.overview_snapshot && row.overview_snapshot.scan_generation !== cached?.scan_generation;
        detail(row.id,current === row.id && (force || changed));
      }
    }
    if (current) openServer(current);
  }
  async function rescan() {
    const id = state().route.serverId, session = resource('session')?.data;
    if (!id || !session?.can_rescan || tracking(id)?.busy) return;
    observe(id,{phase:'starting',busy:true});
    const key = 'rescan/'+id;
    const response = await request(key,{method:'POST',headers:{'X-CSRF-Token':session.csrf_token,'Content-Type':'application/json'},body:'{}'});
    const error = resource(key);
    const job = response?.job ?? error?.problem?.job;
    if (!response && !(error?.code === 'ACTIVE_JOB' && job)) {
      const delay = error?.problem?.retry_after_seconds;
      const message = delay ? 'Rescan is cooling down. Try again in ' + delay + 's.' :
        error?.code === 'ACTIVE_JOB' ? 'A remote scan is already running.' : 'Rescan request failed: ' + (error?.code ?? 'unavailable');
      observe(id,{phase:'idle',busy:false,message});
      return;
    }
    observe(id,{phase:'running',busy:true,expectedId:job?.id,failures:0});
    if (state().route.serverId === id) await pollJob(id);
  }
  async function pollJob(id) {
    const tracked = tracking(id);
    if (!tracked?.busy || !['running','reconnecting'].includes(tracked.phase) || resource('job/'+id)?.status === 'loading') return;
    const response = await request('job/'+id);
    if (tracking(id) !== tracked) return;
    const job = response?.job;
    if (!response || (tracked.expectedId && job?.id !== tracked.expectedId)) {
      const failures = tracked.failures + 1;
      observe(id,{...tracked,failures,busy:failures <= 2,phase:failures <= 2 ? 'reconnecting' : 'idle',
        message:failures <= 2 ? 'Waiting for the new scan status.' : 'Scan status is temporarily unavailable. Try again.'});
    } else if (job?.state === 'running') {
      observe(id,{...tracked,phase:'running',failures:0});
    } else if (job?.state === 'failed') {
      observe(id,{phase:'idle',busy:false,message:'Rescan failed: ' + (job.result_code || 'UNKNOWN_FAILURE')});
    } else {
      observe(id,{phase:'refreshing',busy:true});
      await request(detailKey(id));
      observe(id,{phase:'idle',busy:false});
    }
  }
  async function pollActivity(id) {
    const activityKey = 'activity/'+id;
    if (resource(activityKey)?.status !== 'loading') {
      const activity = await request(activityKey);
      if (state().route.serverId !== id) return;
      const cached = resource(detailKey(id))?.data;
      if (activity && cached && readyScanGeneration(activity,cached)) detail(id,true);
    }
  }
  store.subscribe((next,previous) => {
    if (next.route.serverId !== previous.route.serverId) openServer(next.route.serverId);
  });
  window.addEventListener('focus',() => refresh(true));
  document.addEventListener('visibilitychange',() => { if (!document.hidden) refresh(true); });
  setInterval(() => { if (!document.hidden) refresh(); },60000);
  setInterval(() => {
    const id = state().route.serverId;
    if (!id || document.hidden) return;
    const status = resource('activity/'+id)?.data ?? {};
    const detail = resource(detailKey(id))?.data;
    if (status.state === 'running' || tracking(id)?.busy || pendingScanGeneration(status,detail))
      return Promise.all([pollActivity(id),pollJob(id)]);
  },5000);
  return {refresh,rescan};
}
