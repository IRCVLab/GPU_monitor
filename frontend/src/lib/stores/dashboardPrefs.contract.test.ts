// @ts-nocheck
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./dashboardPrefs.ts', import.meta.url), 'utf8');

test('dashboard layout preference persists Grid or Masonry independently of Full or Compact view', () => {
	assert.match(source, /export type DashboardLayout = 'grid' \| 'masonry';/);
	assert.match(source, /const DASHBOARD_LAYOUT_COOKIE = 'dashboardLayout';/);
	assert.match(source, /export function readDashboardLayout\(\): DashboardLayout/);
	assert.match(source, /export function readDashboardLayout\(\): DashboardLayout \{[\s\S]*readCookie\(DASHBOARD_LAYOUT_COOKIE\)/);
	assert.match(source, /value === 'grid' \? 'grid' : 'masonry'/);
	assert.match(source, /export const dashboardLayout: Writable<DashboardLayout> = writable\(readDashboardLayout\(\)\);/);
	assert.match(source, /dashboardLayout\.subscribe\([\s\S]*writeCookie\(DASHBOARD_LAYOUT_COOKIE, value\)/);
	assert.match(source, /export function setDashboardLayout\(value: DashboardLayout\): void/);
});

test('dashboard width preference mirrors the live monitor framed or full-width contract', () => {
	assert.match(source, /export type DashboardLayoutWidth = 'framed' \| 'full';/);
	assert.match(source, /const DASHBOARD_LAYOUT_WIDTH_COOKIE = 'dashboardLayoutWidth';/);
	assert.match(source, /export function readDashboardLayoutWidth\(\): DashboardLayoutWidth/);
	assert.match(source, /readCookie\(DASHBOARD_LAYOUT_WIDTH_COOKIE\)/);
	assert.match(source, /value === 'full' \? 'full' : 'framed'/);
	assert.match(source, /export const dashboardLayoutWidth: Writable<DashboardLayoutWidth> = writable\(readDashboardLayoutWidth\(\)\);/);
	assert.match(source, /dashboardLayoutWidth\.subscribe\([\s\S]*writeCookie\(DASHBOARD_LAYOUT_WIDTH_COOKIE, value\)/);
	assert.match(source, /export function setDashboardLayoutWidth\(value: DashboardLayoutWidth\): void/);
});


test('dashboard view preference persists Full or Compact in the dashboardView cookie', () => {
	assert.match(source, /const DASHBOARD_VIEW_COOKIE = 'dashboardView';/);
	assert.match(source, /export function readDashboardView\(\): DashboardView/);
	assert.match(source, /readCookie\(DASHBOARD_VIEW_COOKIE\)/);
	assert.match(source, /value === 'compact' \? 'compact' : 'default'/);
	assert.match(source, /export const dashboardView: Writable<DashboardView> = writable\(readDashboardView\(\)\);/);
	assert.match(source, /dashboardView\.subscribe\([\s\S]*writeCookie\(DASHBOARD_VIEW_COOKIE, value\)/);
	assert.match(source, /export function setDashboardView\(value: DashboardView\): void/);
});

test('dashboard preferences do not persist via localStorage, backend, or cross-device sync', () => {
	assert.doesNotMatch(source, /localStorage|sessionStorage|fetch\(|navigator\.sendBeacon|WebSocket|EventSource|sync/i);
});
