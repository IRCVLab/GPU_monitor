"use strict";

/* =========================================================================
   storage-viz — Apple-style, performance-minded viewer.
   - Parse JSON once; build the ECharts treemap data for the CURRENT mount
     only, on demand. Dispose/rebuild on mount/host switch.
   - Defer chart init until the Treemap tab is first visible; Users chart
     inits lazily on first Users-tab activation.
   - Stale (~4000 rows) is virtualized: only the visible window is in the DOM.
   - Fully offline; everything is JSON-driven.
   ========================================================================= */

const DEFAULT_HOSTS = [
  { id: "hinton", label: "hinton", file: "hinton", default: true }
];
var HOSTS = DEFAULT_HOSTS.slice();

/* ---- Utilities ---- */
function humanBytes(n) {
  if (n == null || isNaN(n)) return "—";
  if (n < 1024) return n + " B";
  const u = ["KB", "MB", "GB", "TB", "PB"]; // 1024-based magnitudes, short labels
  let i = -1, v = n;
  do { v /= 1024; i++; } while (v >= 1024 && i < u.length - 1);
  return v.toFixed(v < 10 ? 2 : (v < 100 ? 1 : 0)) + " " + u[i];
}
function fmtDate(unix) {
  if (!unix) return "—";
  const d = new Date(unix * 1000);
  if (isNaN(d.getTime())) return "—";
  const p = (x) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
}
function fmtAge(days) {
  if (days == null) return "—";
  if (days < 1) return "today";
  if (days < 30) return days + "d";
  if (days < 365) return (days / 30).toFixed(days < 90 ? 1 : 0) + "mo";
  return (days / 365).toFixed(1) + "y";
}
function daysAgo(unix) { return unix ? Math.max(0, Math.round((nowUnix - unix) / 86400)) : null; }
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function mountOf(path) {
  let best = "/";
  for (const mp of mountPaths) {
    if (mp === "/") continue;
    if ((path === mp || path.startsWith(mp + "/")) && mp.length > best.length) best = mp;
  }
  return best;
}

/* ---- Color: stable uid palette + fixed per-mount palette ----
   Harmonious, desaturated Apple-like categorical set: evenly-spaced hues at a
   uniform saturation/lightness (~S52% L56%) so owners read as one calm family
   inside the light card (and stay legible in dark), with clear hue separation. */
const PALETTE = [
  "#548fc9", "#5463c9", "#7254c9", "#9d54c9", "#c954c9",
  "#c9549d", "#c95472", "#c96354", "#c98f54", "#c9bb54",
  "#acc954", "#80c954", "#54c954", "#54c980", "#54c9ac",
  "#54bbc9"
];
const OTHER_COLOR = "#9aa0a6";

/* Pick the label color that maximizes WCAG contrast against the tile color —
   white vs near-black, whichever wins. (A fixed luminance cutoff mis-handles
   mid-tone tiles, e.g. our desaturated greens read far better with dark text.) */
function relLum(hex) {
  const r = parseInt(hex.slice(1, 3), 16) / 255,
        g = parseInt(hex.slice(3, 5), 16) / 255,
        b = parseInt(hex.slice(5, 7), 16) / 255;
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
const DARK_LABEL = "#1d1d1f";
function labelColorForBg(hex) {
  if (!hex || hex[0] !== "#" || hex.length < 7) return "#ffffff";
  const L = relLum(hex), Ldark = relLum(DARK_LABEL);
  const cWhite = (1.0 + 0.05) / (L + 0.05);          // contrast vs white
  const cDark = (L + 0.05) / (Ldark + 0.05);         // contrast vs near-black
  return cWhite >= cDark ? "#ffffff" : DARK_LABEL;
}
/* per-mount colors: same calm family, hues spread for clear separation */
const MOUNT_PALETTE = ["#548fc9", "#9d54c9", "#54c954", "#c98f54", "#c9549d", "#54c9ac"];
let mountColor = {};
const uidColorMap = new Map();
let uidCursor = 0;
function colorForUid(uid) {
  if (uid == null) return OTHER_COLOR;
  if (uidColorMap.has(uid)) return uidColorMap.get(uid);
  const c = uidCursor < PALETTE.length ? PALETTE[uidCursor++] : OTHER_COLOR;
  uidColorMap.set(uid, c); return c;
}
function assignColors(users) {
  uidColorMap.clear(); uidCursor = 0;
  [...(users || [])].sort((a, b) => b.bytes - a.bytes).forEach(u => colorForUid(u.uid));
  if (!uidColorMap.has(0)) colorForUid(0);
}

/* ---- State ---- */
let DATA = null;
let ownerByUid = new Map();
let nowUnix = Math.floor(Date.now() / 1000);
let treemapChart = null, usersChart = null;
let treemapInited = false, usersInited = false;
let currentMountIdx = 0;
let mountPaths = [];
let capUsedByMount = {};
let totalCapacityUsed = 1;
let currentTab = "treemap";
let userMountFilter = "", topMountFilter = "", staleMountFilter = "";

/* =========================================================================
   Data loading
   ========================================================================= */
async function tryFetch(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(url + " -> " + r.status);
  return r.json();
}
function isSafeHostToken(value) {
  return typeof value === "string" && /^[A-Za-z0-9._-]+$/.test(value) && value !== "." && value !== "..";
}
function normalizeHosts(input) {
  const rows = Array.isArray(input) ? input : [];
  const out = [];
  for (const row of rows) {
    const id = row && String(row.id || "").trim();
    const file = row && String(row.file || id).trim();
    if (!isSafeHostToken(id) || !isSafeHostToken(file)) continue;
    out.push({
      id,
      label: String(row.label || id),
      file,
      default: row.default === true,
      description: row.description ? String(row.description) : "",
    });
  }
  if (!out.length) return DEFAULT_HOSTS.slice();
  return out;
}
async function loadHostManifest() {
  try {
    HOSTS = normalizeHosts(await tryFetch("data/hosts.json"));
  } catch (e) {
    console.warn("[storage-viz] data/hosts.json unavailable; using default host manifest", e);
    HOSTS = DEFAULT_HOSTS.slice();
  }
  if (typeof globalThis !== "undefined") globalThis.HOSTS = HOSTS;
  return HOSTS;
}

function makeFetchError(url, status, body) {
  const err = new Error(url + " -> " + status);
  err.url = url;
  err.status = status;
  err.body = body;
  return err;
}
async function fetchJson(url, options) {
  const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw makeFetchError(url, response.status, body);
  return body;
}
async function loadSession() {
  return fetchJson("/api/session");
}
async function loadServerSummaries() {
  const body = await fetchJson("/api/servers");
  return Array.isArray(body && body.servers) ? body.servers : [];
}
function safeServerId(serverId) {
  if (!isSafeHostToken(serverId)) throw new Error("invalid server id");
  return serverId;
}
async function loadServerSnapshot(serverId) {
  return fetchJson("/api/servers/" + encodeURIComponent(safeServerId(serverId)) + "/snapshot");
}
async function loadServerJob(serverId) {
  return fetchJson("/api/servers/" + encodeURIComponent(safeServerId(serverId)) + "/job");
}
async function postServerRescan(serverId, csrfToken) {
  const headers = { "Content-Type": "application/json" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  return fetchJson("/api/servers/" + encodeURIComponent(safeServerId(serverId)) + "/rescan", {
    method: "POST",
    headers,
    body: "{}",
  });
}
async function loadOrderedSnapshotsForOverview(summaries, snapshotLoader) {
  const loader = snapshotLoader || loadServerSnapshot;
  const rows = Array.isArray(summaries) ? summaries : [];
  return Promise.all(rows.map(async (summary) => {
    const id = summary && summary.id ? String(summary.id) : "";
    try {
      return { id, snapshot: await loader(id), error: null };
    } catch (error) {
      return { id, snapshot: null, error };
    }
  }));
}
async function loadHost(host) {
  const candidates = ["data/" + host.file + ".json", "data/" + host.file + ".sample.json"];
  let lastErr = null;
  for (const url of candidates) {
    try { const j = await tryFetch(url); console.info("[storage-viz] loaded", url); return j; }
    catch (e) { lastErr = e; }
  }
  throw lastErr || new Error("no data file for " + host.id);
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, { DEFAULT_HOSTS, HOSTS, normalizeHosts, loadHostManifest, loadHost, loadSession, loadServerSummaries, loadServerSnapshot, loadServerJob, postServerRescan, loadOrderedSnapshotsForOverview, safeServerId, isSafeHostToken });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { DEFAULT_HOSTS, normalizeHosts, loadSession, loadServerSummaries, loadServerSnapshot, loadServerJob, postServerRescan, loadOrderedSnapshotsForOverview, safeServerId, isSafeHostToken };
}

/* =========================================================================
   Header + capacity hero (rendered immediately for fast first paint)
   ========================================================================= */
function capColor(p) { return p >= 90 ? "var(--crit)" : p >= 75 ? "var(--warn)" : "var(--ok)"; }
function renderHeader() {
  document.getElementById("h-host").textContent = DATA.hostname || "—";
  document.getElementById("h-scan").textContent = fmtDate(DATA.scan_started_unix) +
    (DATA.scan_duration_sec ? " · " + DATA.scan_duration_sec.toFixed(0) + "s" : "");
  document.getElementById("h-scanner").textContent =
    "scanner v" + (DATA.scanner_version || "?") + (DATA.run_as_root ? " · root scan" : " · non-root");

  const warn = document.getElementById("warnBanner");
  const msgs = [];
  if (DATA.run_as_root === false) msgs.push("run_as_root=false → some directories were not scanned and are hidden from totals.");
  const nB = (DATA.blocked || []).length;
  if (nB > 0) {
    const list = DATA.blocked.slice(0, 6).map(b => b.path + " (" + b.reason + ")").join(", ");
    msgs.push(nB + " director" + (nB === 1 ? "y" : "ies") + " could not be read: " + escapeHtml(list) + (nB > 6 ? " …" : ""));
  }
  warn.classList.toggle("show", msgs.length > 0);
  warn.innerHTML = msgs.length ? "⚠ " + msgs.join("<br>⚠ ") : "";

  const caps = document.getElementById("caps");
  caps.innerHTML = ""; totalCapacityUsed = 0; capUsedByMount = {};
  for (const m of DATA.mounts || []) {
    capUsedByMount[m.path] = m.df_used || 0; totalCapacityUsed += (m.df_used || 0);
    const pct = (m.df_use_pct != null) ? m.df_use_pct : (m.df_total ? Math.round(m.df_used / m.df_total * 100) : 0);
    const color = capColor(pct);
    const div = document.createElement("div");
    div.className = "cap";
    div.innerHTML =
      '<div class="cap-top"><span><span class="cap-path">' + escapeHtml(m.path) + '</span>' +
        '<span class="cap-fs">' + escapeHtml(m.fstype || "") + '</span></span>' +
        '<span class="cap-pct" style="color:' + color + '">' + pct + '<span style="font-size:15px">%</span></span></div>' +
      '<div class="cap-bar"><div class="cap-fill" style="width:' + Math.min(100, pct) + '%;background:' + color + '"></div></div>' +
      '<div class="cap-sub"><span class="figure">' + humanBytes(m.df_used) + '</span> used / <span class="figure">' +
        humanBytes(m.df_total) + '</span> · <span class="figure">' + humanBytes(m.df_avail) + '</span> free</div>';
    caps.appendChild(div);
  }
  if (totalCapacityUsed <= 0) totalCapacityUsed = 1;
}
