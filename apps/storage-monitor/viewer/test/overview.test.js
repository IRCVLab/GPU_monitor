import test from 'node:test';
import assert from 'node:assert/strict';
import * as viewer from '../views/overview.js';
const GiB = 1024 ** 3;
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
      { path: "/data", df_total: 5000 * GiB, df_used: 4050 * GiB, df_use_pct: 81, used_pct: 81, severity: "warning", df_avail: 900 * GiB },
      { path: "/archive", df_total: 8000 * GiB, df_used: 7440 * GiB, df_use_pct: 93, used_pct: 93, severity: "critical", df_avail: 600 * GiB },
    ],
  };
}

function testOverviewCapacityThresholdsAndPrecedence() {
  const {
    derivePrimaryStatus,
    statusPresentation,
    buildOverviewServer,
  } = viewer;

  const observed = Date.now() / 1000;
  const scanSnapshot = { mounts: [] };
  const running = { state: "running", observed_unix: observed };
  assert.strictEqual(buildOverviewServer(makeSummary({ scan_status: running }), scanSnapshot).primaryStatus.code, "active_scan");
  assert.strictEqual(buildOverviewServer(makeSummary({ scan_status: { ...running, activity: "unknown" } }), scanSnapshot).primaryStatus.code, "normal");
  assert.strictEqual(buildOverviewServer(makeSummary({ scan_status: { ...running, disconnected: true } }), scanSnapshot).primaryStatus.code, "normal");
  const failedDuringScan = buildOverviewServer(makeSummary({ latest_scan_result: "failed", scan_status: running }), scanSnapshot);
  assert.strictEqual(failedDuringScan.primaryStatus.code, "scan_failed");
  assert.strictEqual(failedDuringScan.secondaryStatus.code, "active_scan");

  const precedenceCases = [
    [makeSummary({ snapshot_availability: "absent", latest_pull_status: "not_installed" }), null, null, "agent_missing"],
    [makeSummary({ snapshot_availability: "absent", latest_pull_status: "succeeded" }), null, null, "snapshot_absent"],
    [makeSummary({ latest_pull_status: "unreachable" }), makeSnapshot(), null, "pull_unreachable"],
    [makeSummary({ latest_pull_status: "invalid_snapshot" }), makeSnapshot(), null, "pull_invalid"],
    [makeSummary({ latest_scan_result: "failed" }), makeSnapshot(), null, "scan_failed"],
    [makeSummary({ configuration_sync: "drifted" }), makeSnapshot(), null, "config_drift"],
    [makeSummary({ latest_scan_result: "partial" }), makeSnapshot(), null, "partial_scan"],
    [makeSummary({ freshness: "stale" }), makeSnapshot(), null, "stale_snapshot"],
    [makeSummary({ active_job: { id: "job-1", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 950 * GiB, df_use_pct: 95, used_pct: 95, severity: "critical", df_avail: 900 * GiB }]), null, "active_scan"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 950 * GiB, df_use_pct: 95, used_pct: 95, severity: "critical", df_avail: 900 * GiB }]), null, "pressure_critical"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 100 * GiB, df_use_pct: 10, used_pct: 10, severity: "critical", df_avail: 20 * GiB }]), null, "pressure_critical"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 810 * GiB, df_use_pct: 81, used_pct: 81, severity: "warning", df_avail: 900 * GiB }]), null, "pressure_warning"],
    [makeSummary(), makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 550 * GiB, df_use_pct: 55, used_pct: 55, severity: "normal", df_avail: 900 * GiB }]), null, "normal"],
    [makeSummary(), null, new Error("snapshot load failed"), "snapshot_load_failed"],
    [makeSummary({ active_job: { id: "job-9", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }), null, new Error("snapshot load failed"), "snapshot_load_failed"],
    [makeSummary({ freshness: "stale" }), null, new Error("snapshot load failed"), "stale_snapshot"],
    [makeSummary({ latest_pull_status: "unreachable" }), null, new Error("snapshot load failed"), "pull_unreachable"],
  ];
  for (const [summary, snapshot, error, expectedCode] of precedenceCases) {
    const status = derivePrimaryStatus(summary, snapshot, error);
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
    makeSnapshot([{ path: "/data", df_total: 1000 * GiB, df_used: 930 * GiB, df_use_pct: 93, used_pct: 93, severity: "critical", df_avail: 1024 * GiB }]),
  );
  assert.strictEqual(model.primaryStatus.code, "scan_failed", "higher-priority operational state must win over capacity pressure");
  assert.strictEqual(model.secondaryStatus.code, "active_scan", "active scan must remain available as a secondary cue");
  assert.strictEqual(model.mounts.length, 1, "capacity bars must remain present when an operational status wins");
  assert.strictEqual(model.mounts[0].pressure, "critical");

  const loadFailureModel = buildOverviewServer(
    makeSummary({ active_job: { id: "job-3", server_id: "alpha-1", kind: "rescan", state: "running", actor: "operator-1", requested_unix: 1719200000, started_unix: 1719200001, finished_unix: null, result_code: null } }),
    null,
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
      { mount_id: "rootfs", path: "/", df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical" },
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
      { mount_id: "legacy", path: "/legacy", df_total: 3000, df_used: 1200, df_avail: 1800, df_use_pct: 40, used_pct: 40, severity: "critical" },
    ],
  }, overrides);
}

function testActionableMountFilteringPreservesNonBootOrder() {
  const { isActionableMountPath, summarizeMounts, buildOverviewServer } = viewer;
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
      { mount_id: "boot", path: "/boot", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" },
      { mount_id: "home", path: "/home", df_total: 1000, df_used: 400, df_avail: 600, df_use_pct: 40, used_pct: 40, severity: "critical" },
      { mount_id: "efi", path: "/boot/efi", df_total: 200, df_used: 50, df_avail: 150, df_use_pct: 25, used_pct: 25, severity: "critical" },
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
      { mount_id: "bootloader", path: "/bootloader", df_total: 300, df_used: 120, df_avail: 180, df_use_pct: 40, used_pct: 40, severity: "critical" },
    ],
  });
  assert.deepStrictEqual(summarizeMounts(snapshot).map(m => m.path), ["/home", "/data", "/bootloader"], "summaries must omit /boot mounts while preserving non-boot input order");
  const row = buildOverviewServer(makeSummary({ mount_count: 5 }), snapshot);
  assert.strictEqual(row.mountCount, 3, "server row mount count must use actionable mount count after boot filtering");
}

function testOnlyBootMountsProduceZeroActionableMountCount() {
  const { buildOverviewServer } = viewer;
  const row = buildOverviewServer(
    makeSummary({ id: "boot-only", display_name: "boot-only", mount_count: 2 }),
    {
      server_id: "boot-only",
      selected_roots: [
        { mount_id: "boot", capacity_id: "dev-8-1", storage_media: "ssd" },
        { mount_id: "efi", capacity_id: "dev-8-2", storage_media: "ssd" },
      ],
      mounts: [
        { mount_id: "boot", path: "/boot", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" },
        { mount_id: "efi", path: "/boot/efi", df_total: 200, df_used: 20, df_avail: 180, df_use_pct: 10, used_pct: 10, severity: "critical" },
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
  } = viewer;

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
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
      { mount_id: "data-bind", path: "/data-bind", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
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
        { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
        { mount_id: "data-bind", path: "/data-bind", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
      ],
    }) },
  ])[0];
  assert.strictEqual(exactDuplicateRow.aggregate.availableLabel, "500 B", "server aggregate label must expose deduped available capacity");
  assert.strictEqual(exactDuplicateRow.totalAvailableBytes, 500, "server header free byte model must derive from the identity-aware aggregate");
  assert.strictEqual(exactDuplicateRow.totalAvailableLabel, "500 B", "server header free label must derive from the identity-aware aggregate");

  const inconsistentDuplicate = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    mounts: [
      { mount_id: "data", path: "/data", df_total: 2000, df_used: 1500, df_avail: 500, df_use_pct: 75, used_pct: 75, severity: "critical" },
      { mount_id: "data", path: "/data-stale", df_total: 3000, df_used: 1500, df_avail: 1500, df_use_pct: 50, used_pct: 50, severity: "critical" },
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
    mounts: [{ mount_id: "missing", path: "/missing", df_total: 1000, df_used: 100, df_avail: 900, df_use_pct: 10, used_pct: 10, severity: "critical" }],
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
      { mount_id: "zero", path: "/zero", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" },
      { mount_id: "invalid", path: "/invalid", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" },
      { mount_id: "empty-capacity", path: "/empty", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" },
      { mount_id: "invalid-capacity", path: "/invalid-capacity", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" },
    ],
  }));
  assert.deepStrictEqual(invalidIds.map(m => m.identity), [null, null, null, null], "zero, malformed, empty, and noncanonical identities must remain unresolved instead of falling back ambiguously");

  const rows = buildOverviewRows([
    { id: "beta-2", display_name: "beta", order: 20, mount_count: 1 },
    { id: "alpha-1", display_name: "alpha", order: 10, mount_count: 2 },
  ], [
    { id: "beta-2", snapshot: makeCapacitySnapshot({ server_id: "beta-2", selected_roots: [{ mount_id: "same", capacity_id: "dev-8-1" }], mounts: [{ mount_id: "same", path: "/beta", df_total: 1000, df_used: 100, df_avail: 900, df_use_pct: 10, used_pct: 10, severity: "critical" }] }) },
    { id: "alpha-1", snapshot: makeCapacitySnapshot({ server_id: "alpha-1", selected_roots: [{ mount_id: "same", capacity_id: "dev-8-1" }], mounts: [{ mount_id: "same", path: "/alpha", df_total: 2000, df_used: 500, df_avail: 1500, df_use_pct: 25, used_pct: 25, severity: "critical" }] }) },
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
  const { buildOverviewServer, buildOverviewAggregate, summarizeMounts, aggregateMountCapacity } = viewer;

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
    mounts: [{ mount_id: "mystery", path: "/mystery", df_total: 1000, df_used: 100, df_avail: 900, df_use_pct: 10, used_pct: 10, severity: "critical" }],
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
      { mount_id: "known", path: "/known", df_total: 2048, df_used: 1024, df_avail: 1024, df_use_pct: 50, used_pct: 50, severity: "critical" },
      { mount_id: "unknown", path: "/unknown", df_total: 4096, df_used: 1024, df_avail: 3072, df_use_pct: 25, used_pct: 25, severity: "critical" },
    ],
  })));
  assert.strictEqual(knownPlusExcluded.totalLabel, "확인된 용량 ≥ 2.00 KB", "known-plus-excluded partial aggregate must label only the known lower bound");
  assert.strictEqual(knownPlusExcluded.utilizationLabel, "확인된 범위 50%", "known-plus-excluded utilization must remain known-only partial language");

  const actualZero = aggregateMountCapacity(summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "zero-cap", capacity_id: "dev-1-0" }],
    mounts: [{ mount_id: "zero-cap", path: "/zero-cap", df_total: 0, df_used: 0, df_avail: 0, df_use_pct: 0, used_pct: 0, severity: "critical" }],
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
      mounts: [{ mount_id: "known", path: "/known", df_total: 2048, df_used: 1024, df_avail: 1024, df_use_pct: 50, used_pct: 50, severity: "critical" }],
    })),
  ]);
  assert.strictEqual(page.isPartial, true, "page aggregate containing an unavailable server must be partial");
  assert.strictEqual(page.totalLabel, "확인된 용량 ≥ 2.00 KB", "page aggregate with unavailable server must show known-only lower-bound capacity");
  assert.strictEqual(page.utilizationLabel, "확인된 범위 50%", "page aggregate with unavailable server must not imply unavailable capacity is included");
}

function testUnknownMountCapacityRemainsNeutralAndHonest() {
  const { buildOverviewServer, summarizeMounts } = viewer;

  const missingAvailable = summarizeMounts(makeCapacitySnapshot({
    selected_roots: [{ mount_id: "unknown-free", capacity_id: "dev-8-16", storage_media: "ssd" }],
    mounts: [{ mount_id: "unknown-free", path: "/unknown-free", df_total: 2048, df_used: 1024, df_use_pct: 50, used_pct: 50 }],
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
    mounts: [{ mount_id: "invalid-cap", path: "/invalid-cap", df_total: "2048", df_used: -1, df_avail: Infinity, df_use_pct: 75, used_pct: null }],
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

  const unknownOnlyRow = buildOverviewServer(
    makeSummary({ id: "unknown-only", display_name: "unknown-only" }),
    makeCapacitySnapshot({
      server_id: "unknown-only",
      selected_roots: [{ mount_id: "unknown-free", capacity_id: "dev-8-16", storage_media: "ssd" }],
      mounts: [{ mount_id: "unknown-free", path: "/unknown-free", df_total: 2048, df_used: 1024, df_use_pct: 95, used_pct: 95 }],
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
        { mount_id: "unknown-free", path: "/unknown-free", df_total: 2048, df_used: 1024, df_use_pct: 95, used_pct: 95 },
        { mount_id: "known-warning", path: "/known-warning", df_total: 4096 * GiB, df_used: 3500 * GiB, df_avail: 596 * GiB, df_use_pct: 85, used_pct: 85, severity: "warning" },
      ],
    }),
  );
  assert.strictEqual(mixedRow.primaryStatus.code, "pressure_warning", "known warning pressure must still promote while unknown mount pressure is ignored");
}

function testOverviewCapacityIdExactSchemaValidation() {
  const { summarizeMounts } = viewer;
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
      mounts: [{ mount_id: "candidate", path: "/candidate", df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" }],
    }));
    assert.deepStrictEqual(mount.identity, expected, `${JSON.stringify(capacityId)} must follow exact capacity_id schema and must not fallback to major_minor when present-but-invalid`);
  }
}

function testOverviewCapacityBytesRequireStrictJsonNumbers() {
  const { summarizeMounts, aggregateMountCapacity } = viewer;
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
      const mount = { mount_id: "bad", path: "/bad-" + name + "-" + field, df_total: 100, df_used: 10, df_avail: 90, df_use_pct: 10, used_pct: 10, severity: "critical" };
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
test('testOverviewCapacityThresholdsAndPrecedence', testOverviewCapacityThresholdsAndPrecedence);
test('testActionableMountFilteringPreservesNonBootOrder', testActionableMountFilteringPreservesNonBootOrder);
test('testOnlyBootMountsProduceZeroActionableMountCount', testOnlyBootMountsProduceZeroActionableMountCount);
test('testOverviewIdentityAwareMountModelAndAggregate', testOverviewIdentityAwareMountModelAndAggregate);
test('testOverviewUnknownCapacityLabelsDoNotImplyZero', testOverviewUnknownCapacityLabelsDoNotImplyZero);
test('testUnknownMountCapacityRemainsNeutralAndHonest', testUnknownMountCapacityRemainsNeutralAndHonest);
test('testOverviewCapacityIdExactSchemaValidation', testOverviewCapacityIdExactSchemaValidation);
test('testOverviewCapacityBytesRequireStrictJsonNumbers', testOverviewCapacityBytesRequireStrictJsonNumbers);
