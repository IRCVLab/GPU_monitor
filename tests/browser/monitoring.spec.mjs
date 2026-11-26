import { readFile } from 'node:fs/promises';
import { expect, test } from '@playwright/test';
import { gpuOrigin, storageOrigin } from './playwright.config.mjs';

const gpu = { id: 1, name: 'Fixture GPU', host: 'fixture', network: 'internal', port: 22, display_order: 0 };
const retired = { ...gpu, id: 2, host: 'retired', name: 'Retired fixture GPU', display_order: 1 };

function gpuStatus(server, user, timestamp) {
  return {
    ...server, server_id: server.id, server_name: server.name, status: 'online', last_seen: timestamp,
    gpus: [{ index: 0, name: 'NVIDIA H100', utilization: 42, memory_used: 1000, memory_total: 80000, users: [user] }],
  };
}

async function mockGpu(page, { status, servers = [gpu], websocket = () => {} } = {}) {
  await page.routeWebSocket(gpuOrigin.replace('http', 'ws') + '/ws/metrics', websocket);
  await page.route(gpuOrigin + '/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/servers/status') return status(route);
    if (path === '/api/servers') return route.fulfill({ json: servers });
    if (path.endsWith('/catalog')) return route.fulfill({ json: { servers: [] } });
    if (path.endsWith('/diagrams')) return route.fulfill({ json: [] });
    return route.fulfill({ json: { nodes: {}, edges: {}, ports: {}, history_status: 'available', peaks_included: true } });
  });
}

test('a late dashboard poll preserves a newer WebSocket sample', async ({ page }) => {
  const pending = Promise.withResolvers();
  const socket = Promise.withResolvers();
  const release = Promise.withResolvers();
  const older = gpuStatus(gpu, 'old-sample', new Date(Date.now() - 2000).toISOString());
  const newer = gpuStatus(gpu, 'new-sample', new Date().toISOString());
  await mockGpu(page, {
    status: async route => {
      pending.resolve();
      await release.promise;
      await route.fulfill({ json: { 1: older } });
    },
    websocket: socket.resolve,
  });

  await page.goto(gpuOrigin);
  try {
    const [ws] = await Promise.all([socket.promise, pending.promise]);
    ws.send(JSON.stringify({ type: 'update', data: newer }));
    // The loading screen hides pending cards until the HTTP snapshot arrives.
    await expect.poll(() => page.evaluate(async () => {
      const { serverStates } = await import('/src/lib/stores/servers.ts');
      let user;
      serverStates.subscribe(states => { user = states.get(1)?.gpus[0]?.users[0]; })();
      return user;
    })).toBe('new-sample');
  } finally {
    release.resolve();
  }
  const row = page.locator('.monitor-gpu-row').first();
  await expect(row).toContainText('new-sample');
  await expect(row).not.toContainText('old-sample');
});

test('System removes an absent server from its list and count', async ({ page }) => {
  let inventory = [gpu, retired];
  await mockGpu(page, {
    servers: inventory,
    status: route => route.fulfill({
      json: Object.fromEntries(inventory.map(server => [server.id, gpuStatus(server, 'fixture', new Date().toISOString())])),
    }),
  });
  await page.goto(gpuOrigin + '/system');
  const list = page.locator('#system-server-list');
  await expect(list.getByText(retired.name, { exact: true })).toBeVisible();
  await expect(page.locator('.sys-section-toolbar h2')).toHaveText('서버 2');
  await expect(page.locator('.sys-current-reading')).toContainText('0/2대 측정');

  await page.getByRole('link', { name: 'Power Flow', exact: true }).click();
  await expect(page).toHaveURL(gpuOrigin + '/system/power-flow');
  inventory = [gpu];
  await page.getByRole('link', { name: '서버 상태' }).click();
  await expect(list.getByText(gpu.name, { exact: true })).toBeVisible();
  await expect(list.getByText(retired.name, { exact: true })).toHaveCount(0);
  await expect(page.locator('.sys-section-toolbar h2')).toHaveText('서버 1');
  await expect(page.locator('.sys-current-reading')).toContainText('0/1대 측정');
});

test('Power Flow keeps an unsaved draft when navigation is cancelled from monitor mode', async ({ page }) => {
  await mockGpu(page, { status: route => route.fulfill({ json: {} }) });
  await page.goto(gpuOrigin + '/system/power-flow');
  await page.getByRole('button', { name: '전기 조건', exact: true }).click();
  const voltage = page.getByRole('spinbutton', { name: '전압 V' });
  await voltage.fill('230');
  await voltage.press('Tab');
  await expect(page.getByText('저장 전', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '모니터링', exact: true }).click();
  await expect(page.getByText('초안', { exact: true })).toBeVisible();
  const gpuLink = page.getByRole('link', { name: 'GPU', exact: true });

  const cancelled = page.waitForEvent('dialog').then(async dialog => {
    expect(dialog.type()).toBe('confirm');
    await dialog.dismiss();
  });
  await Promise.all([gpuLink.click(), cancelled]);
  await expect(page).toHaveURL(gpuOrigin + '/system/power-flow');
  await expect(page.getByText('초안', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: '배선 편집', exact: true }).click();
  await page.getByRole('button', { name: '전기 조건', exact: true }).click();
  await expect(voltage).toHaveValue('230');
  await page.getByRole('button', { name: '모니터링', exact: true }).click();
  const reloadCancelled = page.waitForEvent('dialog').then(async dialog => {
    expect(dialog.type()).toBe('beforeunload');
    await dialog.dismiss();
  });
  await page.evaluate(() => { setTimeout(() => location.reload(), 0); });
  await reloadCancelled;
  await expect(page.getByText('초안', { exact: true })).toBeVisible();

  const accepted = page.waitForEvent('dialog').then(dialog => dialog.accept());
  await Promise.all([gpuLink.click(), accepted]);
  await expect(page).toHaveURL(gpuOrigin + '/');
});

test('Power Flow preserves a conflicting draft and navigates after a successful save', async ({ page }) => {
  const timestamp = new Date().toISOString();
  let diagram = {
    id: 'original', name: 'Fixture diagram', version: 1, is_scenario: false,
    created_at: timestamp, updated_at: timestamp, effective_from: timestamp,
    graph: { nodes: [], edges: [], settings: { phase: 'single', voltage_v: 220, power_factor: 1, max_age_seconds: 75, max_skew_seconds: 30 } },
  };
  let scenario;
  let saves = 0;
  await mockGpu(page, { status: route => route.fulfill({ json: {} }) });
  await page.route(gpuOrigin + '/api/power-flow/**', async route => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith('/diagrams')) return route.fulfill({ json: [diagram, scenario].filter(Boolean) });
    if (path.endsWith('/original') && method === 'GET') return route.fulfill({ json: diagram });
    if (path.endsWith('/original') && method === 'PUT') {
      saves++;
      if (saves === 1) return route.fulfill({ status: 409, json: {} });
      const body = route.request().postDataJSON();
      expect(body.base_version).toBe(1);
      expect(body.graph.settings.voltage_v).toBe(230);
      diagram = { ...diagram, version: 2, graph: body.graph };
      return route.fulfill({ json: diagram });
    }
    if (path.endsWith('/original/clone')) {
      scenario = { ...diagram, id: 'scenario', name: route.request().postDataJSON().name, is_scenario: true };
      return route.fulfill({ status: 201, json: scenario });
    }
    if (path.endsWith('/state') || path.endsWith('/evaluate')) {
      const historical = new URL(route.request().url()).searchParams.has('at');
      return route.fulfill({ json: {
        version: historical ? 1 : null, at: timestamp, effective_from: timestamp,
        graph: historical ? { ...diagram.graph, settings: { ...diagram.graph.settings, voltage_v: 220 } } : null,
        nodes: {}, edges: {}, ports: {}, history_status: 'available', peaks_included: true,
      } });
    }
    return route.fallback();
  });
  await page.goto(gpuOrigin + '/system/power-flow');
  const diagramPicker = page.getByLabel('전원 배선도 선택');
  await expect(diagramPicker).toHaveValue('original');
  await expect(diagramPicker).toBeEnabled();
  const volts = page.getByRole('spinbutton', { name: '전압 V' });
  await page.getByRole('button', { name: '배선 편집', exact: true }).click();
  await page.getByRole('button', { name: '전기 조건', exact: true }).click();
  await volts.fill('230');
  await volts.press('Tab');
  await page.getByRole('button', { name: '저장', exact: true }).click();
  const dialog = page.getByRole('dialog');
  const password = dialog.getByLabel('관리자 비밀번호');
  await password.fill('fixture-password');
  await dialog.getByRole('button', { name: '저장', exact: true }).click();
  await expect(dialog.getByRole('alert')).toContainText('현재 초안은 유지됩니다');
  await expect(volts).toHaveValue('230');
  await password.fill('fixture-password');
  await dialog.getByRole('button', { name: '저장', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText('저장 전', { exact: true })).toHaveCount(0);

  await page.getByRole('button', { name: '배선 편집', exact: true }).click();
  await expect(page.getByText('v2', { exact: true })).toBeVisible();
  await page.getByLabel('과거 조회 시각').fill('2026-11-24T10:00');
  await expect(page.getByText('편집 초안은 유지됩니다.')).toBeVisible();
  await expect(page.locator('.pf-history-banner')).toContainText('과거 조회');
  await page.getByRole('button', { name: '실시간으로 돌아가기' }).click();
  await page.getByRole('button', { name: '배치안 복제', exact: true }).click();
  await password.fill('fixture-password');
  await dialog.getByRole('button', { name: '복제', exact: true }).click();
  await expect(diagramPicker).toHaveValue('scenario');

  page.on('dialog', prompt => prompt.dismiss());
  await page.getByRole('link', { name: 'GPU', exact: true }).click();
  await expect(page).toHaveURL(gpuOrigin + '/');
});

test('Storage switches servers and shows rescan progress and failure', async ({ page, context }) => {
  const sampleFile = new URL('../../apps/storage-monitor/tests/fixtures/fleet/atlas.json', import.meta.url);
  const sample = JSON.parse(await readFile(sampleFile, 'utf8'));
  const unicodePath = '/srv/atlas/vol1/보고서-été-"quote".txt';
  const undecodablePath = '/srv/atlas/vol1/bad-\uFFFD.txt';
  sample.top_files = [
    { ...sample.top_files[0], path: unicodePath },
    { ...sample.top_files[1], path: undecodablePath },
  ];
  const now = 1795507200;
  sample.selected_roots = sample.selected_roots.map(root => ({ ...root, scan_finished_unix: now - 30 }));
  const generation = 'fixture-1';
  const scanStatus = { state: 'complete', generation, observed_unix: now, updated_unix: now, last_finished_unix: now - 30 };
  const servers = ['atlas', 'borealis'].map(id => ({
    id, display_name: id, mount_count: 1, snapshot_availability: 'available', freshness: 'fresh',
    latest_pull_status: 'succeeded', latest_scan_result: 'complete', configuration_sync: 'in_sync',
    active_job: null, scan_status: scanStatus, overview_snapshot: { ...sample, hostname: id, server_id: id, scan_generation: generation },
  }));
  let state = 'idle';
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: storageOrigin });
  await page.clock.setFixedTime(new Date(now * 1000));
  await page.route(storageOrigin + '/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({ json: { authenticated: true, can_rescan: true, csrf_token: 'test' } });
    if (path === '/api/servers') return route.fulfill({ json: { data_mode: 'inventory', servers } });
    const [, , , id, endpoint] = path.split('/');
    if (endpoint === 'snapshot') return route.fulfill({ json: servers.find(server => server.id === id).overview_snapshot });
    if (endpoint === 'scan-status') return route.fulfill({ json: scanStatus });
    if (id === 'borealis' && endpoint === 'rescan') {
      state = 'running';
      return route.fulfill({ status: 202, json: { job: { id: 'job-1', state } } });
    }
    if (id === 'borealis' && endpoint === 'job') {
      return route.fulfill({ json: { job: { id: 'job-1', state, result_code: 'FIXTURE' } } });
    }
    return route.fulfill({ status: 404 });
  });
  await page.goto(storageOrigin + '/');
  const atlas = page.locator('[data-server-id="atlas"]');
  const mount = atlas.locator('.overview-mount').first();
  await expect(mount).not.toContainText(/스캔 /);
  await expect(mount.locator('.overview-mount-path')).toHaveAttribute('title', '/srv/atlas/vol1');
  await expect(atlas).not.toContainText(/화면 데이터|다음 (?:스캔|확인)|예약 정보/);
  await atlas.click();
  await expect(page).toHaveURL(/atlas/);
  await expect(page.locator('#scanLastCompleted')).toHaveText(/^화면 데이터 /);
  await expect(page.locator('#rescanBtn')).toBeEnabled();
  await page.getByRole('tab', { name: 'Top files' }).click();
  const copy = page.getByRole('button', { name: 'Copy', exact: true });
  await expect(copy.filter({ visible: true })).toHaveCount(2);
  await expect(copy.first()).toHaveAttribute('data-copy', unicodePath);
  await copy.first().click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(unicodePath);
  await expect(copy.nth(1)).toBeDisabled();
  await page.locator('#overviewBack').click();
  await page.locator('[data-server-id="borealis"]').click();
  await expect(page).toHaveURL(/borealis/);

  const rescan = page.locator('#rescanBtn');
  await rescan.click();
  await expect(rescan).toHaveAttribute('data-rescan-state', 'running');
  await expect(rescan).toHaveAttribute('title', 'Scan is running');
  state = 'failed';
  await expect(page.locator('#rescanNotice')).toContainText('Rescan failed: FIXTURE');
});
