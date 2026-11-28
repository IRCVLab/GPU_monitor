#!/usr/bin/env node
'use strict';

// V3 behavioural repro: a click at the Stale Select button's right edge must land
// on the button, at 390, 768 and 1440 px. Runs in a real browser over CDP against
// a sample-data serve.py this script starts on a free port.
//
// Browser: the CDP endpoint at STORAGE_VIZ_CDP_URL (default http://127.0.0.1:9223)
// when one answers; this script opens its own tab there and closes it. Otherwise
// it launches CHROME_BIN or the first Chrome on PATH with a private profile.
// With neither, it prints a SKIP line and exits 0, so CI without Chrome stays green.
// Every process it starts is stopped by its own PID.

const assert = require('assert');
const { spawn } = require('child_process');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');


const APP_DIR = path.resolve(__dirname, '..', '..');
const CDP_URL = process.env.STORAGE_VIZ_CDP_URL || 'http://127.0.0.1:9223';
const WIDTHS = [390, 768, 1440];
const CLICK_MESSAGE = "a click at the Stale Select button's right edge must select its row";

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

async function waitFor(what, probe, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      const value = await probe();
      if (value) return value;
    } catch (err) {
      if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}: ${err.message}`);
    }
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
}

function findChrome() {
  if (process.env.CHROME_BIN) return process.env.CHROME_BIN;
  const names = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser'];
  for (const dir of (process.env.PATH || '').split(path.delimiter)) {
    for (const name of names) {
      const candidate = path.join(dir, name);
      try {
        fs.accessSync(candidate, fs.constants.X_OK);
        return candidate;
      } catch {}
    }
  }
  return null;
}

// Only children returned by spawn reach this function. An existing CDP browser
// receives a tab close and is never signalled.
// A detached child leads its own process group. Chrome's helpers (renderer,
// crashpad) live in that group and keep writing the profile after the main
// process exits, so the whole group is signalled.
function signal(child, name) {
  try {
    if (child.spawnargs && child.pid && child._detachedGroup) process.kill(-child.pid, name);
    else child.kill(name);
  } catch (error) {
    if (error.code !== 'ESRCH') throw error;
  }
}

async function stop(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(resolve => child.once('exit',resolve));
  const timeout = setTimeout(() => signal(child, 'SIGKILL'),5000);
  try {
    signal(child, 'SIGTERM');
    await exited;
  } finally {
    clearTimeout(timeout);
  }
  if (child._detachedGroup) signal(child, 'SIGKILL');  // Any helper still alive in the group.
}

// Returns { cdpUrl, chrome } or null when no browser is available.
async function openBrowser(tmp) {
  try {
    await getJson(CDP_URL + '/json/version');
    return { cdpUrl: CDP_URL, chrome: null };
  } catch {}
  const binary = findChrome();
  if (!binary) return null;
  const port = await freePort();
  const chrome = spawn(binary, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--hide-scrollbars',
    `--remote-debugging-port=${port}`, '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${path.join(tmp, 'chrome')}`, 'about:blank',
  ], { stdio: 'ignore', detached: true });
  chrome._detachedGroup = true;
  const cdpUrl = `http://127.0.0.1:${port}`;
  // A cold Chrome on a busy CI runner can take well over 15 s to open DevTools.
  try { await waitFor('Chrome DevTools endpoint', () => getJson(cdpUrl + '/json/version'), 60000); }
  catch (error) { await stop(chrome); throw error; }
  return { cdpUrl, chrome };
}

async function startSampleServer(tmp) {
  const port = await freePort();
  const server = spawn('python3', ['-m', 'api.server', '--fixtures', process.argv[2] || 'tests/fixtures/fleet'], {
    cwd: APP_DIR,
    stdio: 'ignore',
    env: {
      ...process.env,
      STORAGE_VIZ_BIND: '127.0.0.1',
      STORAGE_VIZ_PORT: String(port),
      STORAGE_VIZ_STATE_DIR: path.join(tmp, 'state'),
    },
  });
  const origin = `http://127.0.0.1:${port}`;
  try { await waitFor('sample serve.py', () => getJson(origin + '/api/v2/fleet')); }
  catch (error) { await stop(server); throw error; }
  return { server, origin };
}

class CdpTab {
  static async open(cdpUrl) {
    const target = await getJson(cdpUrl + '/json/new?about:blank', { method: 'PUT' });
    const socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, { once: true });
      socket.addEventListener('error', () => reject(new Error('CDP WebSocket failed to open')), { once: true });
    });
    return new CdpTab(cdpUrl, target.id, socket);
  }

  constructor(cdpUrl, targetId, socket) {
    this.cdpUrl = cdpUrl;
    this.targetId = targetId;
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.errors = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown' ||
          (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') ||
          (message.method === 'Log.entryAdded' && message.params.entry.level === 'error')) {
        this.errors.push(message.params);
      }
      const waiter = message.id && this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(new Error(`${waiter.method}: ${message.error.message}`));
      else waiter.resolve(message.result);
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => this.pending.set(id, { method, resolve, reject }));
  }

  async evaluate(expression) {
    const { result, exceptionDetails } = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (exceptionDetails) throw new Error(`page threw: ${exceptionDetails.exception?.description || exceptionDetails.text}`);
    return result.value;
  }

  async click(x, y) {
    for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
      await this.send('Input.dispatchMouseEvent', { type, x, y, button: 'left', clickCount: 1 });
    }
  }

  async close() {
    this.socket.close();
    await fetch(`${this.cdpUrl}/json/close/${this.targetId}`).catch(() => {});
  }
}

const BUTTON = "document.querySelector('#staleRows .cleanup-select-btn')";

// Measures the first Stale row's Select button without scrolling: the point 1 px
// inside its right edge, what the page hits there, and an id for the DOM node, so
// a re-render of the virtualized rows shows up as a changed id.
const MEASURE_RIGHT_EDGE = `(() => {
  const button = ${BUTTON};
  if (!button) return null;
  button.v3ProbeId ??= Math.random();
  const r = button.getBoundingClientRect();
  const x = r.right - 1;
  const y = (r.top + r.bottom) / 2;
  const hit = document.elementFromPoint(x, y);
  return {
    node: button.v3ProbeId,
    path: button.dataset.cleanupPath,
    point: [x, y],
    rect: [r.left, r.top, r.width, r.height].map(Math.round),
    hit: !hit ? 'nothing' : button.contains(hit) ? 'button' : hit.tagName.toLowerCase() + '.' + String(hit.className).replace(/ /g, '.'),
  };
})()`;

async function checkWidth(tab, origin, width) {
  await tab.send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
  // A distinct query per width forces a fresh document; the same URL would only
  // re-run the hash route and keep the previous width's picks.
  await tab.send('Page.navigate', { url: `${origin}/?server=hinton&width=${width}#stale` });
  await waitFor(`the Stale Select button at ${width}px`, () =>
    tab.evaluate(`(() => { const b = ${BUTTON}; if (b) b.scrollIntoView({ block: 'center', inline: 'nearest' }); return !!b; })()`));
  // Scrolling re-renders the virtualized rows on the next frame, and late layout can
  // move them. Click only once two measurements 200 ms apart agree, node included.
  let last = null;
  const probe = await waitFor(`a settled Stale Select button at ${width}px`, async () => {
    await new Promise(resolve => setTimeout(resolve, 200));
    const current = await tab.evaluate(MEASURE_RIGHT_EDGE);
    const settled = current && last && JSON.stringify(current) === JSON.stringify(last);
    last = current;
    return settled ? current : null;
  });
  assert.ok(await tab.evaluate(`(() => {
    const b = ${BUTTON}, r = b.getBoundingClientRect();
    return [r.left+4,r.right-4].every(x => [r.top+4,r.bottom-4].every(y => b.contains(document.elementFromPoint(x,y))));
  })()`), 'V3 all four inset corners hit Select');
  const picks = () => tab.evaluate("document.querySelectorAll('#staleRows .cleanup-select-btn[aria-pressed=true]').length");
  const before = await picks();
  await tab.click(...probe.point);
  const after = await waitFor('the pick count to change', async () => {
    const now = await picks();
    return now !== before ? now : null;
  }, 1000).catch(() => before);
  const selector = `#staleRows .cleanup-select-btn[data-cleanup-path=${JSON.stringify(probe.path)}]`;
  const pressed = await tab.evaluate(`document.querySelector(${JSON.stringify(selector)})?.getAttribute('aria-pressed')`);
  assert.ok(after === before + 1 && pressed === 'true',
    `${CLICK_MESSAGE}\nat ${width}px the point ${probe.point.map(Math.round)} hits ${probe.hit}; ` +
    `button rect ${probe.rect}; picks ${before} -> ${after}; aria-pressed ${pressed}`);
  assert.equal(await tab.evaluate("document.getElementById('cleanupDetails').hidden"),true);
  const command = await tab.evaluate("document.getElementById('cleanupCopyAll').dataset.copyCommand");
  assert.match(command,/hostname -s/);
  await tab.evaluate("window.dispatchEvent(new Event('focus'))");
  await waitFor('focus refresh', () => tab.evaluate("performance.getEntriesByType('resource').filter(e=>e.name.endsWith('/api/v2/servers/hinton')).length >= 2"));
  assert.equal(await tab.evaluate("document.getElementById('cleanupCopyAll').dataset.copyCommand"),command,'V1 focus preserves command');
  const geometry = await tab.evaluate(`(() => {
    const vp=document.getElementById('staleViewport'); vp.scrollLeft=vp.scrollWidth;
    const head=[...document.querySelectorAll('#staleHead th')].map(e=>e.getBoundingClientRect());
    const cells=[...document.querySelector('#staleRows tr:not([aria-hidden])').cells].map(e=>e.getBoundingClientRect());
    return {aligned:head.every((r,i)=>Math.abs(r.left-cells[i].left)<1 && Math.abs(r.width-cells[i].width)<1),
      path:cells.at(-1).width, page:document.documentElement.scrollWidth};
  })()`);
  assert.ok(geometry.aligned && geometry.path >= 240 && geometry.page <= width,'V4 header and rows align after scroll: '+JSON.stringify(geometry));
}

async function shot(tab, name, width) {
  const dir = process.env.STORAGE_VIZ_SCREENSHOT_DIR;
  if (!dir) return;
  fs.mkdirSync(dir,{recursive:true});
  if (name !== 'overview') await tab.evaluate(`(async () => {
    const target=document.querySelector('${({stale:'#staleViewport',users:'#usersChart',top:'#topTbl'})[name] || '#treemap'}');
    if ('${name}' === 'detail-treemap' && ${width} === 1440) document.querySelector('#main').scrollTo({top:0,behavior:'instant'});
    else target.scrollIntoView({block:'center',behavior:'instant'});
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
  })()`);
  const {data} = await tab.send('Page.captureScreenshot',{format:'png'});
  fs.writeFileSync(path.join(dir,name+'-'+width+'.png'),Buffer.from(data,'base64'));
}

async function checkNavigation(tab,origin,width) {
  await tab.send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
  await tab.send('Page.navigate',{url:origin+'/'});
  await waitFor('overview mounts',()=>tab.evaluate("document.querySelectorAll('.overview-mount[data-pressure]').length >= 4"));
  const rootSizes = await tab.evaluate("Array.from(document.querySelectorAll('.overview-card[data-server-id=hinton] .overview-scan-folder'), row => [row.firstChild.textContent, row.lastChild.textContent])");
  assert.deepEqual(rootSizes.filter(([path]) => path === '/data' || path === '/data1'), [['/data','7.53 TB'],['/data1','1.15 TB']]);
  await shot(tab,'overview',width);
  await tab.evaluate("document.querySelector('.overview-card[data-server-id=hinton]').click()");
  await waitFor('treemap tiles',()=>tab.evaluate("document.querySelectorAll('.tmtile').length"));
  assert.equal(await tab.evaluate("new URL(location).searchParams.get('tab')"),'treemap','V6 card href decides tab');
  await tab.evaluate('document.querySelector(".cap-folder-button[data-folder=\'/data\']").click()');
  await waitFor('multi-root tree',()=>tab.evaluate("new URL(location).searchParams.has('vol') && document.querySelectorAll('.tm-crumb').length > 1"));
  const banner = await tab.evaluate("({text:document.querySelector('#warnBanner').innerText, height:document.querySelector('#warnBanner').getBoundingClientRect().height})");
  assert.ok(banner.height > 0);
  assert.match(banner.text,/2 directories could not be read/);
  for (const path of ['/home/jusung','/data/private_collab']) assert.ok(banner.text.includes(path+' (EACCES)'));
  await shot(tab,'detail-treemap',width);
  const drill = await tab.evaluate(`(() => {
    const tile=document.querySelector('.tmtile.drillable[data-cleanup-path]');
    tile.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
    return new URL(location).searchParams.get('path');
  })()`);
  assert.ok(drill,'Enter on a directory records the drill in the URL');
  await waitFor('drilled tiles',()=>tab.evaluate("document.querySelectorAll('.tm-crumb').length > 1"));
  await tab.evaluate("document.getElementById('treemapCleanupMode').click()");
  await waitFor('latched select mode',()=>tab.evaluate("document.getElementById('treemapCleanupMode').getAttribute('aria-pressed')==='true'"));
  await tab.evaluate("document.querySelector('.tmtile[data-cleanup-path]').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}))");
  await waitFor('keyboard selection',()=>tab.evaluate("document.getElementById('cleanupCopyAll').dataset.copyCommand.length>0"));
  await tab.evaluate("document.getElementById('cleanupClear').click()");
  await tab.evaluate("document.getElementById('treemapCleanupMode').click()");

  await tab.evaluate("document.querySelector('.tab[data-tab=stale]').click()");
  await waitFor('Stale',()=>tab.evaluate("!!document.querySelector('#staleRows .cleanup-select-btn')"));
  await shot(tab,'stale',width);
  await tab.evaluate("document.querySelector('.tab[data-tab=top]').click()");
  await waitFor('owner options',()=>tab.evaluate("document.getElementById('ownerFilter').options.length > 1"));
  await shot(tab,'top',width);
  const owner = await tab.evaluate(`(() => {const select=document.getElementById('ownerFilter');
    select.selectedIndex=1;select.dispatchEvent(new Event('change'));return select.value;})()`);
  await tab.evaluate('history.back()');
  await waitFor('back to unfiltered files',()=>tab.evaluate("!new URL(location).searchParams.has('owner')"));
  await tab.evaluate('history.forward()');
  await waitFor('V8 forward restores owner',()=>tab.evaluate("document.getElementById('ownerFilter').value === "+JSON.stringify(owner)));
  await tab.evaluate("document.getElementById('overviewBack').click()");
  await tab.evaluate('history.back()');
  await waitFor('V8 back restores owner',()=>tab.evaluate("document.querySelector('#panel-topfiles.active') && document.getElementById('ownerFilter').value === "+JSON.stringify(owner)));
  await tab.evaluate('history.forward()');
  await waitFor('forward restores overview',()=>tab.evaluate("!document.getElementById('overviewView').hidden"));
  await tab.evaluate("document.querySelector('.overview-card[data-server-id=hinton]').click()");
  await waitFor('card after another tab',()=>tab.evaluate("!!document.querySelector('#panel-treemap.active') && !document.getElementById('detailView').hidden"));
  await tab.evaluate("document.querySelector('.tab[data-tab=users]').click()");
  await waitFor('Users chart',()=>tab.evaluate("!!document.querySelector('#usersChart canvas') && !!document.querySelector('#usersTbl tr[data-uid]')"));
  await shot(tab,'users',width);
  await tab.send('Page.navigate',{url:origin+'/?server=orion'});
  await waitFor('orion capture tree',()=>tab.evaluate("!!document.querySelector('.tmtile.drillable')"));
  await tab.evaluate("document.querySelector('.tmtile.drillable').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}))");
  await waitFor('orion tree drilled',()=>tab.evaluate("new URL(location).searchParams.has('path') && document.querySelectorAll('.tm-crumb').length > 1"));
  await shot(tab,'orion-treemap',width);
  await tab.send('Page.navigate',{url:origin+'/?server=unknown'});
  await waitFor('unknown server',()=>tab.evaluate("!document.getElementById('detailError').hidden"));
  assert.ok(await tab.evaluate("['detailHead','detailActions','detailTabs','caps','detailPanels'].every(id=>document.getElementById(id).hidden)"),'V7 unknown server hides chrome');
}

async function main() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'storage-viz-v3-'));
  let browser = null;
  let sample = null;
  let tab = null;
  try {
    browser = await openBrowser(tmp);
    if (!browser) {
      console.log(`SKIP: V3 Stale Select hit test needs Chrome: nothing answers at ${CDP_URL} and no Chrome is on PATH (set CHROME_BIN)`);
      return;
    }
    sample = await startSampleServer(tmp);
    tab = await CdpTab.open(browser.cdpUrl);
    await tab.send('Runtime.enable');
    await tab.send('Log.enable');
    for (const width of WIDTHS) await checkWidth(tab, sample.origin, width);
    for (const width of [390,1440]) await checkNavigation(tab,sample.origin,width);
    assert.deepEqual(tab.errors, [], 'browser console errors');
    console.log('stale select hit test passed');
  } finally {
    if (tab) await tab.close();
    if (sample) await stop(sample.server);
    if (browser) await stop(browser.chrome);
    fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 });
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
