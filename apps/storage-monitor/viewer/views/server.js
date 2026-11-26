import {PALETTE,OTHER_COLOR,MOUNT_PALETTE,monotonicSeconds} from '../format.js';
import {selectedMount,detailKey} from '../state.js';

import {renderHeader} from './header.js';
import {drawTreemap} from './treemap.js';
import {drawUsers} from './users.js';
import {renderFiles} from './files.js';
import {renderScanStatus} from './scan-status.js';
import {renderNotice} from './notices.js';
import {renderSelectionBar} from './selection-bar.js';

const el = id => document.getElementById(id);
const empty = [];
export function renderServer(state,previous,{navigate,dispatch,chart,resize}) {
  const {route,ui,resources} = state;
  const id = route.serverId, server = route.page === 'server';
  const active = resources[detailKey(id)];
  const detail = active?.data;
  const ready = server && !!detail;
  for (const key of ['detailHead','detailActions','detailTabs','detailPanels','caps','warnBanner']) el(key).hidden = !ready;
  const unknown = server && resources.fleet?.data && !resources.fleet.data.servers.some(row => row.server_id === id);
  const error = unknown || server && !detail && active?.status === 'error';
  el('detailError').hidden = !error;
  if (error) el('detailError').textContent = unknown || active?.code === 'UNKNOWN_SERVER' ? 'Unknown server: ' + id : active.code;
  el('detailLoading').hidden = !server || ready || error;
  if (!el('detailLoading').hidden) el('detailLoading').textContent = '불러오는 중…';
  const entries = Object.hasOwn(state.selection,id) ? state.selection[id] : empty;
  if (!previous || previous.route.serverId !== id || previous.resources[detailKey(id)]?.data !== detail ||
      previous.selection[id] !== state.selection[id] || previous.ui.selectionBarExpanded !== ui.selectionBarExpanded)
    renderSelectionBar(detail,entries,ui.selectionBarExpanded,dispatch);
  if (!ready) { el('rescanNotice').hidden = true; return; }
  const activity = resources['activity/'+id];
  const summary = resources.fleet?.data?.servers.find(row => row.server_id === id);
  const facts = activity?.data ?? detail;
  const status = {...facts.activity, estimate_duration_sec:facts.eta?.typical_sec,
    estimate_max_duration_sec:facts.eta?.longest_sec};
  renderScanStatus(status,detail,{
    now:status.host_now_unix == null ? undefined : status.host_now_unix + (activity?.received ? monotonicSeconds()-activity.received : 0)});
  const rescan = state.rescans[id];
  const phase = rescan?.busy ? rescan.phase : status.state === 'running' ? 'running' : 'idle';
  const btn = el('rescanBtn');
  btn.disabled = !resources.session?.data?.can_rescan || phase !== 'idle';
  btn.setAttribute('aria-busy',String(phase !== 'idle'));
  btn.dataset.rescanState = phase;
  btn.title = btn.disabled && phase === 'idle' ? 'Read-only session: manual rescan is disabled.' : 'Run a fresh scan now';
  const message = rescan?.message ?? active.code;
  renderNotice(el('rescanNotice'),message);
  const dataChanged = previous?.resources[detailKey(id)]?.data !== detail;
  const routeChanged = previous?.route !== route;
  if (dataChanged || routeChanged || previous?.resources['activity/'+id]?.data?.capacity !== status.capacity)
    renderHeader(detail,selectedMount(detail,route),navigate);
  if (!(resize || dataChanged || routeChanged || previous?.ui !== ui || previous?.selection[id] !== state.selection[id])) return;
  const tab = route.tab === 'top' ? 'topfiles' : route.tab;
  document.querySelectorAll('.tab').forEach(button => {
    button.classList.toggle('active',button.dataset.tab === route.tab);
    button.setAttribute('aria-selected',String(button.dataset.tab === route.tab));
  });
  document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active',panel.id === 'panel-'+tab));
  const mountPaths = detail.volumes.map(v => v.volume_id);
  const uidColorMap = new Map([...detail.users].sort((a,b) => b.bytes-a.bytes).map((u,i) => [u.uid,PALETTE[i] ?? OTHER_COLOR]));
  const context = {DATA:detail, route, ui, entries, navigate, mountPaths, uidColorMap,
    toggleUserRow:uid => dispatch({type:'ToggleUserRow',uid}),
    toggleCleanupSelectedItem:item => dispatch(entries.some(e => e.path === item.path) ? {type:'Unselect',path:item.path} : {type:'Select',item}),
    mountColor:Object.fromEntries(mountPaths.map((path,i) => [path,MOUNT_PALETTE[i % MOUNT_PALETTE.length]])),
    ownerByUid:new Map(detail.users.map(u => [u.uid,u.name])),colorForUid:uid => uidColorMap.get(uid) ?? OTHER_COLOR,
    sort:ui.sorts[route.tab] ?? {key:'bytes',dir:-1}};
  if (route.tab === 'treemap') drawTreemap(context);
  else if (route.tab === 'users') { if (chart) drawUsers(context,chart); }
  else renderFiles(context);
}
