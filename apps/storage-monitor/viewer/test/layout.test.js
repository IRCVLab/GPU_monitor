import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutOverviewMasonry} from '../views/overview.js';
function testOverviewMasonryPreservesOrderAcrossResponsiveColumns() {
  globalThis.window = {};
  const heights = [316, 250, 170, 250, 170, 210, 120];
  const items = heights.map(height => ({
    style: {},
    firstElementChild: { getBoundingClientRect: () => ({ height }) },
    getBoundingClientRect: () => ({ height }),
  }));
  const container = { children: items, offsetWidth: 1232 };
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '401px 401px 401px' });
  layoutOverviewMasonry(container);
  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '2', '3', '1', '2', '3', '1'], 'three-column overview layout must assign cards deterministically by DOM order modulo column count');
  const threeColumnStarts = items.map(item => Number(item.style.gridRow.split('/')[0].trim()));
  assert.deepStrictEqual(threeColumnStarts, [1, 1, 1, 23, 19, 14, 41], 'three-column starts must stack vertically within each deterministic column');

  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '609px 609px' });
  layoutOverviewMasonry(container);
  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '2', '1', '2', '1', '2', '1'], 'two-column overview layout must recompute deterministic odd/even columns from cleared placement');
  const twoColumnStarts = items.map(item => Number(item.style.gridRow.split('/')[0].trim()));
  assert.deepStrictEqual(twoColumnStarts, [1, 1, 23, 19, 36, 37, 49], 'two-column starts must stack vertically within each deterministic column');

  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '370px' });
  layoutOverviewMasonry(container);
  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '1', '1', '1', '1', '1', '1'], 'one-column overview layout must place every card in column 1');
  const oneColumnStarts = items.map(item => Number(item.style.gridRow.split('/')[0].trim()));
  assert(oneColumnStarts.every((start, index) => index === 0 || start > oneColumnStarts[index - 1]), 'mobile cards must keep strict server order without overlap');
}



function makeOverviewLayoutItem(height, rects) {
  let rectIndex = 0;
  return {
    style: {},
    firstElementChild: { getBoundingClientRect: () => ({ height }) },
    getBoundingClientRect: () => rects[Math.min(rectIndex++, rects.length - 1)],
    offsetWidth: 10,
  };
}

function testOverviewLayoutClearsStaleInlinePlacementAndMotion() {
  globalThis.window = {};
  const items = [
    makeOverviewLayoutItem(120, [{ left: 0, top: 0 }, { left: 0, top: 0 }]),
    makeOverviewLayoutItem(120, [{ left: 0, top: 0 }, { left: 354, top: 0 }]),
  ];
  items[0].style.gridColumn = '99';
  items[0].style.gridRow = '99 / span 99';
  items[0].style.transform = 'translate(9px, 9px)';
  items[0].style.transition = 'transform 280ms cubic-bezier(.22,1,.36,1)';
  const container = { children: items, offsetWidth: 714, __overviewColumnCount: 2 };
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '350px 350px' });

  layoutOverviewMasonry(container);

  assert.deepStrictEqual(items.map(item => item.style.gridColumn), ['1', '2'], 'layout must recompute grid columns from cleared stale placement');
  assert.strictEqual(items[0].style.gridRow, '1 / span 9', 'layout must recompute grid rows from cleared stale placement');
  assert.strictEqual(items[0].style.transform, '', 'same-column-count relayout must clear stale FLIP transform leftovers');
  assert.strictEqual(items[0].style.transition, '', 'same-column-count relayout must clear stale FLIP transition leftovers');
}

function testOverviewFlipRunsOnlyOnColumnCountChangeAndSkipsReducedMotion() {
  globalThis.window = {};
  globalThis.setTimeout = () => undefined;
  globalThis.window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  const moving = makeOverviewLayoutItem(120, [{ left: 354, top: 0 }, { left: 0, top: 150 }]);
  const container = { children: [moving], offsetWidth: 640, __overviewColumnCount: 2 };
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });

  layoutOverviewMasonry(container);
  assert.strictEqual(container.__overviewColumnCount, 1, 'layout must remember the newly active column count');
  assert.strictEqual(moving.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'column-count changes should use the approved restrained FLIP timing');
  assert.strictEqual(moving.style.transform, 'translate(0, 0)', 'column-count changes should animate back to natural placement');

  moving.style.transform = '';
  moving.style.transition = '';
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '620px' });
  layoutOverviewMasonry(container);
  assert.strictEqual(moving.style.transform, '', 'continuous width resize at the same column count must not run FLIP');
  assert.strictEqual(moving.style.transition, '', 'continuous width resize at the same column count must not add FLIP transition');

  const reduced = makeOverviewLayoutItem(120, [{ left: 0, top: 0 }, { left: 354, top: 0 }]);
  const reducedContainer = { children: [reduced], offsetWidth: 714, __overviewColumnCount: 1 };
  globalThis.window.matchMedia = () => ({ matches: true, addEventListener() {}, addListener() {} });
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '350px 350px' });
  layoutOverviewMasonry(reducedContainer);
  assert.strictEqual(reducedContainer.__overviewColumnCount, 2, 'reduced-motion relayout still updates the active column count');
  assert.strictEqual(reduced.style.transform, '', 'reduced motion must skip FLIP transforms');
  assert.strictEqual(reduced.style.transition, '', 'reduced motion must skip FLIP transitions');
}


function testOverviewFlipCapturesAllFirstRectsBeforeClearingPlacement() {
  globalThis.window = {};
  globalThis.setTimeout = () => undefined;
  const animationFrames = [];
  globalThis.requestAnimationFrame = (fn) => { animationFrames.push(fn); return animationFrames.length; };
  globalThis.window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  const items = [];
  const firstPositions = [
    { left: 0, top: 0 },
    { left: 354, top: 0 },
    { left: 0, top: 150 },
  ];
  const finalPositions = [
    { left: 0, top: 0 },
    { left: 0, top: 134 },
    { left: 0, top: 268 },
  ];
  for (let index = 0; index < 3; index += 1) {
    items[index] = {
      style: { gridColumn: index === 1 ? '2' : '1', gridRow: String(index + 1) + ' / span 9' },
      firstElementChild: { getBoundingClientRect: () => ({ height: 120 }) },
      getBoundingClientRect() {
        const originalTwoColumnPlacementStillIntact = items[0].style.gridColumn === '1' && items[1].style.gridColumn === '2' && items[2].style.gridColumn === '1';
        return originalTwoColumnPlacementStillIntact ? firstPositions[index] : finalPositions[index];
      },
      offsetWidth: 10,
    };
  }
  const container = { children: items, offsetWidth: 640, __overviewColumnCount: 2 };
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });

  layoutOverviewMasonry(container);

  assert.strictEqual(items[1].style.transition, 'none', 'later items must be prepared for FLIP before the animation frame runs');
  assert.strictEqual(items[1].style.transform, 'translate(354px, -134px)', 'later item FLIP delta must be computed from the pre-clear two-column rect, not the post-clear one-column rect');
  assert.strictEqual(items[2].style.transform, 'translate(0px, -118px)', 'all item first rects must be captured before any stale placement is cleared');
  for (const frame of animationFrames) frame();
  assert.strictEqual(items[1].style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'later items must animate during a real column-count change');
  assert.strictEqual(items[1].style.transform, 'translate(0, 0)', 'later items must complete FLIP from their previous two-column visual position');
}


function testOverviewFlipIgnoresStaleCallbacksAfterRapidRelayout() {
  globalThis.window = {};
  const animationFrames = [];
  const timers = [];
  globalThis.requestAnimationFrame = (fn) => { animationFrames.push(fn); return animationFrames.length; };
  globalThis.cancelAnimationFrame = (id) => { animationFrames[id - 1] = null; };
  globalThis.setTimeout = (fn) => { timers.push(fn); return timers.length; };
  globalThis.clearTimeout = (id) => { timers[id - 1] = null; };
  globalThis.window.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  let positions = [{ left: 354, top: 0 }, { left: 0, top: 134 }];
  const item = {
    style: {},
    firstElementChild: { getBoundingClientRect: () => ({ height: 120 }) },
    getBoundingClientRect: () => positions.shift() || { left: 0, top: 134 },
    offsetWidth: 10,
  };
  const container = { children: [item], offsetWidth: 640, __overviewColumnCount: 2 };
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });

  layoutOverviewMasonry(container);
  assert.strictEqual(item.style.transform, 'translate(354px, -134px)', 'first relayout prepares FLIP before RAF runs');

  item.style.transform = 'translate(18px, 0px)';
  item.style.transition = 'transform 280ms cubic-bezier(.22,1,.36,1)';
  positions = [{ left: 0, top: 134 }];
  layoutOverviewMasonry(container);
  assert.strictEqual(item.style.transform, '', 'newer same-column relayout clears stale transform before old RAF can run');
  assert.strictEqual(item.style.transition, '', 'newer same-column relayout clears stale transition before old RAF can run');

  for (const frame of animationFrames) if (frame) frame();
  assert.strictEqual(item.style.transform, '', 'stale RAF from the older column-change relayout must not restore transform');
  assert.strictEqual(item.style.transition, '', 'stale RAF from the older column-change relayout must not restore transition');

  positions = [{ left: 0, top: 134 }, { left: 354, top: 0 }];
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '350px 350px' });
  let frameStart = animationFrames.length;
  layoutOverviewMasonry(container);
  for (const frame of animationFrames.slice(frameStart)) if (frame) frame();
  const oldCleanup = timers[timers.length - 1];
  assert.strictEqual(item.style.transform, 'translate(0, 0)', 'older animation should schedule cleanup after its RAF runs');
  assert.strictEqual(item.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'older animation should use the approved transition before cleanup');

  positions = [{ left: 354, top: 0 }, { left: 0, top: 134 }];
  globalThis.getComputedStyle = () => ({ gridAutoRows: '1px', rowGap: '14px', gridTemplateColumns: '640px' });
  frameStart = animationFrames.length;
  layoutOverviewMasonry(container);
  for (const frame of animationFrames.slice(frameStart)) if (frame) frame();
  assert.strictEqual(item.style.transform, 'translate(0, 0)', 'newer animation should be allowed to own the transform');
  assert.strictEqual(item.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'newer animation should be allowed to own the transition');

  if (oldCleanup) oldCleanup();
  assert.strictEqual(item.style.transform, 'translate(0, 0)', 'old cleanup timer must not clear a newer animation transform');
  assert.strictEqual(item.style.transition, 'transform 280ms cubic-bezier(.22,1,.36,1)', 'old cleanup timer must not clear a newer animation transition');
}

test('testOverviewMasonryPreservesOrderAcrossResponsiveColumns',testOverviewMasonryPreservesOrderAcrossResponsiveColumns);
test('testOverviewLayoutClearsStaleInlinePlacementAndMotion',testOverviewLayoutClearsStaleInlinePlacementAndMotion);
test('testOverviewFlipRunsOnlyOnColumnCountChangeAndSkipsReducedMotion',testOverviewFlipRunsOnlyOnColumnCountChangeAndSkipsReducedMotion);
test('testOverviewFlipCapturesAllFirstRectsBeforeClearingPlacement',testOverviewFlipCapturesAllFirstRectsBeforeClearingPlacement);
test('testOverviewFlipIgnoresStaleCallbacksAfterRapidRelayout',testOverviewFlipIgnoresStaleCallbacksAfterRapidRelayout);
