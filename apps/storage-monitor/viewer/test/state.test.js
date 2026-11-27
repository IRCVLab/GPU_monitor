import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState, reducer} from '../state.js';
import {parseUrl} from '../router.js';
import {removal} from '../removal.js';

const route = parseUrl('/?server=hinton&tab=stale&mount=/data&owner=1&path=proj');
const detail = {
  host:{hostname:'hinton',mount_table:{status:'ok',within_roots:[]}}, revision:'old',
  selected_roots:[{volume_id:'v', binding:'before', scan_root:'/data', status:'complete'}],
  top_files:[{kind:'file',path:'/data/a',bytes:100}], mounts:[]
};
function loaded(state,data,requestId) {
  const key = 'detail/hinton';
  return reducer(reducer(state,{type:'DataRequested',key,requestId}),{type:'DataLoaded',key,requestId,data});
}
test('V1 refresh keeps drill, filters and picks and blocks stale commands until review', () => {
  let state = loaded(initialState(route), detail, 1);
  state = reducer(state,{type:'Select',item:detail.top_files[0]});
  const entry = state.selection.hinton[0];
  assert.equal(entry.binding,'before');
  assert.equal(state.ui.selectionBarExpanded,false);
  for (const [patch,flag] of [
    [{top_files:[{...detail.top_files[0],bytes:40}]},null],
    [{top_files:[]},'missing'],
    [{top_files:[{...detail.top_files[0],kind:'directory'}]},'changed'],
    [{selected_roots:[{...detail.selected_roots[0],binding:'after'}]},'changed']
  ]) {
    const data = {...detail,...patch,revision:'new'};
    const next = loaded(state,data,2);
    assert.deepEqual(next.route,route);
    assert.deepEqual(next.ui,state.ui);
    assert.equal(next.selection.hinton[0].flag,flag);
    if (flag) assert.equal(removal(data,next.selection.hinton).command,'');
    else assert.equal(next.selection.hinton[0].bytes,40);
  }
});

const actions = [
  {type:'Navigate',route}, {type:'PopState',route:{page:'overview'}},
  {type:'DataRequested',key:'detail/hinton',requestId:1},
  {type:'DataLoaded',key:'detail/hinton',requestId:1,data:detail},
  {type:'DataFailed',key:'detail/hinton',requestId:1,code:'UNKNOWN_SERVER'},
  {type:'Select',item:detail.top_files[0]}, {type:'Unselect',path:'/data/a'},
  {type:'ConfirmFlagged',path:'/data/a'}, {type:'ClearSelection'}, {type:'ToggleSelectionBar'},
  {type:'ToggleUserRow',uid:1},{type:'Sort',key:'files'}, {type:'Theme',theme:'dark'}, {type:'ToggleSelectMode'},
  {type:'Modifier',enabled:false}, {type:'RescanObserved',serverId:'hinton',job:{phase:'running',busy:true}}
];
function checkTransition(before,event) {
  const after = reducer(before,event);
  if (event.type !== 'Navigate' && event.type !== 'PopState') assert.equal(after.route,before.route);
  if (event.type.startsWith('Data')) assert.equal(after.ui,before.ui);
  for (const entries of Object.values(after.selection)) {
    for (const entry of entries) assert.ok(entry.volumeId && entry.binding && entry.rawPath.length);
    if (entries.some(e => e.flag)) assert.equal(removal(detail,entries).command,'');
  }
  return after;
}
test('viewer transition table is total across every remote state and page', () => {
  const ready = loaded(initialState(route),detail,1);
  for (const route of [{page:'overview'},{page:'statistics'},ready.route]) {
    for (const remote of [undefined,{status:'loading',requestId:1},{status:'error',code:'UNKNOWN_SERVER'},{status:'ready',data:detail}]) {
      const before = {...ready,route,resources:{'detail/hinton':remote}};
      for (const action of actions) checkTransition(before,action);
    }
  }
});
test('10,000 seeded random viewer event sequences preserve invariants', () => {
  let seed = 37;
  for (let i=0;i<10000;i++) {
    let state = loaded(initialState(route),detail,1);
    for (let step=0;step<20;step++) {
      seed = (Math.imul(1664525,seed) + 1013904223) >>> 0;
      state = checkTransition(state,actions[seed % actions.length]);
    }
  }
});
test('removed inventory rejects late detail and failed refresh retains cached data', () => {
  const ready = loaded(initialState(route),detail,1);
  let loading = reducer(ready,{type:'DataRequested',key:'detail/hinton',requestId:2});
  const failed = reducer(loading,{type:'DataFailed',key:'detail/hinton',requestId:2,code:'STORE_ERROR'});
  assert.equal(failed.resources['detail/hinton'].data,detail);
  loading = reducer(loading,{type:'DataRequested',key:'fleet',requestId:3});
  const removed = reducer(loading,{type:'DataLoaded',key:'fleet',requestId:3,data:{servers:[]}});
  assert.equal(reducer(removed,{type:'DataLoaded',key:'detail/hinton',requestId:2,data:detail}),removed);
});

test('flagged picks require explicit review even if the old head returns; removing and confirming are per-server', () => {
  let state = loaded(initialState(route),detail,1);
  state = reducer(state,{type:'Select',item:detail.top_files[0]});
  state = loaded(state,{...detail,top_files:[]},2);
  state = loaded(state,detail,3);
  assert.equal(removal(detail,state.selection.hinton).command,'');
  state = reducer(state,{type:'ConfirmFlagged',path:'/data/a'});
  assert.ok(removal(detail,state.selection.hinton).command);
  state = reducer(state,{type:'Navigate',route:parseUrl('/?server=b')});
  state = reducer(state,{type:'ClearSelection'});
  assert.equal(state.selection.hinton.length,1);
  state = reducer(state,{type:'PopState',route});
  state = reducer(state,{type:'Unselect',path:'/data/a'});
  assert.equal(state.selection.hinton.length,0);
});

test('a treemap pick reconciles against the next capture by volume, binding, kind and raw path', () => {
  const volumeId = 'v-0000000000000001', binding = 'b-0000000000000001';
  const root = {volume_id:volumeId,binding,scan_root:'/data',path:'/data',status:'complete'};
  const data = sha256 => ({host:{hostname:'hinton',mount_table:{status:'ok',within_roots:[]}},
    volumes:[{volume_id:volumeId,capture:{sha256}}],mounts:[root],selected_roots:[root],top_files:[],stale:[]});
  const captured = (state,sha,kind,bytes) => {
    const key = 'capture/'+sha, requestId=3;
    const data = {roots:[{binding,counts:{bytes},tree:{kind:'directory',children:[{name:'a',kind,bytes}]}}]};
    return reducer(reducer(state,{type:'DataRequested',key,requestId}),{type:'DataLoaded',key,requestId,data});
  };
  let state = captured(loaded(initialState(route),data('old'),1),'old','file',10);
  state = reducer(state,{type:'Select',item:{volume_id:volumeId,binding,path:'/data/a',kind:'file',bytes:10}});
  state = loaded(state,data('new'),2);
  assert.equal(removal(data('new'),state.selection.hinton).command,'');
  const replaced = captured(state,'new','file',20);
  assert.equal(replaced.selection.hinton[0].bytes,20);
  assert.equal(replaced.selection.hinton[0].flag,null);
  assert.equal(captured(state,'new','directory',20).selection.hinton[0].flag,'changed');
});
