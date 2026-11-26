import test from 'node:test';
import assert from 'node:assert/strict';
import {parseUrl, formatUrl} from '../router.js';

test('V6 opening a card uses its own tab, including bookmarked v1 links', () => {
  assert.equal(parseUrl('/?server=hinton#treemap').tab, 'treemap');
});
test('V8 history restores owner, storage and raw drill segments', () => {
  for (const name of ['한 글', '~b64~a_0', '100%?#', 'a+b']) {
    const route = {page:'server', serverId:'hinton', tab:'top', volumeId:'v-1', path:[name], owner:0, mount:'/data'};
    assert.deepEqual(parseUrl(formatUrl(route)), route);
  }
});

test('raw names with identical display labels or a literal base64 prefix drill independently', async () => {
  const {rawSegment} = await import('../api.js');
  const {drillStack} = await import('../state.js');
  const children = [{name:'run-�',name_b64:'cnVuLf8='},{name:'run-�',name_b64:'cnVuLf4='},{name:'~b64~cnVuLf8'}];
  const detail = {mounts:[{path:'/data',tree:{children}}]};
  for (const child of children) {
    const route = {...parseUrl('/?server=a'),path:[rawSegment(child)]};
    assert.equal(drillStack(detail,parseUrl(formatUrl(route))).at(-1).node,child);
  }
});

test('generated server routes round trip through bookmarks', () => {
  for (let i=0;i<1000;i++) {
    const route = {page:'server',serverId:'s-'+i,tab:['top','stale','treemap','users'][i%4],
      owner:i%3 ? i : null, mount:i%2 ? '/data '+i : null, volumeId:i%3 ? null : 'v-'+i,
      path:Array.from({length:i%5},(_,j)=>['한 글','&?#','~b64~_w','x+y'][j] + i)};
    assert.deepEqual(parseUrl(formatUrl(route)),route);
  }
});
