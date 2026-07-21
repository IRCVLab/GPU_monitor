"use strict";

const SAFE_SERVER_ID_RE = /^[A-Za-z0-9._-]+$/;

function isSafeServerId(value) {
  return typeof value === "string" && SAFE_SERVER_ID_RE.test(value) && value !== "." && value !== "..";
}
const KNOWN_DETAIL_TABS = new Set(["treemap", "users", "topfiles", "stale"]);
const DEFAULT_CAPACITY_THRESHOLDS = Object.freeze({
  warning_used_pct: 80,
  critical_used_pct: 92,
  warning_free_bytes: 549755813888,
  critical_free_bytes: 137438953472,
});

const STATUS_META = Object.freeze({
  normal: { tone: "normal", shape: "", label: "" },
  agent_missing: { tone: "critical", shape: "■", label: "미설치" },
  snapshot_absent: { tone: "critical", shape: "■", label: "스냅샷 없음" },
  pull_unreachable: { tone: "critical", shape: "■", label: "연결 실패" },
  pull_invalid: { tone: "critical", shape: "■", label: "스냅샷 오류" },
  scan_failed: { tone: "critical", shape: "■", label: "스캔 실패" },
  config_drift: { tone: "warning", shape: "◆", label: "구성 차이" },
  partial_scan: { tone: "warning", shape: "▲", label: "일부 수집" },
  stale_snapshot: { tone: "warning", shape: "◌", label: "오래됨" },
  snapshot_load_failed: { tone: "warning", shape: "◆", label: "불러오기 실패" },
  active_scan: { tone: "info", shape: "◌", label: "스캔 중" },
  pressure_warning: { tone: "warning", shape: "▲", label: "여유 적음" },
  pressure_critical: { tone: "critical", shape: "■", label: "여유 부족" },
});

function compactBytes(n) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  if (n < 1024) return n + " B";
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let value = Number(n);
  let idx = -1;
  do {
    value /= 1024;
    idx += 1;
  } while (value >= 1024 && idx < units.length - 1);
  return value.toFixed(value < 10 ? 2 : (value < 100 ? 1 : 0)) + " " + units[idx];
}

function asInt(value, fallback) {
  const num = Number(value);
  return Number.isFinite(num) ? Math.round(num) : fallback;
}


function isActionableMountPath(path) {
  const value = String(path || "").replace(/\/+$/, "") || "/";
  return value !== "/boot" && !value.startsWith("/boot/");
}

function normalizeSummary(summary) {
  const state = summary && summary.state && typeof summary.state === "object" ? summary.state : summary || {};
  return {
    id: summary && summary.id ? String(summary.id) : "",
    display_name: summary && summary.display_name ? String(summary.display_name) : (summary && summary.id ? String(summary.id) : ""),
    order: asInt(summary && summary.order, 0),
    mount_count: asInt(summary && summary.mount_count, 0),
    snapshot_availability: String(state.snapshot_availability || "absent"),
    freshness: String(state.freshness || "unknown"),
    latest_pull_status: String(state.latest_pull_status || "not_installed"),
    latest_scan_result: String(state.latest_scan_result || "failed"),
    configuration_sync: String(state.configuration_sync || "unknown"),
    active_job: state.active_job || null,
  };
}

function pressureLevel(usedPct, freeBytes, thresholds = DEFAULT_CAPACITY_THRESHOLDS) {
  if (typeof usedPct !== "number" || !Number.isFinite(usedPct) || typeof freeBytes !== "number" || !Number.isFinite(freeBytes)) return "unknown";
  const used = Math.round(usedPct);
  const free = Math.round(freeBytes);
  if (used >= thresholds.critical_used_pct || free <= thresholds.critical_free_bytes) return "critical";
  if (used >= thresholds.warning_used_pct || free <= thresholds.warning_free_bytes) return "warning";
  return "normal";
}

function statusPresentation(code) {
  const meta = STATUS_META[code] || STATUS_META.normal;
  return Object.assign({ code }, meta, { text: meta.shape ? meta.shape + " " + meta.label : meta.label });
}

function hasOwn(obj, key) {
  return !!(obj && Object.prototype.hasOwnProperty.call(obj, key));
}

function normalizeBytes(value) {
  if (typeof value !== "number") return null;
  if (!Number.isFinite(value) || value < 0 || !Number.isSafeInteger(value)) return null;
  return value;
}

function normalizePercent(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, Math.round(value)));
}

function isCanonicalDecimal(raw, allowZero) {
  if (!/^\d{1,10}$/.test(raw)) return false;
  if (raw.length > 1 && raw[0] === "0") return false;
  if (!allowZero && raw === "0") return false;
  return true;
}

function canonicalCapacityId(value) {
  if (typeof value !== "string") return null;
  const match = value.match(/^dev-(\d+)-(\d+)$/);
  if (!match) return null;
  if (!isCanonicalDecimal(match[1], false) || !isCanonicalDecimal(match[2], true)) return null;
  return value;
}

function canonicalMajorMinor(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^(\d+):(\d+)$/);
  if (!match) return null;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (!Number.isSafeInteger(major) || !Number.isSafeInteger(minor)) return null;
  if (major === 0 && minor === 0) return null;
  return String(major) + ":" + String(minor);
}

function capacityIdentityFromRoot(root) {
  if (!root || typeof root !== "object") return null;
  if (hasOwn(root, "capacity_id")) {
    const capacityId = canonicalCapacityId(root.capacity_id);
    return capacityId ? { kind: "capacity_id", value: capacityId, key: "capacity_id:" + capacityId } : null;
  }
  const majorMinor = canonicalMajorMinor(root.major_minor);
  return majorMinor ? { kind: "major_minor", value: majorMinor, key: "major_minor:" + majorMinor } : null;
}

function selectedRootByMountId(snapshot) {
  const byMountId = new Map();
  const roots = Array.isArray(snapshot && snapshot.selected_roots) ? snapshot.selected_roots : [];
  for (const root of roots) {
    const mountId = root && root.mount_id != null ? String(root.mount_id) : "";
    if (!mountId || byMountId.has(mountId)) continue;
    byMountId.set(mountId, root);
  }
  return byMountId;
}

function summarizeMounts(snapshot, thresholds = DEFAULT_CAPACITY_THRESHOLDS) {
  const mounts = Array.isArray(snapshot && snapshot.mounts) ? snapshot.mounts : [];
  const rootsByMountId = selectedRootByMountId(snapshot);
  return mounts.filter(mount => isActionableMountPath(mount && (mount.path || mount.mountpoint))).map((mount, index) => {
    const mountId = mount && mount.mount_id != null ? String(mount.mount_id) : "";
    const selectedRoot = mountId ? rootsByMountId.get(mountId) || null : null;
    const usedBytes = normalizeBytes(mount && mount.df_used);
    const totalBytes = normalizeBytes(mount && mount.df_total);
    const availableBytes = normalizeBytes(mount && mount.df_avail);
    const explicitPct = normalizePercent(mount && mount.df_use_pct);
    const computedPct = totalBytes && usedBytes != null ? Math.round((usedBytes / totalBytes) * 100) : null;
    const usedPct = totalBytes != null && usedBytes != null ? (explicitPct != null ? explicitPct : normalizePercent(computedPct)) : null;
    const freeBytes = availableBytes;
    const media = String((selectedRoot && (selectedRoot.storage_media || selectedRoot.block_media)) || (mount && (mount.storage_media || mount.block_media)) || "unknown");
    const mediaConfidence = String((selectedRoot && (selectedRoot.storage_media_confidence || selectedRoot.block_media_confidence)) || (mount && (mount.storage_media_confidence || mount.block_media_confidence)) || "unknown");
    const usedPctText = usedPct == null ? "—" : usedPct + "%";
    const usedTotalText = compactBytes(usedBytes) + " / " + compactBytes(totalBytes);
    const freeText = availableBytes == null ? "여유 미확인" : "여유 " + compactBytes(availableBytes);
    const pressure = pressureLevel(usedPct, freeBytes, thresholds);
    return {
      key: String((mount && mount.mount_id) || (mount && mount.path) || index),
      mountId,
      path: String((mount && mount.path) || (mount && mount.mountpoint) || "/"),
      usedBytes,
      totalBytes,
      availableBytes,
      usedPct,
      freeBytes,
      media,
      mediaConfidence,
      mediaLabel: formatMediaLabel(media),
      usedTotalText,
      usedPctText,
      freeText,
      identity: capacityIdentityFromRoot(selectedRoot),
      pressure,
      pressureLabel: pressureText(pressure),
      metricText: usedPctText + " · " + freeText,
    };
  });
}

function formatMediaLabel(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (raw === "ssd") return "SSD";
  if (raw === "hdd") return "HDD";
  if (raw === "mixed") return "Mixed";
  return "Unknown";
}

function pressureText(value) {
  if (value === "critical") return "위험";
  if (value === "warning") return "주의";
  if (value === "unknown") return "미확인";
  return "정상";
}

function aggregateLabels(totalBytes, usedBytes, availableBytes, isPartial, hasKnownCapacity) {
  if (!hasKnownCapacity) {
    return {
      totalLabel: "—",
      usedLabel: "—",
      availableLabel: "—",
      utilizationLabel: "—",
    };
  }
  const utilization = totalBytes > 0 ? Math.round((usedBytes / totalBytes) * 100) + "%" : "—";
  if (!isPartial) {
    return {
      totalLabel: compactBytes(totalBytes),
      usedLabel: compactBytes(usedBytes),
      availableLabel: compactBytes(availableBytes),
      utilizationLabel: utilization,
    };
  }
  return {
    totalLabel: "확인된 용량 ≥ " + compactBytes(totalBytes),
    usedLabel: "확인된 사용량 ≥ " + compactBytes(usedBytes),
    availableLabel: "확인된 여유 ≥ " + compactBytes(availableBytes),
    utilizationLabel: "확인된 범위 " + utilization,
  };
}

function sameCapacityNumbers(left, right) {
  return left.totalBytes === right.totalBytes && left.usedBytes === right.usedBytes && left.availableBytes === right.availableBytes;
}

function capacityNumbersKnown(mount) {
  return mount && mount.totalBytes != null && mount.usedBytes != null && mount.availableBytes != null;
}

function aggregateMountCapacity(mounts) {
  const inputMounts = Array.isArray(mounts) ? mounts : [];
  const groups = new Map();
  const partialReasons = [];
  let excludedMountCount = 0;
  for (const mount of inputMounts) {
    if (!mount || !mount.identity || !mount.identity.key) {
      excludedMountCount += 1;
      partialReasons.push(String((mount && mount.path) || "unknown mount") + ": unresolved capacity identity, 1개 마운트 제외");
      continue;
    }
    const list = groups.get(mount.identity.key) || [];
    list.push(mount);
    groups.set(mount.identity.key, list);
  }

  let totalBytes = 0;
  let usedBytes = 0;
  let availableBytes = 0;
  const capacityEntries = [];
  for (const [identityKey, group] of groups) {
    const first = group[0];
    const identityLabel = first.identity.value || identityKey;
    const allNumbersKnown = group.every(capacityNumbersKnown);
    const consistent = allNumbersKnown && group.every(mount => sameCapacityNumbers(first, mount));
    if (!consistent) {
      excludedMountCount += group.length;
      const reason = allNumbersKnown ? "inconsistent capacity data" : "invalid capacity numbers";
      partialReasons.push(identityLabel + ": " + reason + ", " + group.length + "개 마운트 제외");
      continue;
    }
    totalBytes += first.totalBytes;
    usedBytes += first.usedBytes;
    availableBytes += first.availableBytes;
    capacityEntries.push({
      key: identityKey,
      identity: first.identity,
      totalBytes: first.totalBytes,
      usedBytes: first.usedBytes,
      availableBytes: first.availableBytes,
    });
  }
  if (!inputMounts.length) partialReasons.push("no known capacity identities");
  const hasKnownCapacity = capacityEntries.length > 0;
  const isPartial = excludedMountCount > 0 || partialReasons.length > 0 || !hasKnownCapacity;
  return Object.assign({
    isPartial,
    excludedMountCount,
    partialReasons,
    totalBytes,
    usedBytes,
    availableBytes,
    capacityEntries,
  }, aggregateLabels(totalBytes, usedBytes, availableBytes, isPartial, hasKnownCapacity));
}

function buildOverviewAggregate(rows) {
  const pageEntries = new Map();
  const partialReasons = [];
  let excludedMountCount = 0;
  for (const row of Array.isArray(rows) ? rows : []) {
    const aggregate = row && row.aggregate ? row.aggregate : aggregateMountCapacity(row && row.mounts);
    excludedMountCount += aggregate.excludedMountCount || 0;
    for (const reason of aggregate.partialReasons || []) partialReasons.push((row && row.id ? row.id + ": " : "") + reason);
    for (const entry of aggregate.capacityEntries || []) {
      const pageKey = String((row && row.id) || "") + "\u0000" + entry.key;
      if (!pageEntries.has(pageKey)) pageEntries.set(pageKey, entry);
    }
  }
  let totalBytes = 0;
  let usedBytes = 0;
  let availableBytes = 0;
  for (const entry of pageEntries.values()) {
    totalBytes += entry.totalBytes;
    usedBytes += entry.usedBytes;
    availableBytes += entry.availableBytes;
  }
  const hasKnownCapacity = pageEntries.size > 0;
  const isPartial = excludedMountCount > 0 || partialReasons.length > 0 || !hasKnownCapacity;
  return Object.assign({
    isPartial,
    excludedMountCount,
    partialReasons,
    totalBytes,
    usedBytes,
    availableBytes,
  }, aggregateLabels(totalBytes, usedBytes, availableBytes, isPartial, hasKnownCapacity));
}

function strongestPressure(mounts) {
  let worst = "normal";
  for (const mount of mounts) {
    if (mount.pressure === "critical") return "critical";
    if (mount.pressure === "warning") worst = "warning";
  }
  return worst;
}

function hasActiveScan(activeJob) {
  return !!(activeJob && (activeJob.state === "requested" || activeJob.state === "running"));
}

function derivePrimaryStatus(summaryInput, snapshot, thresholds = DEFAULT_CAPACITY_THRESHOLDS, error = null) {
  const summary = normalizeSummary(summaryInput);
  const mounts = summarizeMounts(snapshot, thresholds);
  const pressure = strongestPressure(mounts);
  if (summary.snapshot_availability === "absent" && summary.latest_pull_status === "not_installed") return statusPresentation("agent_missing");
  if (summary.snapshot_availability === "absent") return statusPresentation("snapshot_absent");
  if (summary.latest_pull_status === "unreachable") return statusPresentation("pull_unreachable");
  if (summary.latest_pull_status === "invalid_snapshot") return statusPresentation("pull_invalid");
  if (summary.latest_scan_result === "failed") return statusPresentation("scan_failed");
  if (summary.configuration_sync === "drifted") return statusPresentation("config_drift");
  if (summary.latest_scan_result === "partial") return statusPresentation("partial_scan");
  if (summary.freshness === "stale") return statusPresentation("stale_snapshot");
  if (error) return statusPresentation("snapshot_load_failed");
  if (hasActiveScan(summary.active_job)) return statusPresentation("active_scan");
  if (pressure === "critical") return statusPresentation("pressure_critical");
  if (pressure === "warning") return statusPresentation("pressure_warning");
  return statusPresentation("normal");
}

function deriveSecondaryStatus(summaryInput, primaryCode) {
  const summary = normalizeSummary(summaryInput);
  if (hasActiveScan(summary.active_job) && primaryCode !== "active_scan") return statusPresentation("active_scan");
  return null;
}

function buildOverviewServer(summaryInput, snapshot, thresholds = DEFAULT_CAPACITY_THRESHOLDS, error = null) {
  const summary = normalizeSummary(summaryInput);
  const mounts = summarizeMounts(snapshot, thresholds);
  const snapshotHasMountList = Array.isArray(snapshot && snapshot.mounts);
  const primaryStatus = derivePrimaryStatus(summary, snapshot, thresholds, error);
  const secondaryStatus = deriveSecondaryStatus(summary, primaryStatus.code);
  const aggregate = aggregateMountCapacity(mounts);
  const hasKnownCapacity = aggregate.availableLabel !== "—";
  return {
    id: summary.id,
    displayName: summary.display_name || summary.id,
    order: summary.order,
    mountCount: snapshotHasMountList ? mounts.length : summary.mount_count,
    totalAvailableBytes: hasKnownCapacity ? aggregate.availableBytes : null,
    totalAvailableLabel: hasKnownCapacity ? aggregate.availableLabel : "여유 미확인",
    mounts,
    aggregate,
    primaryStatus,
    secondaryStatus,
    snapshot,
    error,
  };
}

function buildOverviewRows(summaries, snapshotEntries, thresholds = DEFAULT_CAPACITY_THRESHOLDS) {
  const entryMap = new Map();
  for (const entry of Array.isArray(snapshotEntries) ? snapshotEntries : []) entryMap.set(entry.id, entry);
  return (Array.isArray(summaries) ? summaries : []).map((summary) => {
    const entry = entryMap.get(summary && summary.id) || { snapshot: null, error: null };
    return buildOverviewServer(summary, entry.snapshot, thresholds, entry.error || null);
  });
}

function parseRoute(locationLike) {
  const search = String((locationLike && locationLike.search) || "");
  const hash = String((locationLike && locationLike.hash) || "").replace(/^#/, "");
  const params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  const rawServerId = params.get("server") || "";
  const serverId = isSafeServerId(rawServerId) ? rawServerId : null;
  const tab = KNOWN_DETAIL_TABS.has(hash) ? hash : "treemap";
  return { serverId, tab };
}

function buildRouteHref(pathname, route) {
  const safePath = pathname || "/";
  if (!route || !route.serverId || !isSafeServerId(route.serverId)) return safePath;
  const tab = KNOWN_DETAIL_TABS.has(route.tab) ? route.tab : "treemap";
  return safePath + "?server=" + encodeURIComponent(route.serverId) + "#" + tab;
}

function makeEl(doc, tag, className, text) {
  const el = doc.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function createStatusBadge(doc, status, extraClass) {
  if (!status || !status.code || status.code === "normal") return null;
  const badge = makeEl(doc, "span", ["overview-badge", extraClass || ""].filter(Boolean).join(" "));
  badge.setAttribute("data-tone", status.tone);
  badge.setAttribute("aria-label", status.label);
  const shape = makeEl(doc, "span", "overview-badge-shape", status.shape);
  const text = makeEl(doc, "span", "overview-badge-text", status.label);
  badge.appendChild(shape);
  badge.appendChild(text);
  return badge;
}

function totalAvailableMetaText(row) {
  if (!row || row.totalAvailableLabel === "—") return "여유 미확인";
  if (/미확인$/.test(row.totalAvailableLabel) || /^확인된 여유 /.test(row.totalAvailableLabel)) return row.totalAvailableLabel;
  return "여유 " + row.totalAvailableLabel;
}

function renderOverviewAggregate(container, aggregate) {
  if (!container) return;
  container.innerHTML = "";
  const doc = container.ownerDocument || document;
  if (!aggregate) {
    container.hidden = true;
    return;
  }
  container.hidden = false;
  const head = makeEl(doc, "div", "overview-aggregate-head");
  head.appendChild(makeEl(doc, "h2", "overview-aggregate-title", "전체 로컬 스토리지"));
  const stateText = !aggregate.totalBytes && aggregate.totalLabel === "—"
    ? "확인된 용량 없음"
    : (aggregate.isPartial ? "부분 집계" : "정확 집계");
  head.appendChild(makeEl(doc, "span", "overview-aggregate-state", stateText));
  container.appendChild(head);

  const metrics = makeEl(doc, "div", "overview-aggregate-metrics");
  const usage = makeEl(doc, "div", "overview-aggregate-metric overview-aggregate-metric-primary");
  usage.appendChild(makeEl(doc, "span", "overview-aggregate-label", "사용률"));
  const usageLine = makeEl(doc, "span", "overview-aggregate-usage");
  usageLine.appendChild(makeEl(doc, "span", "overview-aggregate-percent figure", aggregate.utilizationLabel));
  usageLine.appendChild(makeEl(doc, "span", "overview-aggregate-capacity figure", aggregate.usedLabel + " / " + aggregate.totalLabel));
  usage.appendChild(usageLine);
  metrics.appendChild(usage);

  const free = makeEl(doc, "div", "overview-aggregate-metric");
  free.appendChild(makeEl(doc, "span", "overview-aggregate-label", "여유"));
  free.appendChild(makeEl(doc, "span", "overview-aggregate-value figure", aggregate.availableLabel));
  metrics.appendChild(free);
  container.appendChild(metrics);

  if (aggregate.isPartial && aggregate.partialReasons && aggregate.partialReasons.length) {
    const note = makeEl(doc, "p", "overview-aggregate-note", aggregate.partialReasons.slice(0, 2).join(" · ") + (aggregate.partialReasons.length > 2 ? " …" : ""));
    container.appendChild(note);
  }
}

function createOverviewRowElement(doc, row, handlers = {}) {
  const item = makeEl(doc, "li", "overview-item");
  const capacityOnlyState = row.primaryStatus.code === "pressure_warning" || row.primaryStatus.code === "pressure_critical";
  const operationalTone = capacityOnlyState ? "normal" : (row.primaryStatus.tone || "normal");
  const card = makeEl(doc, "a", "overview-card");
  card.setAttribute("href", buildRouteHref(handlers.pathname || "/", { serverId: row.id, tab: "treemap" }));
  card.dataset.serverId = row.id;
  card.setAttribute("data-primary-status", row.primaryStatus.code);
  card.setAttribute("data-tone", operationalTone);

  const cardHeader = makeEl(doc, "header", "overview-card-header");
  const titleLine = makeEl(doc, "div", "overview-card-title-line");
  titleLine.appendChild(makeEl(doc, "h2", "overview-name", row.displayName));
  const statusDot = makeEl(doc, "span", "overview-status-dot");
  statusDot.setAttribute("aria-hidden", "true");
  statusDot.setAttribute("data-tone", operationalTone);
  titleLine.appendChild(statusDot);
  titleLine.appendChild(makeEl(doc, "span", "overview-meta", row.mountCount + "개 마운트"));
  cardHeader.appendChild(titleLine);
  if (row.primaryStatus.code !== "normal" && !capacityOnlyState) {
    cardHeader.appendChild(makeEl(doc, "span", "overview-card-state", row.primaryStatus.label));
  }
  card.appendChild(cardHeader);

  const mountsWrap = makeEl(doc, "div", "overview-mounts");
  if (!row.mounts.length) {
    mountsWrap.appendChild(makeEl(doc, "div", "overview-mount overview-mount-empty", "표시할 데이터 마운트 없음"));
  } else {
    for (const mount of row.mounts) {
      const mountEl = makeEl(doc, "div", "overview-mount");
      mountEl.setAttribute("data-pressure", mount.pressure);
      if (mount.pressure === "warning" || mount.pressure === "critical") {
        mountEl.setAttribute("role", "group");
        mountEl.setAttribute("aria-label", [mount.path, mount.mediaLabel, mount.usedPctText, mount.freeText, mount.pressureLabel].join(", "));
      }
      mountEl.appendChild(makeEl(doc, "span", "overview-media-label", mount.mediaLabel));
      const body = makeEl(doc, "div", "overview-mount-body");
      const line = makeEl(doc, "div", "overview-mount-line");
      const pathEl = makeEl(doc, "div", "overview-mount-path", mount.path);
      pathEl.title = mount.path;
      line.appendChild(pathEl);
      line.appendChild(makeEl(doc, "div", "overview-mount-pct", mount.usedPctText));
      body.appendChild(line);
      const metrics = makeEl(doc, "div", "overview-mount-metrics");
      const meter = makeEl(doc, "div", "overview-pressure-bar");
      const fill = makeEl(doc, "span", "overview-pressure-fill");
      fill.setAttribute("data-pressure", mount.pressure);
      fill.style.width = Math.max(4, Math.min(100, mount.usedPct == null ? 0 : mount.usedPct)) + "%";
      meter.appendChild(fill);
      metrics.appendChild(meter);
      metrics.appendChild(makeEl(doc, "div", "overview-mount-free", mount.freeText));
      body.appendChild(metrics);
      mountEl.appendChild(body);
      mountsWrap.appendChild(mountEl);
    }
  }
  card.appendChild(mountsWrap);

  if (row.mounts.length) {
    const footer = makeEl(doc, "footer", "overview-card-footer");
    footer.appendChild(makeEl(doc, "span", "overview-footer-label", "스토리지"));
    footer.appendChild(makeEl(doc, "span", "overview-footer-value", row.aggregate.utilizationLabel));
    footer.appendChild(makeEl(doc, "span", "overview-footer-separator", "·"));
    footer.appendChild(makeEl(doc, "span", "overview-footer-value", totalAvailableMetaText(row)));
    const warningCount = row.mounts.filter(mount => mount.pressure === "warning").length;
    const criticalCount = row.mounts.filter(mount => mount.pressure === "critical").length;
    if (criticalCount) footer.appendChild(makeEl(doc, "span", "overview-footer-pressure overview-footer-critical", "위험 " + criticalCount));
    if (warningCount) footer.appendChild(makeEl(doc, "span", "overview-footer-pressure overview-footer-warning", "주의 " + warningCount));
    card.appendChild(footer);
  }

  const activate = () => {
    if (handlers && typeof handlers.onOpenServer === "function") handlers.onOpenServer(row.id);
  };
  card.onclick = (event) => {
    if (event && typeof event.preventDefault === "function") event.preventDefault();
    activate();
  };
  card.onkeydown = (event) => {
    if (event && event.key === " ") {
      if (typeof event.preventDefault === "function") event.preventDefault();
      activate();
    }
  };
  item.appendChild(card);
  return item;
}

function overviewColumnCountFromStyle(style) {
  const template = String((style && style.gridTemplateColumns) || "").trim();
  if (!template || template === "none") return 1;
  const repeat = template.match(/^repeat\((\d+),/);
  if (repeat) return Math.max(1, Number(repeat[1]) || 1);
  return Math.max(1, template.split(/\s+/).filter(Boolean).length);
}

function prefersReducedOverviewMotion() {
  return typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function animateOverviewColumnChange(container, items, firstRects, columnCount) {
  const previousColumnCount = container.__overviewColumnCount;
  container.__overviewColumnCount = columnCount;
  if (!previousColumnCount || previousColumnCount === columnCount || prefersReducedOverviewMotion()) return;
  for (const item of items) {
    const first = firstRects.get(item);
    if (!first || typeof item.getBoundingClientRect !== "function") continue;
    const last = item.getBoundingClientRect();
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    if (!dx && !dy) continue;
    item.style.transition = "none";
    item.style.transform = "translate(" + dx + "px, " + dy + "px)";
    void item.offsetWidth;
    const run = () => {
      item.style.transition = "transform 280ms cubic-bezier(.22,1,.36,1)";
      item.style.transform = "translate(0, 0)";
      setTimeout(() => {
        if (item.style.transform === "translate(0, 0)") item.style.transform = "";
        if (item.style.transition === "transform 280ms cubic-bezier(.22,1,.36,1)") item.style.transition = "";
      }, 300);
    };
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(run);
    else run();
  }
}

function layoutOverviewMasonry(container) {
  if (!container || !container.children) return;
  const items = Array.from(container.children);
  if (!items.length || typeof items[0].getBoundingClientRect !== "function" || typeof getComputedStyle !== "function") return;
  const firstRects = new Map();
  for (const item of items) {
    if (typeof item.getBoundingClientRect === "function") firstRects.set(item, item.getBoundingClientRect());
  }
  for (const item of items) {
    item.style.transform = "";
    item.style.transition = "";
    item.style.gridRow = "auto";
    item.style.gridColumn = "auto";
  }
  void container.offsetWidth;
  const style = getComputedStyle(container);
  const rowHeight = Number.parseFloat(style.gridAutoRows) || 1;
  const rowGap = Number.parseFloat(style.rowGap) || 0;
  const columnCount = overviewColumnCountFromStyle(style);
  const nextRowByColumn = Array(columnCount).fill(1);
  items.forEach((item, index) => {
    const card = item.firstElementChild || item;
    const height = card.getBoundingClientRect().height;
    const span = Math.max(1, Math.ceil((height + rowGap) / (rowHeight + rowGap)));
    const column = index % columnCount;
    const startRow = nextRowByColumn[column];
    item.style.gridColumn = String(column + 1);
    item.style.gridRow = String(startRow) + " / span " + span;
    nextRowByColumn[column] = startRow + span;
  });
  animateOverviewColumnChange(container, items, firstRects, columnCount);
}

function scheduleOverviewMasonry(container) {
  if (!container || typeof requestAnimationFrame !== "function") return;
  if (container.__overviewLayoutFrame && typeof cancelAnimationFrame === "function") cancelAnimationFrame(container.__overviewLayoutFrame);
  container.__overviewLayoutFrame = requestAnimationFrame(() => {
    container.__overviewLayoutFrame = 0;
    layoutOverviewMasonry(container);
  });
  if (!container.__overviewResizeObserver && typeof ResizeObserver === "function") {
    container.__overviewResizeObserver = new ResizeObserver(() => scheduleOverviewMasonry(container));
    container.__overviewResizeObserver.observe(container);
  }
}

function renderOverviewList(container, rows, handlers = {}) {
  if (!container) return;
  container.innerHTML = "";
  for (const row of Array.isArray(rows) ? rows : []) container.appendChild(createOverviewRowElement(container.ownerDocument || document, row, handlers));
  scheduleOverviewMasonry(container);
}

const overviewExports = {
  SAFE_SERVER_ID_RE,
  isSafeServerId,
  KNOWN_DETAIL_TABS,
  DEFAULT_CAPACITY_THRESHOLDS,
  compactBytes,
  isActionableMountPath,
  normalizeSummary,
  normalizePercent,
  selectedRootByMountId,
  summarizeMounts,
  formatMediaLabel,
  pressureText,
  aggregateMountCapacity,
  buildOverviewAggregate,
  pressureLevel,
  statusPresentation,
  derivePrimaryStatus,
  buildOverviewServer,
  buildOverviewRows,
  parseRoute,
  buildRouteHref,
  renderOverviewAggregate,
  renderOverviewList,
  layoutOverviewMasonry,
  totalAvailableMetaText,
};

if (typeof globalThis !== "undefined") Object.assign(globalThis, overviewExports);
if (typeof module !== "undefined" && module.exports) module.exports = overviewExports;
