import {fetchResource} from './api.js';
import {monotonicSeconds} from './format.js';
import {detailKey} from './state.js';


export function createRequests(store, fetcher = fetchResource) {
  const pending = new Map();
  let sequence = 0;
  return async function request(key, options, resourceKey = key) {
    pending.get(key)?.abort();
    const controller = new AbortController();
    pending.set(key,controller);
    const requestId = ++sequence;
    store.dispatch({type:'DataRequested',key,requestId});
    try {
      const data = await fetcher(resourceKey,controller.signal,options);
      if (controller.signal.aborted) return;
      store.dispatch({type:'DataLoaded',key,requestId,data,received:monotonicSeconds()});
      return data;
    } catch (error) {
      if (!controller.signal.aborted)
        store.dispatch({type:'DataFailed',key,requestId,code:error.body?.error?.code ?? error.message,problem:error.body?.error});
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
      return request(key);
    }
  }
  const tracking = id => state().rescans[id];
  const observe = (serverId,job) => store.dispatch({type:'RescanObserved',serverId,job});
  function openServer(id, force = false) {
    const row = resource('fleet')?.data?.servers.find(row => row.server_id === id);
    if (!row) return;
    detail(id,force || row.revision !== resource(detailKey(id))?.data?.revision);
  }
  async function refresh(force = false) {
    if (!resource('session')?.data && !await request('session')) return;
    if (await request('fleet')) openServer(state().route.serverId,force);
  }
  function loadCaptures() {
    const {route,selection} = state();
    if (!route.serverId) return;
    const data = resource(detailKey(route.serverId))?.data;
    if (!data?.volumes) return;
    const wanted = new Set((selection[route.serverId] ?? []).map(entry => entry.volumeId));
    if (route.tab === 'treemap') wanted.add(route.volumeId ?? data.volumes[0]?.volume_id);
    for (const volume of data.volumes) {
      const sha = volume.capture?.sha256;
      if (sha && wanted.has(volume.volume_id) && !resource('capture/'+sha)) {
        request('capture/'+sha,{volumeId:volume.volume_id,sha},'capture/'+route.serverId);
      }
    }
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
      const delay = error?.problem?.retry_after_sec;
      const message = delay ? 'Rescan is cooling down. Try again in ' + delay + 's.' :
        error?.code === 'ACTIVE_JOB' ? 'A remote scan is already running.' : 'Rescan request failed: ' + (error?.code ?? 'unavailable');
      observe(id,{phase:'idle',busy:false,message});
      return;
    }
    observe(id,{phase:'running',busy:true,expectedId:job?.request_id,failures:0});
    if (state().route.serverId === id) await pollJob(id);
  }
  async function pollJob(id) {
    const tracked = tracking(id);
    if (!tracked?.busy || !['running','reconnecting'].includes(tracked.phase) || resource('activity/'+id)?.status === 'loading') return;
    const response = await request('activity/'+id);
    if (tracking(id) !== tracked) return;
    const job = response?.job ?? response?.last_job;
    if (!response || (tracked.expectedId && job?.request_id !== tracked.expectedId)) {
      const failures = tracked.failures + 1;
      observe(id,{...tracked,failures,busy:failures <= 2,phase:failures <= 2 ? 'reconnecting' : 'idle',
        message:failures <= 2 ? 'Waiting for the new scan status.' : 'Scan status is temporarily unavailable. Try again.'});
    } else if (response.job) {
      observe(id,{...tracked,phase:'running',failures:0});
    } else if (job?.state !== 'succeeded') {
      observe(id,{phase:'idle',busy:false,message:'Rescan failed: ' + (job.result_code || 'UNKNOWN_FAILURE')});
    } else {
      observe(id,{phase:'refreshing',busy:true});
      await request(detailKey(id));
      observe(id,{phase:'idle',busy:false});
    }
  }
  store.subscribe((next,previous,event) => {
    if (next.route.serverId !== previous.route.serverId) openServer(next.route.serverId);
    if (next.route !== previous.route || event.type === 'DataLoaded') loadCaptures();
  });
  window.addEventListener('focus',() => refresh(true));
  document.addEventListener('visibilitychange',() => { if (!document.hidden) refresh(true); });
  setInterval(() => { if (!document.hidden) refresh(); },60000);
  setInterval(() => {
    const id = state().route.serverId;
    if (!id || document.hidden) return;
    if (tracking(id)?.busy) return pollJob(id);
    const status = resource('activity/'+id)?.data ?? resource(detailKey(id))?.data;
    if ((status?.activity?.state === 'running' || status?.job) && resource('activity/'+id)?.status !== 'loading')
      return request('activity/'+id);
  },5000);
  return {refresh,rescan};
}
