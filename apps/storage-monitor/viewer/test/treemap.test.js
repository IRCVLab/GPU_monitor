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
