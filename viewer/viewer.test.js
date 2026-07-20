#!/usr/bin/env node
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const here = __dirname;
const GiB = 1024 ** 3;

function testHostManifest() {
  const manifestPath = path.join(here, "..", "data", "hosts.json");
  const hosts = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  assert(Array.isArray(hosts), "hosts manifest must be an array");
  assert(hosts.some(h => h.id === "hinton" && h.file === "hinton"), "hinton host entry must exist");
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
}

async function testOverviewSnapshotFetchPreservesInventoryOrderAndIsolatesFailures() {
  const { loadOrderedSnapshotsForOverview } = require("./data-client.js");
  const summaries = [
    { id: "beta-2", display_name: "beta", order: 20 },
    { id: "alpha-1", display_name: "alpha", order: 10 },
    { id: "gamma-3", display_name: "gamma", order: 30 },
  ];
  const events = [];
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
  });

  assert.notDeepStrictEqual(events, result.map(item => item.id), "responses may resolve out of order in the fetch layer");
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
      { path: "/data", df_use_pct: 81, df_avail: 900 * GiB },
      { path: "/archive", df_use_pct: 93, df_avail: 600 * GiB },
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

  const precedenceCases = [
    [makeSummary({ snapshot_availability: "absent", latest_pull_status: "not_installed" }), null, null, "agent_missing"],
    [makeSummary({ snapshot_availability: "absent", latest_pull_status: "succeeded" }), null, null, "snapshot_absent"],
    [makeSummary({ latest_pull_status: "unreachable" }), makeSnapshot(), null, "pull_unreachable"],
    [makeSummary({ latest_pull_status: "invalid_snapshot" }), makeSnapshot(), null, "pull_invalid"],
    [makeSummary({ latest_scan_result: "failed" }), makeSnapshot(), null, "scan_failed"],
    [makeSummary({ configuration_sync: "drifted" }), makeSnapshot(), null, "config_drift"],
    [makeSummary({ latest_scan_result: "partial" }), makeSnapshot(), null, "partial_scan"],
    [makeSummary({ freshness: "stale" }), makeSnapshot(), null, "stale_snapshot"],
    [makeSummary({ active_job: { id: "job-1", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }), makeSnapshot([{ path: "/data", df_use_pct: 95, df_avail: 900 * GiB }]), null, "active_scan"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_use_pct: 95, df_avail: 900 * GiB }]), null, "pressure_critical"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_use_pct: 10, df_avail: 20 * GiB }]), null, "pressure_critical"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_use_pct: 81, df_avail: 900 * GiB }]), null, "pressure_warning"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_use_pct: 55, df_avail: 900 * GiB }]), null, "normal"],
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
    makeSnapshot([{ path: "/data", df_use_pct: 93, df_avail: 1024 * GiB }]),
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
    mounts: [
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
      { mount_id: "data", path: "/data-bind", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75 },
    ],
  })));
  assert.strictEqual(exactDuplicate.totalBytes, 2000, "exact duplicate capacity identities within a server must count once");
  assert.strictEqual(exactDuplicate.isPartial, false, "consistent exact duplicates must not make the aggregate partial");

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
  assert.strictEqual(unresolved.totalLabel, "확인된 용량 ≥ 0 B", "partial total labels must not imply unknown capacity is included");
  assert.strictEqual(unresolved.utilizationLabel, "확인된 범위 —", "partial utilization labels must show an unknown range when no known capacity remains");

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

async function main() {
  testHostManifest();
  testHostManifestHelpers();
  await testOverviewSnapshotFetchPreservesInventoryOrderAndIsolatesFailures();
  testOverviewCapacityThresholdsAndPrecedence();
  testOverviewIdentityAwareMountModelAndAggregate();
  testOverviewRouteHelpers();
  testRemovedAnalysisSurfaceIsAbsentFromViewerFiles();
  testTreemapFidelity();
  testCleanupCommandSafetyContracts();
  console.log("viewer regression tests passed");
}

main().catch(err => {
  console.error(err);
  process.exit(1);
});
