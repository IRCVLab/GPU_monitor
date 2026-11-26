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
