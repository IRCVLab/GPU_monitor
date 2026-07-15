// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./dashboardPrefs.ts', import.meta.url), 'utf8');

test('dashboard layout preference persists Grid or Masonry independently of Full or Compact view', () => {
	assert.match(source, /export type DashboardLayout = 'grid' \| 'masonry';/);
	assert.match(source, /const DASHBOARD_LAYOUT_COOKIE = 'dashboardLayout';/);
	assert.match(source, /export function readDashboardLayout\(\): DashboardLayout/);
	assert.match(source, /value === 'grid' \? 'grid' : 'masonry'/);
	assert.match(source, /export const dashboardLayout: Writable<DashboardLayout> = writable\(readDashboardLayout\(\)\);/);
	assert.match(source, /dashboardLayout\.subscribe\([\s\S]*writeCookie\(DASHBOARD_LAYOUT_COOKIE, value\)/);
	assert.match(source, /export function setDashboardLayout\(value: DashboardLayout\): void/);
});
