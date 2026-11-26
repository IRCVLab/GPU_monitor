import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const gpuPort = process.env.MONITORING_BROWSER_GPU_PORT ?? '18773';
const storagePort = process.env.MONITORING_BROWSER_STORAGE_PORT ?? '18775';
export const gpuOrigin = 'http://127.0.0.1:' + gpuPort;
export const storageOrigin = 'http://127.0.0.1:' + storagePort;

export default defineConfig({
  testDir: '.',
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 20_000 },
  use: { reducedMotion: 'reduce' },
  webServer: [
    {
      command: 'npm run dev -- --host 127.0.0.1 --strictPort --port ' + Number(gpuPort),
      cwd: repo + 'apps/gpu-monitor/frontend',
      url: gpuOrigin,
      timeout: 60_000,
    },
    {
      command: 'python3.12 -m api.server --fixtures tests/fixtures/fleet ' + Number(storagePort),
      cwd: repo + 'apps/storage-monitor',
      url: storageOrigin + '/',
    },
  ],
});
