import test from 'node:test';
import assert from 'node:assert/strict';
import * as viewer from '../views/treemap.js';
const GiB = 1024 ** 3;
function testTreemapFidelity() {
  const { squarify, rectArea } = viewer;
  const gib = 1024 ** 3;
  const rects = squarify([
    { id: "large", value: 600 * gib },
    { id: "small", value: 600 * 1024 ** 2 },
  ], 0, 0, 1000, 600);
  const large = rects.find(r => r.id === "large");
  const small = rects.find(r => r.id === "small");
  assert(large && small, "both sibling rects are present");
  const ratio = rectArea(large) / rectArea(small);
  assert(ratio > 900 && ratio < 1100, `600GB/600MB area ratio should remain ~1024, got ${ratio}`);
}
test('testTreemapFidelity', testTreemapFidelity);

test('micro tiles stay proportional, aggregate stays in legend and groups paint behind children', async () => {
  const {documentFixture} = await import('./dom.js');
  globalThis.document = documentFixture();
  document.querySelector = () => null;
  document.querySelectorAll = () => [];
  globalThis.getComputedStyle = () => ({paddingTop:0,paddingBottom:0});
  const tree = {children:[
    {name:'proj',kind:'directory',bytes:600*GiB,uid:1,children:[{name:'big',kind:'file',bytes:600*GiB,uid:1}]},
    {name:'small',kind:'file',bytes:600*1024**2,uid:1}
  ]};
  const route = {page:'server',serverId:'a',path:[]};
  const data = {mounts:[{path:'/data',mount_id:'v',tree}], selected_roots:[{mount_id:'v',scan_root:'/data',status:'complete'}]};
  viewer.drawTreemap({DATA:data,route,ownerByUid:new Map(),uidColorMap:new Map(),entries:[],ui:{},colorForUid:()=>'#123456'});
  const tiles = document.getElementById('treemap');
  assert.equal(tiles._tmHiddenItems.bytes,600*1024**2);
  const group = tiles.children.find(tile=>tile.dataset.cleanupPath === '/data/proj');
  const child = tiles.children.find(tile=>tile.dataset.cleanupPath === '/data/proj/big');
  assert.equal(group.style.zIndex,'1');
  assert.equal(child.style.zIndex,'2');
  assert.ok(document.getElementById('treemapLegend').children.some(item=>item.className.includes('tm-scale-note')));
  assert.equal(tiles.children.some(item=>item.className.includes('tm-scale-note')),false);
});
