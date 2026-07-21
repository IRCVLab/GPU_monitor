#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const here = __dirname;
const GiB = 1024 ** 3;

function testCleanShellThemeBootstrapContract() {
  const html = fs.readFileSync(path.join(here, "index.html"), "utf8");
  const css = fs.readFileSync(path.join(here, "styles.css"), "utf8");
  const app = fs.readFileSync(path.join(here, "app.js"), "utf8");

  const themeScript = html.match(/<script>\s*\(\(\) => \{[\s\S]*?themeMode=[\s\S]*?document\.documentElement\.classList\.add\(mode\);[\s\S]*?dataset\.material = 'liquid';[\s\S]*?\}\)\(\);\s*<\/script>/);
  assert(themeScript, "index must include an early inline themeMode bootstrap that applies html.light/html.dark and data-material=liquid");
  assert(html.indexOf(themeScript[0]) < html.indexOf('<link rel="stylesheet" href="styles.css">'), "theme bootstrap must run before styles.css can paint");
  assert(/<a\b(?=[^>]*class="suite-nav-link")(?=[^>]*href="http:\/\/127\.0\.0\.1:15173\/")(?=[^>]*aria-label="GPU Monitor")(?![^>]*target=)[^>]*>[\s\S]*?suite-nav-label-full">GPU Monitor<\/span>[\s\S]*?<\/a>/.test(html), "header must include an accessible same-tab GPU Monitor suite link");
  assert(/<button\b(?=[^>]*id="themeModeButton")(?=[^>]*class="theme-mode-button")(?=[^>]*aria-label="Toggle light and dark theme")(?=[^>]*aria-pressed=)[^>]*>[\s\S]*<svg[\s\S]*<\/button>/.test(html), "header must include an accessible circular theme mode button with inline SVG icons");
  assert(!html.includes("고정 순서 서버 저장소 개요"), "old Storage Viz subtitle copy must be absent from the redesigned shell");
  assert(/html\.light\s*\{[\s\S]*--bg:\s*#f4f5f7;[\s\S]*--surface:\s*#ffffff;[\s\S]*--accent:\s*#297cef;/.test(css), "light mode must map Clean semantic tokens to Storage variables");
  assert(/html\.dark\s*\{[\s\S]*--bg:\s*#090b0f;[\s\S]*--surface:\s*#13161b;[\s\S]*--accent:\s*#3a8cff;/.test(css), "dark mode must map Clean semantic tokens to Storage variables");
  assert(/html\[data-material='liquid'\]\s*\{[\s\S]*--material-surface-alpha:\s*0\.94;[\s\S]*--material-blur:\s*6px;[\s\S]*--material-control-radius:\s*0\.7rem;/.test(css), "liquid material values must be copied into Storage CSS");
  assert(/transition:[^;]*(?:background|background-color|color|border-color|box-shadow)[^;]*(?:\.16s|160ms|\.18s|180ms|\.2s|200ms|\.22s|220ms)/.test(css), "themeable surfaces must use 160-220ms color/surface transitions");
  assert(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{[\s\S]*transition:\s*none\s*!important/.test(css), "reduced motion must disable color/surface transitions");
  assert(/function\s+applyStoredThemeMode\s*\(/.test(app), "app.js must produce applyStoredThemeMode()");
  assert(/function\s+toggleThemeMode\s*\(/.test(app), "app.js must produce toggleThemeMode()");
  assert(/document\.startViewTransition/.test(app), "theme toggle must use the same native view-transition reveal as GPU Monitor when supported");
  assert(/getBoundingClientRect\(\)/.test(app), "theme reveal must originate from the actual theme button center");
  assert(/Math\.hypot\(/.test(app), "theme reveal radius must cover the farthest viewport corner");
  assert(/--theme-reveal-x/.test(app) && /--theme-reveal-y/.test(app) && /--theme-reveal-radius/.test(app), "theme reveal coordinates must be passed to CSS");
  assert(/::view-transition-old\(root\)/.test(css), "old theme snapshot must remain visible below the reveal");
  assert(/::view-transition-new\(root\)/.test(css), "new theme snapshot must animate as the reveal layer");
  assert(/@keyframes\s+theme-root-reveal[\s\S]*clip-path:\s*circle\(0px at var\(--theme-reveal-x\) var\(--theme-reveal-y\)\)[\s\S]*clip-path:\s*circle\(var\(--theme-reveal-radius\) at var\(--theme-reveal-x\) var\(--theme-reveal-y\)\)/.test(css), "new theme must reveal in a circle from the button center");
}

function testOverviewDoesNotBlockOnTheDetailOnlyChartLibrary() {
  const html = fs.readFileSync(path.join(here, "index.html"), "utf8");
  const app = fs.readFileSync(path.join(here, "app.js"), "utf8");
  assert(!/<script[^>]+src="echarts\.min\.js"/.test(html), "the 1 MB detail chart library must not block overview HTML parsing");
  assert(/function\s+ensureEchartsLoaded\s*\(/.test(app), "detail navigation must own lazy chart loading");
  assert(/script\.src\s*=\s*"echarts\.min\.js"/.test(app), "lazy chart loading must use the local vendored asset");
  assert(/currentTab === "users"[\s\S]*renderUsersWhenReady\(\)/.test(app), "the Users tab must wait for the lazy chart dependency before rendering");
  const renderAll = app.slice(app.indexOf("function renderAll"), app.indexOf("function showOverviewError"));
  assert(/currentTab === "users"[\s\S]*renderUsersWhenReady\(\)/.test(renderAll), "a direct Users deep-link must request the chart after detail data arrives");
}


function testHostManifest() {
  const manifestPath = path.join(here, "..", "data", "hosts.json");
  const hosts = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  assert(Array.isArray(hosts), "hosts manifest must be an array");
  assert(hosts.some(h => h.id === "hinton" && h.file === "hinton"), "hinton host entry must exist");
  assert.deepStrictEqual(hosts.map(h => h.id), ["hinton", "atlas", "orion", "zeus"], "sample host order must stay authoritative and unsorted");
  for (const h of hosts) {
    assert(/^[A-Za-z0-9._-]+$/.test(h.id), `host id is safe: ${h.id}`);
    assert(h.label && h.file, `host has label and file: ${JSON.stringify(h)}`);
  }
}

function testHostManifestHelpers() {
  const { normalizeHosts, safeServerId } = require("./data-client.js");
  const hosts = normalizeHosts([
    { id: "hinton", label: "Hinton", file: "hinton", default: true },
    { id: "../bad", label: "bad", file: "../bad" },
    { id: "lecun", file: "lecun" },
  ]);
  assert.deepStrictEqual(hosts.map(h => h.id), ["hinton", "lecun"]);
  assert.strictEqual(hosts[0].label, "Hinton");
  assert.strictEqual(hosts[1].label, "lecun");
  assert.strictEqual(hosts[0].default, true);
  assert.deepStrictEqual(normalizeHosts([]), [{ id: "hinton", label: "hinton", file: "hinton", default: true }]);

  const ordered = normalizeHosts([
    { id: "atlas", label: "Atlas", file: "atlas" },
    { id: "beta", label: "Beta", file: "beta", default: true },
    { id: "cedar", label: "Cedar", file: "cedar" },
  ]);
  assert.deepStrictEqual(ordered.map(h => h.id), ["atlas", "beta", "cedar"], "manifest order must remain exact even when default:true appears in the middle");
  assert.strictEqual(ordered[1].default, true, "default metadata must remain attached to the original row");
  assert.strictEqual(safeServerId("alpha-1"), "alpha-1", "safe server ids should pass unchanged");
  assert.throws(() => safeServerId("."), /invalid server id/, "single-dot server ids must be rejected to match the backend");
  assert.throws(() => safeServerId(".."), /invalid server id/, "double-dot server ids must be rejected to match the backend");

  const sampleHosts = normalizeHosts([
    { id: "hinton", label: "Hinton", file: "hinton", sample_data: true },
    { id: "inventory", label: "Inventory", file: "inventory", sample_data: false },
    { id: "missing-flag", label: "Missing", file: "missing" },
  ]);
  assert.strictEqual(sampleHosts[0].sample_data, true, "authoritative sample_data:true metadata must be preserved for static sample-mode decisions");
  assert.strictEqual(Object.prototype.hasOwnProperty.call(sampleHosts[1], "sample_data"), false, "non-true sample flags must not be promoted to sample metadata");
  assert.strictEqual(Object.prototype.hasOwnProperty.call(sampleHosts[2], "sample_data"), false, "missing sample flags must remain absent instead of inferred");
}

async function testLoadServerSummariesReturnsNormalizedEnvelope() {
  const { loadServerSummaries } = require("./data-client.js");
  const originalFetch = global.fetch;
  try {
    global.fetch = async (url) => {
      assert.strictEqual(url, "/api/servers");
      return {
        ok: true,
        json: async () => ({
          data_mode: "sample",
          servers: [
            { id: "hinton", display_name: "hinton" },
            { id: "atlas", display_name: "atlas" },
          ],
        }),
      };
    };
    const envelope = await loadServerSummaries();
    assert.deepStrictEqual(envelope, {
      data_mode: "sample",
      servers: [
        { id: "hinton", display_name: "hinton" },
        { id: "atlas", display_name: "atlas" },
      ],
    }, "loadServerSummaries must expose the server array and data_mode metadata together");

    global.fetch = async () => ({
      ok: true,
      json: async () => [{ id: "legacy" }],
    });
    assert.deepStrictEqual(await loadServerSummaries(), { data_mode: "inventory", servers: [{ id: "legacy" }] }, "legacy array responses must normalize to an inventory envelope");
  } finally {
    global.fetch = originalFetch;
  }
}

async function testOverviewSnapshotFetchPreservesInventoryOrderAndIsolatesFailures() {
  const { loadOrderedSnapshotsForOverview } = require("./data-client.js");
  const summaries = [
    { id: "beta-2", display_name: "beta", order: 20 },
    { id: "alpha-1", display_name: "alpha", order: 10 },
    { id: "gamma-3", display_name: "gamma", order: 30 },
  ];
  const events = [];
  const streamed = [];
  const result = await loadOrderedSnapshotsForOverview(summaries, async (serverId) => {
    if (serverId === "beta-2") {
      await new Promise(resolve => setTimeout(resolve, 25));
      events.push(serverId);
      return { server_id: serverId, mounts: [{ path: "/data", df_use_pct: 81, df_avail: 700 * GiB }] };
    }
    if (serverId === "alpha-1") {
      await new Promise(resolve => setTimeout(resolve, 5));
      events.push(serverId);
      const err = new Error("404");
      err.status = 404;
      throw err;
    }
    events.push(serverId);
    return { server_id: serverId, mounts: [{ path: "/archive", df_use_pct: 32, df_avail: 2 * GiB }] };
  }, entry => streamed.push(entry.id));

  assert.notDeepStrictEqual(events, result.map(item => item.id), "responses may resolve out of order in the fetch layer");
  assert.deepStrictEqual(streamed, events, "completed snapshots must stream to the overview as each server resolves");
  assert.deepStrictEqual(result.map(item => item.id), ["beta-2", "alpha-1", "gamma-3"], "returned rows must preserve inventory order");
  assert.strictEqual(result[0].snapshot.server_id, "beta-2");
  assert.strictEqual(result[1].snapshot, null, "snapshot failures must be isolated into the row result");
  assert(result[1].error instanceof Error, "snapshot failures must be surfaced for rendering decisions");
  assert.strictEqual(result[2].snapshot.server_id, "gamma-3");
}

function makeSummary(overrides = {}) {
  return Object.assign({
    id: "alpha-1",
    display_name: "alpha",
    order: 1,
    mount_count: 1,
    snapshot_availability: "available",
    freshness: "fresh",
    latest_pull_status: "succeeded",
    latest_scan_result: "complete",
    configuration_sync: "in_sync",
    active_job: null,
  }, overrides);
}

function makeSnapshot(mountOverrides = []) {
  return {
    server_id: "alpha-1",
    mounts: mountOverrides.length ? mountOverrides : [
      { path: "/data", df_total: 5000 * GiB, df_used: 4050 * GiB, df_use_pct: 81, df_avail: 900 * GiB },
      { path: "/archive", df_total: 8000 * GiB, df_used: 7440 * GiB, df_use_pct: 93, df_avail: 600 * GiB },
    ],
  };
}

function testOverviewCapacityThresholdsAndPrecedence() {
  const {
    DEFAULT_CAPACITY_THRESHOLDS,
    pressureLevel,
    derivePrimaryStatus,
    statusPresentation,
    buildOverviewServer,
  } = require("./overview.js");

  assert.deepStrictEqual(DEFAULT_CAPACITY_THRESHOLDS, {
    warning_used_pct: 80,
    critical_used_pct: 92,
    warning_free_bytes: 549755813888,
    critical_free_bytes: 137438953472,
  }, "overview thresholds must match the centralized inventory defaults");

  assert.strictEqual(pressureLevel(79, DEFAULT_CAPACITY_THRESHOLDS.warning_free_bytes + 1), "normal");
  assert.strictEqual(pressureLevel(80, 10 ** 12), "warning");
  assert.strictEqual(pressureLevel(1, DEFAULT_CAPACITY_THRESHOLDS.warning_free_bytes), "warning");
  assert.strictEqual(pressureLevel(92, 10 ** 12), "critical");
  assert.strictEqual(pressureLevel(1, DEFAULT_CAPACITY_THRESHOLDS.critical_free_bytes), "critical");
  assert.strictEqual(pressureLevel(null, 10 ** 12), "unknown", "missing utilization must not be promoted to a capacity-pressure warning");
  assert.strictEqual(pressureLevel(10, null), "unknown", "missing free bytes must not be promoted to a false critical state");

  const precedenceCases = [
    [makeSummary({ snapshot_availability: "absent", latest_pull_status: "not_installed" }), null, null, "agent_missing"],
    [makeSummary({ snapshot_availability: "absent", latest_pull_status: "succeeded" }), null, null, "snapshot_absent"],
    [makeSummary({ latest_pull_status: "unreachable" }), makeSnapshot(), null, "pull_unreachable"],
    [makeSummary({ latest_pull_status: "invalid_snapshot" }), makeSnapshot(), null, "pull_invalid"],
    [makeSummary({ latest_scan_result: "failed" }), makeSnapshot(), null, "scan_failed"],
    [makeSummary({ configuration_sync: "drifted" }), makeSnapshot(), null, "config_drift"],
    [makeSummary({ latest_scan_result: "partial" }), makeSnapshot(), null, "partial_scan"],
    [makeSummary({ freshness: "stale" }), makeSnapshot(), null, "stale_snapshot"],
    [makeSummary({ active_job: { id: "job-1", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 950 * GiB, df_use_pct: 95, df_avail: 900 * GiB }]), null, "active_scan"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 950 * GiB, df_use_pct: 95, df_avail: 900 * GiB }]), null, "pressure_critical"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 100 * GiB, df_use_pct: 10, df_avail: 20 * GiB }]), null, "pressure_critical"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 810 * GiB, df_use_pct: 81, df_avail: 900 * GiB }]), null, "pressure_warning"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 550 * GiB, df_use_pct: 55, df_avail: 900 * GiB }]), null, "normal"],
    [makeSummary(), null, new Error("snapshot load failed"), "snapshot_load_failed"],
    [makeSummary({ active_job: { id: "job-9", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }), null, new Error("snapshot load failed"), "snapshot_load_failed"],
    [makeSummary({ freshness: "stale" }), null, new Error("snapshot load failed"), "stale_snapshot"],
    [makeSummary({ latest_pull_status: "unreachable" }), null, new Error("snapshot load failed"), "pull_unreachable"],
  ];
  for (const [summary, snapshot, error, expectedCode] of precedenceCases) {
    const status = derivePrimaryStatus(summary, snapshot, DEFAULT_CAPACITY_THRESHOLDS, error);
    assert.strictEqual(status.code, expectedCode, `expected ${expectedCode} for ${JSON.stringify(summary)}`);
  }

  const severe = statusPresentation("scan_failed");
  assert.strictEqual(typeof severe.shape, "string");
  assert.ok(severe.shape.length > 0, "exceptional states must expose a visible shape cue");
  assert.ok(/[가-힣]/.test(severe.label), "exceptional states must use concise Korean copy");

  const loadFailure = statusPresentation("snapshot_load_failed");
  assert.ok(loadFailure.shape.length > 0, "client load failure must expose a visible shape cue");
  assert.ok(/[가-힣]/.test(loadFailure.label), "client load failure must expose a concise Korean label");

  const model = buildOverviewServer(
    makeSummary({ latest_scan_result: "failed", active_job: { id: "job-2", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }),
    makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 930 * GiB, df_use_pct: 93, df_avail: 1024 * GiB }]),
    DEFAULT_CAPACITY_THRESHOLDS,
  );
  assert.strictEqual(model.primaryStatus.code, "scan_failed", "higher-priority operational state must win over capacity pressure");
  assert.strictEqual(model.secondaryStatus.code, "active_scan", "active scan must remain available as a secondary cue");
  assert.strictEqual(model.mounts.length, 1, "capacity bars must remain present when an operational status wins");
  assert.strictEqual(model.mounts[0].pressure, "critical");

  const loadFailureModel = buildOverviewServer(
    makeSummary({ active_job: { id: "job-3", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }),
    null,
    DEFAULT_CAPACITY_THRESHOLDS,
    new Error("snapshot load failed"),
  );
  assert.strictEqual(loadFailureModel.primaryStatus.code, "snapshot_load_failed", "client snapshot failure must be visible when summary state would otherwise be healthy/active/capacity-only");
  assert.strictEqual(loadFailureModel.secondaryStatus.code, "active_scan", "active scan may remain visible as a secondary cue beneath client load failure");
}


function makeCapacitySnapshot(overrides = {}) {
  return Object.assign({
    server_id: "alpha-1",
    selected_roots: [
      { mount_id: "rootfs", capacity_id: "dev-8-1", major_minor: "8:1", storage_media: "ssd", storage_media_confidence: "resolved" },
      { mount_id: "data", capacity_id: "dev-8-16", major_minor: "8:16", block_media: "hdd", block_media_confidence: "resolved" },
      { mount_id: "data", capacity_id: "dev-8-99", major_minor: "8:99", storage_media: "ssd", storage_media_confidence: "guessed" },
      { mount_id: "legacy", major_minor: "9:42", storage_media: "mixed", storage_media_confidence: "inferred" },
      { mount_id: "bad-zero", major_minor: "0:0", storage_media: "unknown", storage_media_confidence: "unresolved" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40 },
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      { mount_id: "legacy", path: "/legacy", df_total: 3000, df_used: 1200, df_avail: 1800, df_use_pct: 40 },
    ],
  }, overrides);
}

function testActionableMountFilteringPreservesNonBootOrder() {
  const { isActionableMountPath, summarizeMounts, buildOverviewServer } = require("./overview.js");
  assert.strictEqual(typeof isActionableMountPath, "function", "actionable mount path helper must be exported");
  assert.strictEqual(isActionableMountPath("/boot"), false, "plain /boot must be omitted from compact overview");
  assert.strictEqual(isActionableMountPath("/boot/"), false, "trailing slash /boot must be normalized before filtering");
  assert.strictEqual(isActionableMountPath("/boot/efi"), false, "nested /boot/efi must be omitted from compact overview");
  assert.strictEqual(isActionableMountPath("/bootloader"), true, "non-/boot prefix collisions must remain actionable");

  const snapshot = makeCapacitySnapshot({
    selected_roots: [
      { mount_id: "boot", capacity_id: "dev-8-1", storage_media: "ssd" },
      { mount_id: "home", capacity_id: "dev-8-2", storage_media: "ssd" },
      { mount_id: "efi", capacity_id: "dev-8-3", storage_media: "ssd" },
      { mount_id: "data", capacity_id: "dev-8-4", storage_media: "hdd" },
      { mount_id: "bootloader", capacity_id: "dev-8-5", storage_media: "mixed" },
    ],
    mounts: [
      { mount_id: "boot", path: "/boot", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 },
      { mount_id: "home", path: "/home", df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40 },
      { mount_id: "efi", path: "/boot/efi", df_total: 200, df_used: 50, df_avail: 150, df_use_pct: 25 },
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      { mount_id: "bootloader", path: "/bootloader", df_total: 300, df_used: 120, df_avail: 180, df_use_pct: 40 },
    ],
  });
  assert.deepStrictEqual(summarizeMounts(snapshot).map(m => m.path), ["/home", "/data", "/bootloader"], "summaries must omit /boot mounts while preserving non-boot input order");
  const row = buildOverviewServer(makeSummary({ mount_count: 5 }), snapshot);
  assert.strictEqual(row.mountCount, 3, "server row mount count must use actionable mount count after boot filtering");
}

function testOnlyBootMountsProduceZeroActionableMountCount() {
  const { buildOverviewServer } = require("./overview.js");
  const row = buildOverviewServer(
    makeSummary({ id: "boot-only", display_name: "boot-only", mount_count: 2 }),
    {
      server_id: "boot-only",
      selected_roots: [
        { mount_id: "boot", capacity_id: "dev-8-1", storage_media: "ssd" },
        { mount_id: "efi", capacity_id: "dev-8-2", storage_media: "ssd" },
      ],
      mounts: [
        { mount_id: "boot", path: "/boot", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 },
        { mount_id: "efi", path: "/boot/efi", df_total: 200, df_used: 20, df_avail: 180, df_use_pct: 10 },
      ],
    },
  );
  assert.strictEqual(row.mounts.length, 0, "boot-only snapshots must summarize to zero actionable mounts");
  assert.strictEqual(row.mountCount, 0, "boot-only snapshots must render zero actionable mounts instead of falling back to legacy summary count");
}

function testOverviewIdentityAwareMountModelAndAggregate() {
  const {
    selectedRootByMountId,
    summarizeMounts,
    aggregateMountCapacity,
    buildOverviewAggregate,
    buildOverviewRows,
  } = require("./overview.js");

  assert.strictEqual(typeof selectedRootByMountId, "function", "selected-root lookup helper must be exported");
  assert.strictEqual(typeof summarizeMounts, "function", "mount summarizer must stay exported");
  assert.strictEqual(typeof aggregateMountCapacity, "function", "server aggregate helper must be exported");
  assert.strictEqual(typeof buildOverviewAggregate, "function", "page aggregate helper must be exported");

  const snapshot = makeCapacitySnapshot();
  const byMount = selectedRootByMountId(snapshot);
  assert.strictEqual(byMount.get("data").capacity_id, "dev-8-16", "duplicate selected roots must link to the first matching mount_id");

  const mounts = summarizeMounts(snapshot);
  assert.deepStrictEqual(mounts.map(m => m.path), ["/", "/data", "/legacy"], "mount summaries must preserve snapshot mount order exactly");
  assert.deepStrictEqual({
    usedBytes: mounts[1].usedBytes,
    totalBytes: mounts[1].totalBytes,
    availableBytes: mounts[1].availableBytes,
    usedPct: mounts[1].usedPct,
    media: mounts[1].media,
    mediaConfidence: mounts[1].mediaConfidence,
    identity: mounts[1].identity,
  }, {
    usedBytes: 1500,
    totalBytes: 2000,
    availableBytes: 500,
    usedPct: 75,
    media: "hdd",
    mediaConfidence: "resolved",
    identity: { kind: "capacity_id", value: "dev-8-16", key: "capacity_id:dev-8-16" },
  }, "mount summaries must expose linked capacity/media identity metadata");
  assert.deepStrictEqual(mounts[2].identity, { kind: "major_minor", value: "9:42", key: "major_minor:9:42" }, "legacy nonzero major_minor must be used when capacity_id is absent");

  const exactDuplicate = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    selected_roots: [
      { mount_id: "data", capacity_id: "dev-8-16" },
      { mount_id: "data-bind", capacity_id: "dev-8-16" },
    ],
    mounts: [
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      { mount_id: "data-bind", path: "/data-bind", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
    ],
  })));
  assert.strictEqual(exactDuplicate.totalBytes, 2000, "exact duplicate capacity identities within a server must count once");
  assert.strictEqual(exactDuplicate.availableBytes, 500, "exact duplicate capacity identities within a server must count available bytes once");
  assert.strictEqual(exactDuplicate.isPartial, false, "consistent exact duplicates must not make the aggregate partial");

  const exactDuplicateRow = buildOverviewRows([
    { id: "alpha-1", display_name: "alpha", order: 1, mount_count: 2 },
  ], [
    { id: "alpha-1", snapshot: makeCapacitySnapshot({
      selected_roots: [
        { mount_id: "data", capacity_id: "dev-8-16" },
        { mount_id: "data-bind", capacity_id: "dev-8-16" },
      ],
      mounts: [
        { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
        { mount_id: "data-bind", path: "/data-bind", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      ],
    }) },
  ])[0];
  assert.strictEqual(exactDuplicateRow.aggregate.availableLabel, "500 B", "server aggregate label must expose deduped available capacity");
  assert.strictEqual(exactDuplicateRow.totalAvailableBytes, 500, "server header free byte model must derive from the identity-aware aggregate");
  assert.strictEqual(exactDuplicateRow.totalAvailableLabel, "500 B", "server header free label must derive from the identity-aware aggregate");

  const inconsistentDuplicate = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    mounts: [
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      { mount_id: "data", path: "/data-stale", df_total: 3000, df_used: 1500, df_avail: 1500, df_use_pct: 50 },
    ],
  })));
  assert.deepStrictEqual({
    totalBytes: inconsistentDuplicate.totalBytes,
    usedBytes: inconsistentDuplicate.usedBytes,
    availableBytes: inconsistentDuplicate.availableBytes,
    isPartial: inconsistentDuplicate.isPartial,
    excludedMountCount: inconsistentDuplicate.excludedMountCount,
  }, { totalBytes: 0, usedBytes: 0, availableBytes: 0, isPartial: true, excludedMountCount: 2 }, "any inconsistent duplicate must exclude that identity's entire contribution");
  assert(inconsistentDuplicate.partialReasons.some(reason => reason.includes("dev-8-16") && reason.includes("2개 마운트 제외")), "inconsistent duplicate partial reason must identify the excluded identity and count");

  const unresolved = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "missing", storage_media: "unknown", storage_media_confidence: "unresolved" }],
    mounts: [{ mount_id: "missing", path: "/missing", df_total: 1000, df_used: 100, df_avail: 900, df_use_pct: 10 }],
  })));
  assert.strictEqual(unresolved.excludedMountCount, 1, "unresolved identities must increment excludedMountCount");
  assert.deepStrictEqual(unresolved.partialReasons, ["/missing: unresolved capacity identity, 1개 마운트 제외"], "unresolved identities must add a precise partial reason");
  assert.strictEqual(unresolved.totalLabel, "—", "all-unresolved partial totals must not imply known zero capacity");
  assert.strictEqual(unresolved.utilizationLabel, "—", "all-unresolved partial utilization must stay unknown when no known capacity remains");

  const invalidIds = summarizeMounts(makeCapacitySnapshot({
    selected_roots: [
      { mount_id: "zero", major_minor: "0:0", storage_media: "ssd", storage_media_confidence: "resolved" },
      { mount_id: "invalid", major_minor: "8:x", storage_media: "ssd", storage_media_confidence: "resolved" },
      { mount_id: "empty-capacity", capacity_id: "", major_minor: "8:88", storage_media: "ssd", storage_media_confidence: "resolved" },
      { mount_id: "invalid-capacity", capacity_id: "not-canonical", major_minor: "8:89", storage_media: "ssd", storage_media_confidence: "resolved" },
    ],
    mounts: [
      { mount_id: "zero", path: "/zero", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 },
      { mount_id: "invalid", path: "/invalid", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 },
      { mount_id: "empty-capacity", path: "/empty", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 },
      { mount_id: "invalid-capacity", path: "/invalid-capacity", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 },
    ],
  }));
  assert.deepStrictEqual(invalidIds.map(m => m.identity), [null, null, null, null], "zero, malformed, empty, and noncanonical identities must remain unresolved instead of falling back ambiguously");

  const rows = buildOverviewRows([
    { id: "beta-2", display_name: "beta", order: 20, mount_count: 1 },
    { id: "alpha-1", display_name: "alpha", order: 10, mount_count: 2 },
  ], [
    { id: "beta-2", snapshot: makeCapacitySnapshot({ server_id: "beta-2", selected_roots: [{ mount_id: "same", capacity_id: "dev-8-1" }], mounts: [{ mount_id: "same", path: "/beta", df_total: 1000, df_used: 100, df_avail: 900, df_use_pct: 10 }] }) },
    { id: "alpha-1", snapshot: makeCapacitySnapshot({ server_id: "alpha-1", selected_roots: [{ mount_id: "same", capacity_id: "dev-8-1" }], mounts: [{ mount_id: "same", path: "/alpha", df_total: 2000, df_used: 500, df_avail: 1500, df_use_pct: 25 }] }) },
  ]);
  assert.deepStrictEqual(rows.map(row => row.id), ["beta-2", "alpha-1"], "overview rows must preserve manifest/server order exactly");
  assert.deepStrictEqual(rows.map(row => row.mounts.map(m => m.path)), [["/beta"], ["/alpha"]], "overview rows must preserve mount order exactly");

  const pageAggregate = buildOverviewAggregate(rows);
  assert.deepStrictEqual({
    isPartial: pageAggregate.isPartial,
    excludedMountCount: pageAggregate.excludedMountCount,
    totalBytes: pageAggregate.totalBytes,
    usedBytes: pageAggregate.usedBytes,
    availableBytes: pageAggregate.availableBytes,
    totalLabel: pageAggregate.totalLabel,
    usedLabel: pageAggregate.usedLabel,
    availableLabel: pageAggregate.availableLabel,
    utilizationLabel: pageAggregate.utilizationLabel,
  }, {
    isPartial: false,
    excludedMountCount: 0,
    totalBytes: 3000,
    usedBytes: 600,
    availableBytes: 2400,
    totalLabel: "2.93 KB",
    usedLabel: "600 B",
    availableLabel: "2.34 KB",
    utilizationLabel: "20%",
  }, "page-level identities must be namespaced by server id so identical device ids on different servers do not collide");
}


function testOverviewUnknownCapacityLabelsDoNotImplyZero() {
  const { buildOverviewServer, buildOverviewAggregate, summarizeMounts, aggregateMountCapacity } = require("./overview.js");

  const missingSnapshotRow = buildOverviewServer(makeSummary({ id: "missing-1", display_name: "missing" }), null);
  assert.deepStrictEqual({
    isPartial: missingSnapshotRow.aggregate.isPartial,
    excludedMountCount: missingSnapshotRow.aggregate.excludedMountCount,
    totalLabel: missingSnapshotRow.aggregate.totalLabel,
    usedLabel: missingSnapshotRow.aggregate.usedLabel,
    availableLabel: missingSnapshotRow.aggregate.availableLabel,
    utilizationLabel: missingSnapshotRow.aggregate.utilizationLabel,
  }, {
    isPartial: true,
    excludedMountCount: 0,
    totalLabel: "—",
    usedLabel: "—",
    availableLabel: "—",
    utilizationLabel: "—",
  }, "missing snapshots must be partial/unknown and must never render exact 0 B labels");

  const noMounts = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({ mounts: [] })));
  assert.strictEqual(noMounts.isPartial, true, "empty mount lists have no known capacity identities and must be degraded/unknown");
  assert.strictEqual(noMounts.totalLabel, "—", "empty mount lists must not imply exact zero capacity");

  const allUnresolved = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "mystery" }],
    mounts: [{ mount_id: "mystery", path: "/mystery", df_total: 1000, df_used: 100, df_avail: 900, df_use_pct: 10 }],
  })));
  assert.deepStrictEqual({
    isPartial: allUnresolved.isPartial,
    excludedMountCount: allUnresolved.excludedMountCount,
    totalLabel: allUnresolved.totalLabel,
    usedLabel: allUnresolved.usedLabel,
    availableLabel: allUnresolved.availableLabel,
    utilizationLabel: allUnresolved.utilizationLabel,
  }, {
    isPartial: true,
    excludedMountCount: 1,
    totalLabel: "—",
    usedLabel: "—",
    availableLabel: "—",
    utilizationLabel: "—",
  }, "all-excluded aggregates must be unknown, not known-zero");

  const knownPlusExcluded = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    selected_roots: [
      { mount_id: "known", capacity_id: "dev-8-16" },
      { mount_id: "unknown" },
    ],
    mounts: [
      { mount_id: "known", path: "/known", df_total: 2048, df_used: 1024, df_avail: 1024, df_use_pct: 50 },
      { mount_id: "unknown", path: "/unknown", df_total: 4096, df_used: 1024, df_avail: 3072, df_use_pct: 25 },
    ],
  })));
  assert.strictEqual(knownPlusExcluded.totalLabel, "확인된 용량 ≥ 2.00 KB", "known-plus-excluded partial aggregate must label only the known lower bound");
  assert.strictEqual(knownPlusExcluded.utilizationLabel, "확인된 범위 50%", "known-plus-excluded utilization must remain known-only partial language");

  const actualZero = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "zero-cap", capacity_id: "dev-1-0" }],
    mounts: [{ mount_id: "zero-cap", path: "/zero-cap", df_total: 0, df_used: 0, df_avail: 0, df_use_pct: 0 }],
  })));
  assert.deepStrictEqual({
    isPartial: actualZero.isPartial,
    totalLabel: actualZero.totalLabel,
    usedLabel: actualZero.usedLabel,
    availableLabel: actualZero.availableLabel,
    utilizationLabel: actualZero.utilizationLabel,
  }, { isPartial: false, totalLabel: "0 B", usedLabel: "0 B", availableLabel: "0 B", utilizationLabel: "—" }, "validated explicit zero-capacity records may remain exact zero when schema numbers truly say zero");

  const page = buildOverviewAggregate([
    buildOverviewServer(makeSummary({ id: "unavailable", display_name: "unavailable" }), null),
    buildOverviewServer(makeSummary({ id: "known", display_name: "known" }), makeCapacitySnapshot({
      server_id: "known",
      selected_roots: [{ mount_id: "known", capacity_id: "dev-8-16" }],
      mounts: [{ mount_id: "known", path: "/known", df_total: 2048, df_used: 1024, df_avail: 1024, df_use_pct: 50 }],
    })),
  ]);
  assert.strictEqual(page.isPartial, true, "page aggregate containing an unavailable server must be partial");
  assert.strictEqual(page.totalLabel, "확인된 용량 ≥ 2.00 KB", "page aggregate with unavailable server must show known-only lower-bound capacity");
  assert.strictEqual(page.utilizationLabel, "확인된 범위 50%", "page aggregate with unavailable server must not imply unavailable capacity is included");
}

function testUnknownMountCapacityRemainsNeutralAndHonest() {
  const { buildOverviewServer, summarizeMounts, pressureLevel } = require("./overview.js");

  const missingAvailable = summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "unknown-free", capacity_id: "dev-8-16", storage_media: "ssd" }],
    mounts: [{ mount_id: "unknown-free", path: "/unknown-free", df_total: 2048, df_used: 1024, df_use_pct: 50 }],
  }))[0];
  assert.deepStrictEqual({
    availableBytes: missingAvailable.availableBytes,
    freeBytes: missingAvailable.freeBytes,
    pressure: missingAvailable.pressure,
    pressureLabel: missingAvailable.pressureLabel,
    freeText: missingAvailable.freeText,
  }, {
    availableBytes: null,
    freeBytes: null,
    pressure: "unknown",
    pressureLabel: "미확인",
    freeText: "여유 미확인",
  }, "missing df_avail must stay unknown instead of becoming 0 B critical");

  const invalidNumbers = summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "invalid-cap", capacity_id: "dev-8-16", storage_media: "hdd" }],
    mounts: [{ mount_id: "invalid-cap", path: "/invalid-cap", df_total: "2048", df_used: -1, df_avail: Infinity, df_use_pct: 75 }],
  }))[0];
  assert.deepStrictEqual({
    totalBytes: invalidNumbers.totalBytes,
    usedBytes: invalidNumbers.usedBytes,
    availableBytes: invalidNumbers.availableBytes,
    freeBytes: invalidNumbers.freeBytes,
    usedPct: invalidNumbers.usedPct,
    usedTotalText: invalidNumbers.usedTotalText,
    freeText: invalidNumbers.freeText,
    pressure: invalidNumbers.pressure,
  }, {
    totalBytes: null,
    usedBytes: null,
    availableBytes: null,
    freeBytes: null,
    usedPct: null,
    usedTotalText: "— / —",
    freeText: "여유 미확인",
    pressure: "unknown",
  }, "invalid capacity numbers must render as unknown labels and neutral pressure");

  assert.strictEqual(pressureLevel(95, null), "unknown", "even a high percent must not promote when required free capacity is unavailable");
  const unknownOnlyRow = buildOverviewServer(
    makeSummary({ id: "unknown-only", display_name: "unknown-only" }),
    makeCapacitySnapshot({
      server_id: "unknown-only",
      selected_roots: [{ mount_id: "unknown-free", capacity_id: "dev-8-16", storage_media: "ssd" }],
      mounts: [{ mount_id: "unknown-free", path: "/unknown-free", df_total: 2048, df_used: 1024, df_use_pct: 95 }],
    }),
  );
  assert.strictEqual(unknownOnlyRow.primaryStatus.code, "normal", "unknown capacity pressure must be ignored for warning/critical primary status promotion");
  assert.strictEqual(unknownOnlyRow.totalAvailableLabel, "여유 미확인", "server subtotal metadata must not imply 0 B free when all mount free capacity is unknown");

  const mixedRow = buildOverviewServer(
    makeSummary({ id: "mixed", display_name: "mixed" }),
    makeCapacitySnapshot({
      server_id: "mixed",
      selected_roots: [
        { mount_id: "unknown-free", capacity_id: "dev-8-16", storage_media: "ssd" },
        { mount_id: "known-warning", capacity_id: "dev-8-32", storage_media: "hdd" },
      ],
      mounts: [
        { mount_id: "unknown-free", path: "/unknown-free", df_total: 2048, df_used: 1024, df_use_pct: 95 },
        { mount_id: "known-warning", path: "/known-warning", df_total: 4096 * GiB, df_used: 3500 * GiB, df_avail: 596 * GiB, df_use_pct: 85 },
      ],
    }),
  );
  assert.strictEqual(mixedRow.primaryStatus.code, "pressure_warning", "known warning pressure must still promote while unknown mount pressure is ignored");
}

function testOverviewCapacityIdExactSchemaValidation() {
  const { summarizeMounts } = require("./overview.js");
  const cases = [
    ["dev-1-0", { kind: "capacity_id", value: "dev-1-0", key: "capacity_id:dev-1-0" }],
    ["dev-9999999999-9999999999", { kind: "capacity_id", value: "dev-9999999999-9999999999", key: "capacity_id:dev-9999999999-9999999999" }],
    [" dev-1-0", null],
    ["dev-1-0 ", null],
    ["dev-01-0", null],
    ["dev-1-01", null],
    ["dev-10000000000-0", null],
    ["dev-1-10000000000", null],
    ["dev-0-0", null],
    ["dev-0-1", null],
  ];
  for (const [capacityId, expected] of cases) {
    const [mount] = summarizeMounts(makeCapacitySnapshot({
      selected_roots: [{ mount_id: "candidate", capacity_id: capacityId, major_minor: "8:88" }],
      mounts: [{ mount_id: "candidate", path: "/candidate", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 }],
    }));
    assert.deepStrictEqual(mount.identity, expected, `${JSON.stringify(capacityId)} must follow exact capacity_id schema and must not fallback to major_minor when present-but-invalid`);
  }
}

function testOverviewCapacityBytesRequireStrictJsonNumbers() {
  const { summarizeMounts, aggregateMountCapacity } = require("./overview.js");
  const invalidValues = [
    ["null", null],
    ["string", "100"],
    ["boolean", true],
    ["fraction", 1.5],
    ["negative", -1],
    ["NaN", NaN],
    ["Infinity", Infinity],
    ["unsafe", Number.MAX_SAFE_INTEGER + 1],
  ];
  for (const [name, value] of invalidValues) {
    for (const field of ["df_total", "df_used", "df_avail"]) {
      const mount = { mount_id: "bad", path: "/bad-" + name + "-" + field, df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10 };
      mount[field] = value;
      const aggregate = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
        selected_roots: [{ mount_id: "bad", capacity_id: "dev-8-16" }],
        mounts: [mount],
      })));
      assert.strictEqual(aggregate.isPartial, true, `${name} ${field} must make aggregate partial`);
      assert.strictEqual(aggregate.excludedMountCount, 1, `${name} ${field} must exclude the identity's mount`);
      assert.strictEqual(aggregate.totalLabel, "—", `${name} ${field} must not render an unknown excluded identity as 0 B`);
      assert(aggregate.partialReasons.some(reason => reason.includes("invalid capacity numbers") && reason.includes("dev-8-16")), `${name} ${field} must provide a precise invalid-number reason`);
    }
  }
}

function testOverviewRouteHelpers() {
  const { parseRoute, buildRouteHref } = require("./overview.js");
  assert.deepStrictEqual(parseRoute({ pathname: "/", search: "", hash: "" }), { serverId: null, tab: "treemap" });
  assert.deepStrictEqual(parseRoute({ pathname: "/viewer/", search: "?server=beta-2", hash: "#users" }), { serverId: "beta-2", tab: "users" });
  assert.deepStrictEqual(parseRoute({ pathname: "/viewer/", search: "?server=../bad", hash: "#nope" }), { serverId: null, tab: "treemap" }, "unsafe ids and unknown tabs must collapse to overview defaults");
  assert.deepStrictEqual(parseRoute({ pathname: "/viewer/", search: "?server=.", hash: "#users" }), { serverId: null, tab: "users" }, "single-dot server ids must be rejected in routes");
  assert.deepStrictEqual(parseRoute({ pathname: "/viewer/", search: "?server=..", hash: "#users" }), { serverId: null, tab: "users" }, "double-dot server ids must be rejected in routes");
  assert.strictEqual(buildRouteHref("/viewer/index.html", { serverId: null, tab: "treemap" }), "/viewer/index.html");
  assert.strictEqual(buildRouteHref("/viewer/index.html", { serverId: "beta-2", tab: "topfiles" }), "/viewer/index.html?server=beta-2#topfiles");
  assert.strictEqual(buildRouteHref("/viewer/index.html", { serverId: ".", tab: "treemap" }), "/viewer/index.html", "single-dot server ids must not produce navigable hrefs");
  assert.strictEqual(buildRouteHref("/viewer/index.html", { serverId: "..", tab: "treemap" }), "/viewer/index.html", "double-dot server ids must not produce navigable hrefs");
}

function testRemovedAnalysisSurfaceIsAbsentFromViewerFiles() {
  const removedName = "ad" + "vis" + "or";
  const banned = [removedName, "/" + "ai" + "/", "ai" + " " + removedName, "storage" + "_viz" + "_ai"];
  const bannedHooks = [
    "bind" + "Ad" + "vis" + "orUi",
    "init" + "Ad" + "vis" + "orUI",
    "fetch" + "Ad" + "vis" + "orStatus",
    "append" + "Ad" + "vis" + "orBadges",
  ];
  const checked = ["index.html", "app.js", "overview.js", "treemap.js", "tables.js", "styles.css"];
  for (const file of checked) {
    const content = fs.readFileSync(path.join(here, file), "utf8");
    const lower = content.toLowerCase();
    for (const token of banned) {
      assert(!lower.includes(token), `${file} must not contain removed analysis token ${token}`);
    }
    for (const hook of bannedHooks) {
      assert(!content.includes(hook), `${file} must not contain removed runtime hook ${hook}`);
    }
  }
  const html = fs.readFileSync(path.join(here, "index.html"), "utf8");
  const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map(m => m[1]);
  assert(!scripts.some(src => src.toLowerCase().includes(removedName)), "removed analysis scripts must not be loaded");
  assert(!html.toLowerCase().includes('data-tab="' + removedName + '"'), "removed analysis tab must not exist");
  assert(!html.toLowerCase().includes('id="panel-' + removedName + '"'), "removed analysis panel must not exist");
}

function testTreemapFidelity() {
  const { squarify, rectArea } = require("./treemap.js");
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

function makeCleanupSnapshot() {
  return {
    server_id: "alpha-1",
    hostname: "alpha",
    scan_finished_unix: 1721390400,
    selected_roots: [
      {
        mount_id: "rootfs",
        mount_root: "/",
        mountpoint: "/",
        scan_root: "/home",
        status: "partial",
      },
      {
        mount_id: "data",
        mount_root: "/",
        mountpoint: "/data",
        scan_root: "/data",
        status: "complete",
      },
      {
        mount_id: "archive",
        mount_root: "/",
        mountpoint: "/archive",
        scan_root: "/archive",
        status: "failed",
      },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/home", scan_root: "/home" },
      { mount_id: "data", path: "/data", scan_root: "/data" },
    ],
  };
}

function testCleanupCommandSafetyContracts() {
  const {
    shellQuote,
    cleanupPathWithinRoot,
    cleanupLongestMatchingRoot,
    validateCleanupSelection,
    buildCleanupCommandPlan,
  } = require("./selection.js");

  assert.strictEqual(shellQuote("/data/a b/it's.txt"), "'/data/a b/it'\"'\"'s.txt'");
  assert.strictEqual(typeof cleanupPathWithinRoot, "function", "root containment helper must be exposed for boundary tests");
  assert.strictEqual(typeof cleanupLongestMatchingRoot, "function", "longest-root matching helper must be exposed for boundary tests");
  assert.strictEqual(typeof validateCleanupSelection, "function", "selection validation must be centralized as a pure function");
  assert.strictEqual(typeof buildCleanupCommandPlan, "function", "fixed command template generation must be centralized as a pure function");

  const snapshot = makeCleanupSnapshot();
  const candidate = {
    path: "/data/projects/-dash * glob \"quotes\" 유니코드's",
    kind: "directory",
    source: "treemap",
  };
  const validation = validateCleanupSelection(snapshot, candidate);
  assert.deepStrictEqual({
    accepted: validation.accepted,
    kind: validation.kind,
    path: validation.path,
    scanRoot: validation.scanRoot,
  }, {
    accepted: true,
    kind: "directory",
    path: "/data/projects/-dash * glob \"quotes\" 유니코드's",
    scanRoot: "/data",
  }, "valid file/directory selections must stay opaque and stay anchored to the matched selected scan root");

  const plan = buildCleanupCommandPlan(snapshot, candidate, { revealDestructive: false, freshness: "fresh" });
  assert.deepStrictEqual(plan.inspectionCommands.map(entry => entry.command), [
    "sudo du -shx -- '/data/projects/-dash * glob \"quotes\" 유니코드'\"'\"'s'",
    "sudo find '/data/projects/-dash * glob \"quotes\" 유니코드'\"'\"'s' -xdev \\( -type f -o -type d \\) -printf '%s\\t%TY-%Tm-%Td %TH:%TM\\t%p\\n' | sort -nr | head -n 20",
    "sudo stat -- '/data/projects/-dash * glob \"quotes\" 유니코드'\"'\"'s'",
    "sudo find '/data/projects/-dash * glob \"quotes\" 유니코드'\"'\"'s' -xdev -printf '%TY-%Tm-%Td %TH:%TM\\t%s\\t%p\\n' | sort -r | head -n 20",
  ], "inspection commands must remain first, fixed-template, and safely quoted");
  assert.strictEqual(plan.destructiveVisible, false, "destructive output must stay hidden until an explicit reveal");
  assert.strictEqual(plan.destructiveCommand.command, "sudo rm -ri --one-file-system -- '/data/projects/-dash * glob \"quotes\" 유니코드'\"'\"'s'");
  assert.ok(!plan.destructiveCommand.command.includes(" -f"), "destructive commands must never use force");
  assert.ok(plan.warnings.some(w => /may have changed/i.test(w)), "copy-only panel must warn that the live path may have changed since the snapshot");

  const stalePlan = buildCleanupCommandPlan(snapshot, { path: "/data/old.bin", kind: "file", source: "stale" }, { revealDestructive: true, freshness: "stale" });
  assert.strictEqual(stalePlan.destructiveVisible, true, "destructive output may appear only after a separate explicit reveal");
  assert.strictEqual(stalePlan.destructiveCommand.command, "sudo rm -i -- '/data/old.bin'");
  assert.ok(stalePlan.warnings.some(w => /stale/i.test(w)), "retained stale snapshot state must be explicit in the warning text");

  const rejectedCases = [
    [{ path: "/data/link", kind: "symlink" }, "unsupported_kind"],
    [{ path: "/data/device", kind: "other" }, "unsupported_kind"],
    [{ path: "/data/missing-kind" }, "missing_kind"],
    [{ path: "/" , kind: "directory" }, "root_path"],
    [{ path: "/data", kind: "directory" }, "scan_root"],
    [{ path: "/home", kind: "directory" }, "scan_root"],
    [{ path: "/archive/project", kind: "directory" }, "selected_root_status"],
    [{ path: "/tmp", kind: "directory" }, "top_level_root"],
    [{ path: "/srv/projects/model.ckpt", kind: "file" }, "outside_selected_roots"],
    [{ path: "relative/path", kind: "file" }, "noncanonical_path"],
    [{ path: "/data/../escape", kind: "file" }, "noncanonical_path"],
    [{ path: "/data/control\nchar", kind: "file" }, "control_character"],
  ];
  for (const [input, code] of rejectedCases) {
    const rejected = validateCleanupSelection(snapshot, input);
    assert.strictEqual(rejected.accepted, false, `${input.path || input.kind} must be rejected`);
    assert.strictEqual(rejected.reason.code, code, `${input.path || input.kind} must expose a bounded rejection code`);
  }

  const rootScopedSnapshot = {
    server_id: "root-scan",
    selected_roots: [
      { mount_id: "rootfs", mount_root: "/", mountpoint: "/", scan_root: "/", status: "complete" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", scan_root: "/" },
    ],
  };
  assert.strictEqual(cleanupPathWithinRoot("/home/user/file.bin", "/"), true, 'scan_root "/" must contain deeper descendants');
  assert.strictEqual(cleanupPathWithinRoot("/data2/file.bin", "/data"), false, "prefix collisions must not count as root containment");
  assert.strictEqual(validateCleanupSelection(rootScopedSnapshot, { path: "/", kind: "directory" }).reason.code, "root_path", '"/" itself must stay rejected even when scan_root is "/"');
  assert.strictEqual(validateCleanupSelection(rootScopedSnapshot, { path: "/home", kind: "directory" }).reason.code, "top_level_root", "one-segment targets must stay rejected under scan_root '/'");
  const rootAccepted = validateCleanupSelection(rootScopedSnapshot, { path: "/home/user/file.bin", kind: "file" });
  assert.strictEqual(rootAccepted.accepted, true, "deep descendants must be accepted under complete scan_root '/'");
  assert.strictEqual(rootAccepted.scanRoot, "/", "root-scoped acceptance must stay anchored to '/'");

  const rootFailedSnapshot = {
    server_id: "root-failed",
    selected_roots: [
      { mount_id: "rootfs", mount_root: "/", mountpoint: "/", scan_root: "/", status: "failed" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", scan_root: "/" },
    ],
  };
  assert.strictEqual(validateCleanupSelection(rootFailedSnapshot, { path: "/home/user/file.bin", kind: "file" }).reason.code, "selected_root_status", "deep descendants under failed '/' root must be rejected");

  const collisionSnapshot = {
    server_id: "prefix-collision",
    selected_roots: [
      { mount_id: "data", mount_root: "/", mountpoint: "/data", scan_root: "/data", status: "complete" },
    ],
    mounts: [
      { mount_id: "data", path: "/data", scan_root: "/data" },
    ],
  };
  assert.strictEqual(validateCleanupSelection(collisionSnapshot, { path: "/data2/file.bin", kind: "file" }).reason.code, "outside_selected_roots", "/data vs /data2 prefix collisions must remain rejected");

  const nestedFailedSnapshot = {
    server_id: "nested-failed",
    selected_roots: [
      { mount_id: "rootfs", mount_root: "/", mountpoint: "/", scan_root: "/", status: "complete" },
      { mount_id: "home-user", mount_root: "/", mountpoint: "/home/user", scan_root: "/home/user", status: "failed" },
    ],
    mounts: [
      { mount_id: "rootfs", path: "/", scan_root: "/" },
      { mount_id: "home-user", path: "/home/user", scan_root: "/home/user" },
    ],
  };
  const longest = cleanupLongestMatchingRoot(nestedFailedSnapshot.selected_roots, "/home/user/file.bin");
  assert.strictEqual(longest && longest.scan_root, "/home/user", "nested root matching must prefer the longest scan_root");
  assert.strictEqual(validateCleanupSelection(nestedFailedSnapshot, { path: "/home/user/file.bin", kind: "file" }).reason.code, "selected_root_status", "a nested failed root must win over an outer complete '/' root");
}


function testOverviewMonitorCardCssContract() {
  const css = fs.readFileSync(path.join(here, "styles.css"), "utf8");
  assert(/\.overview-list\b[\s\S]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/.test(css), "overview must use the GPU Monitor three-column card rhythm");
  assert(/\.overview-card\b[\s\S]*border-radius:\s*(?:24px|1\.5rem)/.test(css), "overview cards must share GPU Monitor card geometry");
  assert(/\.overview-mounts\b[\s\S]*flex-direction:\s*column/.test(css), "overview mounts must stack as compact monitor rows");
  assert(/\.overview-pressure-fill\b[\s\S]*background:\s*var\(--accent\)/.test(css), "healthy capacity graphs must reuse the suite accent color");
  assert(/@media\s*\(max-width:\s*520px\)[\s\S]*\.overview-card\b[\s\S]*overflow:\s*hidden/.test(css), "mobile overview cards must explicitly guard against horizontal overflow");
}

async function main() {
  testCleanShellThemeBootstrapContract();
  testOverviewDoesNotBlockOnTheDetailOnlyChartLibrary();
  testHostManifest();
  testHostManifestHelpers();
  await testLoadServerSummariesReturnsNormalizedEnvelope();
  await testOverviewSnapshotFetchPreservesInventoryOrderAndIsolatesFailures();
  testOverviewCapacityThresholdsAndPrecedence();
  testActionableMountFilteringPreservesNonBootOrder();
  testOnlyBootMountsProduceZeroActionableMountCount();
  testOverviewIdentityAwareMountModelAndAggregate();
  testOverviewUnknownCapacityLabelsDoNotImplyZero();
  testUnknownMountCapacityRemainsNeutralAndHonest();
  testOverviewCapacityIdExactSchemaValidation();
  testOverviewCapacityBytesRequireStrictJsonNumbers();
  testOverviewRouteHelpers();
  testOverviewMonitorCardCssContract();
  testRemovedAnalysisSurfaceIsAbsentFromViewerFiles();
  testTreemapFidelity();
  testCleanupCommandSafetyContracts();
  console.log("viewer regression tests passed");
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
