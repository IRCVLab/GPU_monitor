import test from 'node:test';
import assert from 'node:assert/strict';
import {initialState, reducer} from '../state.js';
import {parseUrl} from '../router.js';
import {removal} from '../removal.js';

const route = parseUrl('/?server=hinton&tab=stale&mount=/data&owner=1&path=proj');
const detail = {
  hostname:'hinton', scan_generation:'old',
  selected_roots:[{mount_id:'v', binding:'before', scan_root:'/data', status:'complete'}],
  top_files:[{kind:'file',path:'/data/a',bytes:100}], mounts:[]
};
function loaded(state, data, requestId) {
  const key = 'detail:hinton';
  state = reducer(state,{type:'DataRequested',key,requestId});
  return reducer(state,{type:'DataLoaded',key,requestId,data});
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
    const data = {...detail,...patch,scan_generation:'new'};
    const next = loaded(state,data,2);
    assert.deepEqual(next.route,route);
    assert.deepEqual(next.ui,state.ui);
    assert.equal(next.selection.hinton[0].flag,flag);
    if (flag) assert.equal(removal(data,next.selection.hinton).command,'');
    else assert.equal(next.selection.hinton[0].bytes,40);
  }
});
