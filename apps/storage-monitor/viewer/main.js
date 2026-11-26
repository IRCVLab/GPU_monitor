import {removal} from './removal.js';
import {createStore} from './store.js';
import {initialState,reducer,detailKey,drillStack,serverSelection} from './state.js';
import {bindRouter,parseUrl} from './router.js';
import {bindEffects} from './effects.js';
import {renderServer} from './views/server.js';
import {buildOverviewRows,renderOverviewList,buildFleetUserUsage,renderFleetUserUsage,layoutOverviewMasonry} from './views/overview.js';
import {bindTooltip} from './views/treemap.js';
import {bindTheme} from './views/theme.js';

const store = createStore(reducer,initialState(parseUrl(location.href)));
const navigate = bindRouter(store);
const effects = bindEffects(store);
const el = id => document.getElementById(id);
const patchRoute = patch => navigate({...store.getState().route,...patch});
let frame, previous, chartLoading, redraw = false;
function schedule(force = false) {
  redraw ||= force;
  if (!frame) frame = requestAnimationFrame(render);
}
function render() {
  frame = null;
  const state = store.getState(), {route,resources} = state;
  const server = route.page === 'server', stats = route.page === 'statistics';
  document.body.dataset.shellMode = server ? 'detail' : route.page;
  el('overviewView').hidden = route.page !== 'overview';
  el('statisticsView').hidden = !stats;
  el('statisticsBack').hidden = !stats;
  el('statisticsLink').hidden = stats;
  el('overviewBack').hidden = !server;
  el('detailView').hidden = !server;
  const fleet = resources.fleet?.data;
  const list = el('overviewList');
  if (!server && fleet) {
    const entries = fleet.servers.map(({id}) => ({id,snapshot:resources[detailKey(id)]?.data,error:resources[detailKey(id)]?.code}));
    if (stats) renderFleetUserUsage(el('fleetUsage'),buildFleetUserUsage(fleet.servers,entries));
    else if (previous?.route !== route || previous?.resources !== resources) renderOverviewList(list,buildOverviewRows(fleet.servers,entries));
    if (redraw && !stats) layoutOverviewMasonry(list);
    el(stats ? 'statisticsSampleDataMarker' : 'sampleDataMarker').hidden = fleet.data_mode !== 'sample';
  }
  const bootError = resources.session?.code || resources.fleet?.code;
  el('bootError').hidden = !bootError;
  if (server && route.tab === 'users' && !window.echarts && !chartLoading) {
    const script = document.createElement('script');
    script.src = 'echarts.min.js';
    chartLoading = true;
    script.onload = () => schedule(true);
    script.onerror = () => {chartLoading = false; el('bootError').hidden = false;};
    document.head.append(script);
  }
  renderServer(state,previous,{navigate:patchRoute,dispatch:store.dispatch,chart:window.echarts,resize:redraw});
  previous = state;
  redraw = false;
}
store.subscribe((state,old,event) => {
  if (event.type === 'DataLoaded' && event.key === detailKey(state.route.serverId) && state.route.path?.length) {
    const count = Math.max(0,drillStack(event.data,state.route).length-1);
    if (count < state.route.path.length) navigate({...state.route,path:state.route.path.slice(0,count)},true);
  }
  schedule();
});
bindTheme(theme => store.dispatch({type:'Theme',theme}));
store.dispatch({type:'Theme',theme:document.documentElement.classList.contains('dark') ? 'dark' : 'light'});
bindTooltip();
el('overviewBack').onclick = () => navigate({page:'overview'});
el('bootRetry').onclick = () => {effects.refresh(true); schedule(true);};
el('rescanBtn').onclick = effects.rescan;
el('cleanupToggle').onclick = () => store.dispatch({type:'ToggleSelectionBar'});
el('cleanupClear').onclick = () => store.dispatch({type:'ClearSelection'});
el('treemapCleanupMode').onclick = () => store.dispatch({type:'ToggleSelectMode'});
for (const name of ['keydown','keyup']) document.addEventListener(name,e => {
  if (e.key === 'Control' || e.key === 'Meta') store.dispatch({type:'Modifier',enabled:e.ctrlKey || e.metaKey});
});
window.addEventListener('blur',() => store.dispatch({type:'Modifier',enabled:false}));
for (const key of ['ownerFilter','staleOwnerFilter']) el(key).onchange = e => patchRoute({owner:e.target.value === '' ? null : Number(e.target.value)});
document.addEventListener('click', async event => {
  const target = event.target.closest('button,th.sortable');
  if (!target) return;
  const state = store.getState(), {route} = state;
  if (target.dataset.tab) patchRoute({tab:target.dataset.tab});
  else if (target.dataset.key) store.dispatch({type:'Sort',key:target.dataset.key});
  else if (target.dataset.cleanupPath && target.classList.contains('cleanup-select-btn')) {
    const path = target.dataset.cleanupPath;
    const data = state.resources[detailKey(route.serverId)].data;
    const item = (route.tab === 'stale' ? data.stale : data.top_files).find(row => row.path === path);
    store.dispatch(serverSelection(state,route.serverId).some(e => e.path === path) ? {type:'Unselect',path} : {type:'Select',item});
  } else if (target.dataset.copy || target.dataset.copyCommand) {
    const text = target.dataset.copy ?? removal(state.resources[detailKey(route.serverId)]?.data,serverSelection(state,route.serverId)).command;
    if (!text || target.dataset.copyCommand && text !== target.dataset.copyCommand) return;
    const label = target.dataset.label ||= target.textContent;
    try {
      if (!await writeClipboardText(text)) throw new Error('Clipboard unavailable');
      target.textContent = 'Copied';
    } catch { target.textContent = 'Copy failed'; }
    setTimeout(() => {target.textContent = label;},1200);
  }
});
window.addEventListener('resize',() => schedule(true));
schedule();
effects.refresh();

async function writeClipboardText(text) {
  if (!text) return false;
  if (typeof navigator !== "undefined" && navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return true;
  }
  if (typeof document !== "undefined" && document.execCommand) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
  return false;
}
