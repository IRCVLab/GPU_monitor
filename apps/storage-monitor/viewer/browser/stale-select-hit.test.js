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
const { expectedFailure } = require('../expected-failure');

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

// Stops a child this script started, by its PID. Chrome runs detached, so its PID
// leads a process group and signalling the group stops its helper processes too.
async function stop(child, { group = false } = {}) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise(resolve => child.once('exit', resolve));
  const signal = sig => { try { process.kill(group ? -child.pid : child.pid, sig); } catch {} };
  signal('SIGTERM');
  const done = await Promise.race([exited.then(() => true), new Promise(resolve => setTimeout(resolve, 5000, false))]);
  if (!done) signal('SIGKILL');
  if (group) {
    await waitFor('Chrome helper processes to exit', () => {
      try { process.kill(-child.pid, 0); return false; } catch { return true; }
    }, 5000).catch(() => signal('SIGKILL'));
  }
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
  const cdpUrl = `http://127.0.0.1:${port}`;
  await waitFor('Chrome DevTools endpoint', () => getJson(cdpUrl + '/json/version'));
  return { cdpUrl, chrome };
}

async function startSampleServer(tmp) {
  const port = await freePort();
  const server = spawn('python3', [path.join(APP_DIR, 'viewer', 'serve.py')], {
    cwd: APP_DIR,
    stdio: 'ignore',
    env: {
      ...process.env,
      STORAGE_VIZ_DEV_SAMPLE_DIR: path.join(APP_DIR, 'data'),
      STORAGE_VIZ_BIND: '127.0.0.1',
      STORAGE_VIZ_PORT: String(port),
      STORAGE_VIZ_STATE_DIR: path.join(tmp, 'state'),
    },
  });
  const origin = `http://127.0.0.1:${port}`;
  await waitFor('sample serve.py', () => getJson(origin + '/api/servers'));
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
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
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
  const picks = () => tab.evaluate('cleanupItems().length');
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
    await expectedFailure(['V3'], CLICK_MESSAGE, async function testStaleSelectRightEdgeClickLandsOnButton() {
      for (const width of WIDTHS) await checkWidth(tab, sample.origin, width);
    });
    console.log('stale select hit test passed');
  } finally {
    if (tab) await tab.close();
    if (sample) await stop(sample.server);
    if (browser) await stop(browser.chrome, { group: true });
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
