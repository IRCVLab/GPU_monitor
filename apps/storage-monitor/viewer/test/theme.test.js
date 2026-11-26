import test from 'node:test';
import assert from 'node:assert/strict';
import {bindTheme} from '../views/theme.js';
import {documentFixture} from './dom.js';
function setup() {
  globalThis.document = documentFixture();
  globalThis.window = {innerWidth:1200,innerHeight:800,matchMedia:()=>({matches:false})};
  bindTheme(()=>{});
}
async function testThemeRevealOriginUsesVisibleIconCenterAndButtonFallback() {
  setup();
  const root = document.documentElement;
  const button = document.getElementById('themeModeButton');
  const sun = document.createElement('svg');
  const moon = document.createElement('svg');
  sun.getBoundingClientRect = () => ({ left: 100, top: 50, width: 20, height: 10 });
  moon.getBoundingClientRect = () => ({ left: 300, top: 70, width: 12, height: 12 });
  button.getBoundingClientRect = () => ({ left: 10, top: 20, width: 80, height: 40 });
  button.querySelector = (selector) => selector === '.theme-icon-sun' ? sun : selector === '.theme-icon-moon' ? moon : null;
  const firstCaptured = [];
  root.style = {
    setProperty(name, value) { firstCaptured.push([name, value]); },
    removeProperty() {},
  };
  root.classList.remove('light', 'dark');
  root.classList.add('dark');
  let updateRan = false;
  document.startViewTransition = (update) => {
    update();
    updateRan = true;
    return { finished: Promise.resolve() };
  };

  await button.onclick({ currentTarget: button });
  assert.ok(updateRan, 'view transition update callback must run');
  assert.deepStrictEqual(firstCaptured.filter(([name]) => name === '--theme-reveal-x').map(([, value]) => value), ['110px'], 'reveal x must use the currently visible SVG center, not the button center');
  assert.deepStrictEqual(firstCaptured.filter(([name]) => name === '--theme-reveal-y').map(([, value]) => value), ['55px'], 'reveal y must use the currently visible SVG center, not the button center');

  const captured = [];
  root.style = {
    setProperty(name, value) { captured.push([name, value]); root[name] = value; },
    removeProperty(name) { delete root[name]; },
  };
  button.querySelector = () => null;
  root.classList.remove('light', 'dark');
  root.classList.add('dark');
  await button.onclick({ currentTarget: button });
  assert.deepStrictEqual(captured.filter(([name]) => name === '--theme-reveal-x').map(([, value]) => value), ['50px'], 'missing visible icon falls back to button center x');
  assert.deepStrictEqual(captured.filter(([name]) => name === '--theme-reveal-y').map(([, value]) => value), ['40px'], 'missing visible icon falls back to button center y');
}

async function testThemeRevealLifecycleLocksAndCleansUpAfterFinished() {
  setup();
  const root = document.documentElement;
  const button = document.getElementById('themeModeButton');
  root.classList.remove('light', 'dark');
  root.classList.add('dark');
  button.getBoundingClientRect = () => ({ left: 20, top: 30, width: 40, height: 40 });
  const properties = new Map();
  root.style = {
    setProperty(name, value) { properties.set(name, value); },
    removeProperty(name) { properties.delete(name); },
  };
  let finish;
  let startCalls = 0;
  let classPresentInsideUpdate = false;
  document.startViewTransition = (update) => {
    startCalls += 1;
    classPresentInsideUpdate = root.classList.contains('theme-transitioning');
    update();
    return { finished: new Promise(resolve => { finish = resolve; }) };
  };

  const first = button.onclick({ currentTarget: button });
  const second = button.onclick({ currentTarget: button });
  assert.strictEqual(startCalls, 1, 'rapid repeated activation cannot start an overlapping view transition');
  assert.ok(classPresentInsideUpdate, 'html.theme-transitioning must be present before startViewTransition captures');
  assert.ok(root.classList.contains('theme-transitioning'), 'transitioning class stays through transition.finished');
  assert.strictEqual(button.getAttribute('aria-busy'), 'true', 'theme button is marked busy while locked');
  assert.ok(properties.has('--theme-reveal-radius'), 'reveal CSS vars stay through transition.finished');
  finish();
  await first;
  await second;
  assert.ok(!root.classList.contains('theme-transitioning'), 'transitioning class is removed after finished');
  assert.strictEqual(button.getAttribute('aria-busy'), undefined, 'busy state is cleaned up after finished');
  assert.strictEqual(properties.size, 0, 'reveal CSS vars are cleaned up after finished');
  assert.strictEqual(document.activeElement, button, 'focus returns to the button after transition cleanup');
}


function testThemeUnsupportedViewTransitionSwitchesImmediatelyWithNoTweenState() {
  setup();
  const root = document.documentElement;
  const button = document.getElementById('themeModeButton');
  root.classList.remove('light', 'dark');
  root.classList.add('dark');
  window.matchMedia = (query) => ({ matches: false, media: query, addEventListener() {}, addListener() {} });
  document.startViewTransition = undefined;
  const properties = [];
  root.style = {
    setProperty(name, value) { properties.push(['set', name, value]); },
    removeProperty(name) { properties.push(['remove', name]); },
  };
  let classActiveDuringCookie = false;
  let cookieValue = '';
  Object.defineProperty(document, 'cookie', {
    configurable: true,
    get() { return cookieValue; },
    set(value) {
      classActiveDuringCookie = root.classList.contains('theme-no-transition');
      cookieValue = String(value);
    },
  });

  const result = button.onclick({ currentTarget: button });

  assert.ok(result && typeof result.then === 'function', 'toggle remains async-compatible on unsupported browsers');
  return result.then((mode) => {
    assert.strictEqual(mode, 'light', 'unsupported path returns the destination mode');
    assert.ok(root.classList.contains('light'), 'unsupported path applies destination immediately');
    assert.ok(!root.classList.contains('dark'), 'unsupported path removes the previous theme immediately');
    assert.strictEqual(classActiveDuringCookie, true, 'theme-no-transition must be active while destination/cookie are applied');
    assert.ok(!root.classList.contains('theme-no-transition'), 'theme-no-transition is removed after final style is forced');
    assert.ok(!root.classList.contains('theme-transitioning'), 'unsupported path never enters native transition lock class');
    assert.deepStrictEqual(properties, [], 'unsupported path does not set or remove reveal CSS vars');
    assert.strictEqual(button.getAttribute('aria-busy'), undefined, 'unsupported path does not leave a transition lock/busy state');
    assert.strictEqual(button.getAttribute('aria-pressed'), 'false', 'aria-pressed reflects the light destination immediately');
    assert.match(document.cookie, /themeMode=light; Path=\/; SameSite=Lax/, 'unsupported path persists the destination theme cookie');
    assert.strictEqual(document.activeElement, button, 'focus is preserved on unsupported immediate switch');
  });
}

function testInitialThemeAriaSemanticsSynchronizeBeforeInteraction() {
  setup();
  const button = document.getElementById('themeModeButton');
  document.cookie = 'themeMode=light';
  bindTheme(()=>{});
  assert.strictEqual(button.getAttribute('aria-pressed'), 'false', 'initial stored light mode synchronizes aria before any click');
  document.cookie = 'themeMode=dark';
  bindTheme(()=>{});
  assert.strictEqual(button.getAttribute('aria-pressed'), 'true', 'initial stored dark mode synchronizes aria before any click');
}

function testThemeReducedMotionSwitchesImmediatelyWithNoRevealState() {
  setup();
  const root = document.documentElement;
  const button = document.getElementById('themeModeButton');
  root.classList.remove('light', 'dark');
  root.classList.add('dark');
  window.matchMedia = (query) => ({ matches: query.includes('prefers-reduced-motion'), addEventListener() {}, addListener() {} });
  let startCalls = 0;
  document.startViewTransition = () => { startCalls += 1; return { finished: Promise.resolve() }; };
  const properties = [];
  root.style = {
    setProperty(name, value) { properties.push([name, value]); },
    removeProperty(name) { properties.push(['remove', name]); },
  };
  button.onclick({ currentTarget: button });
  assert.strictEqual(startCalls, 0, 'reduced motion must not start a native view transition');
  assert.ok(root.classList.contains('light'), 'reduced motion switches immediately');
  assert.ok(!root.classList.contains('theme-transitioning'), 'reduced motion must not add transition class that rotates icons');
  assert.deepStrictEqual(properties, [], 'reduced motion must not set reveal CSS vars');
  assert.strictEqual(document.activeElement, button, 'focus is preserved on immediate switch');
}

test('testThemeRevealOriginUsesVisibleIconCenterAndButtonFallback',testThemeRevealOriginUsesVisibleIconCenterAndButtonFallback);
test('testThemeRevealLifecycleLocksAndCleansUpAfterFinished',testThemeRevealLifecycleLocksAndCleansUpAfterFinished);
test('testThemeUnsupportedViewTransitionSwitchesImmediatelyWithNoTweenState',testThemeUnsupportedViewTransitionSwitchesImmediatelyWithNoTweenState);
test('testInitialThemeAriaSemanticsSynchronizeBeforeInteraction',testInitialThemeAriaSemanticsSynchronizeBeforeInteraction);
test('testThemeReducedMotionSwitchesImmediatelyWithNoRevealState',testThemeReducedMotionSwitchesImmediatelyWithNoRevealState);
