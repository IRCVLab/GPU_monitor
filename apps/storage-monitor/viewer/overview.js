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
  const used = asInt(usedPct, 0);
  const free = asInt(freeBytes, 0);
  if (used >= thresholds.critical_used_pct || free <= thresholds.critical_free_bytes) return "critical";
  if (used >= thresholds.warning_used_pct || free <= thresholds.warning_free_bytes) return "warning";
  return "normal";
}

function statusPresentation(code) {
  const meta = STATUS_META[code] || STATUS_META.normal;
  return Object.assign({ code }, meta, { text: meta.shape ? meta.shape + " " + meta.label : meta.label });
}

function summarizeMounts(snapshot, thresholds = DEFAULT_CAPACITY_THRESHOLDS) {
  const mounts = Array.isArray(snapshot && snapshot.mounts) ? snapshot.mounts : [];
  return mounts.map((mount, index) => {
    const usedPct = asInt(mount && mount.df_use_pct, 0);
    const freeBytes = asInt(mount && mount.df_avail, 0);
    return {
      key: String((mount && mount.mount_id) || (mount && mount.path) || index),
      path: String((mount && mount.path) || (mount && mount.mountpoint) || "/"),
      usedPct,
      freeBytes,
      pressure: pressureLevel(usedPct, freeBytes, thresholds),
      metricText: usedPct + "% · " + compactBytes(freeBytes) + " free",
    };
  });
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
  const primaryStatus = derivePrimaryStatus(summary, snapshot, thresholds, error);
  const secondaryStatus = deriveSecondaryStatus(summary, primaryStatus.code);
  const totalAvailableBytes = mounts.reduce((sum, mount) => sum + mount.freeBytes, 0);
  return {
    id: summary.id,
    displayName: summary.display_name || summary.id,
    order: summary.order,
    mountCount: mounts.length || summary.mount_count,
    totalAvailableBytes,
    totalAvailableLabel: mounts.length ? compactBytes(totalAvailableBytes) : "—",
    mounts,
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

function createOverviewRowElement(doc, row, handlers = {}) {
  const item = makeEl(doc, "li", "overview-item");
  const button = makeEl(doc, "button", "overview-row");
  button.type = "button";
  button.dataset.serverId = row.id;
  button.setAttribute("data-primary-status", row.primaryStatus.code);
  button.setAttribute("aria-label", row.displayName + (row.primaryStatus.label ? " · " + row.primaryStatus.label : ""));

  const main = makeEl(doc, "div", "overview-row-main");
  const titleWrap = makeEl(doc, "div", "overview-row-title");
  const name = makeEl(doc, "span", "overview-name", row.displayName);
  const meta = makeEl(doc, "span", "overview-meta", row.mountCount + "개 마운트 · " + row.totalAvailableLabel + " free");
  titleWrap.appendChild(name);
  titleWrap.appendChild(meta);

  const statusWrap = makeEl(doc, "div", "overview-row-status");
  const primary = createStatusBadge(doc, row.primaryStatus, "overview-badge-primary");
  const secondary = createStatusBadge(doc, row.secondaryStatus, "overview-badge-secondary");
  if (primary) statusWrap.appendChild(primary);
  if (secondary) statusWrap.appendChild(secondary);
  if (!primary && !secondary) statusWrap.appendChild(makeEl(doc, "span", "overview-quiet", "정상"));

  main.appendChild(titleWrap);
  main.appendChild(statusWrap);
  button.appendChild(main);

  const mountsWrap = makeEl(doc, "div", "overview-mounts");
  if (!row.mounts.length) {
    mountsWrap.appendChild(makeEl(doc, "div", "overview-mount overview-mount-empty", "용량 막대 없음"));
  } else {
    for (const mount of row.mounts) {
      const mountEl = makeEl(doc, "div", "overview-mount");
      mountEl.setAttribute("data-pressure", mount.pressure);
      const mountTop = makeEl(doc, "div", "overview-mount-top");
      mountTop.appendChild(makeEl(doc, "span", "overview-mount-path", mount.path));
      mountTop.appendChild(makeEl(doc, "span", "overview-mount-metric", mount.metricText));
      const meter = makeEl(doc, "div", "overview-meter");
      const fill = makeEl(doc, "span", "overview-meter-fill");
      fill.setAttribute("data-pressure", mount.pressure);
      fill.style.width = Math.max(4, Math.min(100, mount.usedPct || 0)) + "%";
      meter.appendChild(fill);
      mountEl.appendChild(mountTop);
      mountEl.appendChild(meter);
      mountsWrap.appendChild(mountEl);
    }
  }
  button.appendChild(mountsWrap);

  const activate = () => {
    if (handlers && typeof handlers.onOpenServer === "function") handlers.onOpenServer(row.id);
  };
  button.onclick = activate;
  button.onkeydown = (event) => {
    if (event && (event.key === "Enter" || event.key === " ")) {
      if (typeof event.preventDefault === "function") event.preventDefault();
      activate();
    }
  };
  item.appendChild(button);
  return item;
}

function renderOverviewList(container, rows, handlers = {}) {
  if (!container) return;
  container.innerHTML = "";
  for (const row of Array.isArray(rows) ? rows : []) container.appendChild(createOverviewRowElement(container.ownerDocument || document, row, handlers));
}

const overviewExports = {
  SAFE_SERVER_ID_RE,
  isSafeServerId,
  KNOWN_DETAIL_TABS,
  DEFAULT_CAPACITY_THRESHOLDS,
  compactBytes,
  normalizeSummary,
  pressureLevel,
  statusPresentation,
  derivePrimaryStatus,
  buildOverviewServer,
  buildOverviewRows,
  parseRoute,
  buildRouteHref,
  renderOverviewList,
};

if (typeof globalThis !== "undefined") Object.assign(globalThis, overviewExports);
if (typeof module !== "undefined" && module.exports) module.exports = overviewExports;
