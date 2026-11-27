import {rawSegment} from './api.js';
import {validateCleanupSelection} from './removal.js';

export function initialState(route = {page:'overview'}) {
  return {route, resources:{}, selection:Object.create(null), rescans:{}, ui:{expandedUsers:{}, sorts:{}, selectionBarExpanded:false, theme:'light', selectMode:false, modifier:false}};
}
export const serverSelection = (state,id) => Object.hasOwn(state.selection,id) ? state.selection[id] : [];
export const detailKey = id => 'detail/' + id;
export const mountId = mount => mount.volume_id ?? mount.mount_id ?? mount.path;
export function selectedMount(detail, route) {
  const mounts = detail?.mounts ?? [];
  const id = route.volumeId ?? mountId(mounts[0] ?? {});
  const roots = mounts.filter(m => mountId(m) === id);
  if (roots.length <= 1) return roots[0];
  const tree = {kind:'directory',name:'/',children:[],bytes:0};
  for (const root of roots) {
    if (!root.tree) continue;
    let parent = tree;
    const parts = root.path.split('/').filter(Boolean);
    for (const [index,name] of parts.entries()) {
      parent.bytes += root.tree.bytes;
      if (index === parts.length-1) parent.children.push({...root.tree,name});
      else {
        let child = parent.children.find(n => n.name === name);
        if (!child) parent.children.push(child = {kind:'directory',name,bytes:0,children:[]});
        parent = child;
      }
    }
  }
  return {volume_id:id, path:'/', tree, scanned_bytes:tree.bytes};
}
export function drillStack(detail, route) {
  const mount = selectedMount(detail, route);
  if (!mount?.tree) return [];
  const stack = [{node:mount.tree, name:mount.path}];
  for (const segment of route.path) {
    const node = stack.at(-1).node.children?.find(c => rawSegment(c) === segment);
    if (!node) break;
    stack.push({node, name:node.name});
  }
  return stack;
}
function selectionEntry(detail,item) {
  const checked = validateCleanupSelection(detail,item);
  if (!checked.accepted) return null;
  const root = checked.selectedRoot;
  const volumeId = item.volume_id ?? root.volume_id;
  const binding = item.binding ?? root.binding;
  if (!volumeId || !binding) return null;
  return {...item, volumeId, binding, rawPath:item.raw_path ?? item.path.split('/').slice(1).map(name => rawSegment({name})), flag:null};
}
function lookup(detail, entry) {
  const candidates = [...(detail.top_files ?? []), ...(detail.stale ?? [])].filter(row => row.path === entry.path);
  for (const mount of detail.mounts ?? []) {
    if (!mount.tree || !entry.path.startsWith(mount.path.replace(/\/$/,'') + '/')) continue;
    let node = mount.tree;
    for (const segment of entry.path.slice(mount.path.length).split('/').filter(Boolean))
      node = node?.children?.find(c => rawSegment(c) === rawSegment({name:segment}));
    if (node) candidates.push({...node, path:entry.path, volume_id:mount.volume_id, binding:mount.binding});
  }
  const matches = candidates.map(row => selectionEntry(detail,row)).filter(row =>
    row && row.rawPath.length === entry.rawPath.length && row.rawPath.every((s,i) => s === entry.rawPath[i]));
  const same = matches.find(row => row.volumeId === entry.volumeId && row.binding === entry.binding && row.kind === entry.kind);
  if (same && !entry.flag) return entry.bytes === same.bytes ? entry : {...entry, bytes:same.bytes};
  if (same) return {...entry, flag:'changed', replacement:same};
  return {...entry, flag:matches.length ? 'changed' : 'missing', replacement:matches[0] ?? null};
}
export function reducer(state, event) {
  const {type,key,requestId,data} = event;
  const remote = state.resources[key];
  const id = state.route.serverId, entries = serverSelection(state,id);
  const selection = next => ({...state,selection:{...state.selection,[id]:next}});
  switch (type) {
    case 'Navigate': case 'PopState': return {...state,route:event.route};
    case 'DataRequested': return {...state,resources:{...state.resources,[key]:{...remote,status:'loading',requestId}}};
    case 'DataFailed': case 'DataLoaded': {
      if (remote?.status !== 'loading' || remote.requestId !== requestId) return state;
      const value = type === 'DataLoaded' ? {status:'ready',data,received:event.received} :
        {status:'error',code:event.code,problem:event.problem,data:event.code === 'UNKNOWN_SERVER' ? undefined : remote.data};
      const next = {...state,resources:{...state.resources,[key]:value}};
      if (type === 'DataLoaded' && key === 'fleet') {
        const configured = new Set(data.servers.map(row => row.server_id));
        for (const name of Object.keys(next.resources)) {
          const [,server] = name.split('/');
          if (server && !name.startsWith("capture/") && !configured.has(server)) delete next.resources[name];
        }
      }
      if (type === 'DataLoaded' && (key.startsWith('capture/') || key.startsWith('detail/'))) {
        for (const [name,remote] of Object.entries(next.resources)) {
          if (!name.startsWith('detail/') || !remote.data) continue;
          const server = name.slice(7);
          let detail = remote.data;
          if (detail.volumes) {
            if (key !== name && !detail.volumes.some(v => 'capture/'+v.capture?.sha256 === key)) continue;
            const mounts = detail.mounts.map(m => {
              const volume = detail.volumes.find(v => v.volume_id === m.volume_id);
              const capture = next.resources['capture/'+volume.capture?.sha256]?.data;
              const root = capture?.roots.find(r => r.binding === m.binding);
              return root ? {...m, tree:root.tree, scanned_bytes:root.counts.bytes} : m;
            });
            detail = {...detail,mounts};
            next.resources[name] = {...remote,data:detail};
          } else if (name !== key) continue;
          const selected = serverSelection(state,server);
          const updated = selected.map(entry => {
            const volume = detail.volumes?.find(v => v.volume_id === entry.volumeId);
            const previous = entry.pending ? {...entry,flag:entry.priorFlag,pending:false} : entry;
            const match = lookup(detail,previous);
            if (match.flag === 'missing' && volume?.capture && !next.resources['capture/'+volume.capture.sha256]?.data)
              return {...entry, flag:'missing', pending:true, priorFlag:previous.flag};
            return match;
          });
          if (updated.some((entry,i) => entry !== selected[i])) next.selection = {...next.selection,[server]:updated};
        }
      }
      if (key === 'fleet' || key.startsWith('detail/')) {
        const captures = new Set();
        for (const [name,resource] of Object.entries(next.resources)) {
          if (!name.startsWith('detail/')) continue;
          for (const volume of resource.data?.volumes ?? []) {
            if (volume.capture) captures.add('capture/' + volume.capture.sha256);
          }
        }
        for (const name of Object.keys(next.resources)) {
          if (name.startsWith('capture/') && !captures.has(name)) delete next.resources[name];
        }
      }
      return next;
    }
    case 'RescanObserved':
      return {...state,rescans:{...state.rescans,[event.serverId]:event.job}};
    case 'Select': {
      const entry = selectionEntry(state.resources[detailKey(id)]?.data,event.item);
      return !id || !entry || entries.some(e => e.path === entry.path) ? state : selection([...entries,entry]);
    }
    case 'Unselect': return selection(entries.filter(e => e.path !== event.path));
    case 'ClearSelection': return {...selection([]),ui:{...state.ui,selectionBarExpanded:false}};
    case 'ConfirmFlagged': return selection(entries.map(e => e.path === event.path && e.flag === 'changed' ? e.replacement : e));
    case 'ToggleUserRow':
      return {...state, ui:{...state.ui,expandedUsers:{...state.ui.expandedUsers,[id]:state.ui.expandedUsers[id] === event.uid ? null : event.uid}}};
    case 'Sort': {
      const tab = state.route.tab, before = state.ui.sorts[tab] ?? {key:'bytes',dir:-1};
      const sort = {key:event.key,dir:before.key === event.key ? -before.dir : ['name','path','owner','mount'].includes(event.key) ? 1 : -1};
      return {...state,ui:{...state.ui,sorts:{...state.ui.sorts,[tab]:sort}}};
    }
    case 'ToggleSelectionBar': return {...state,ui:{...state.ui,selectionBarExpanded:!state.ui.selectionBarExpanded}};
    case 'Theme': return {...state,ui:{...state.ui,theme:event.theme}};
    case 'Modifier': return event.enabled === state.ui.modifier ? state : {...state,ui:{...state.ui,modifier:event.enabled}};
    case 'ToggleSelectMode': return {...state,ui:{...state.ui,selectMode:!state.ui.selectMode}};
    default: throw new Error('Unknown viewer event ' + type);
  }
}
