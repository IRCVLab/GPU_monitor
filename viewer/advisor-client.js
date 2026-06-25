"use strict";

/* =========================================================================
   AI Advisor client — endpoint calls + static-safe exclusions
   ========================================================================= */
const ADVISOR_EXCLUSIONS_PREFIX = "storage-viz:ai-advisor:exclusions:v1:";
const ADVISOR_DISABLED_STATUS = {
  enabled: false,
  provider: "disabled",
  model: "",
  cached: false,
  message: "AI Advisor is disabled or unavailable in this viewer mode.",
};

function advisorHostId(hostOrId) {
  if (typeof hostOrId === "string") return hostOrId || "default";
  if (hostOrId && hostOrId.id) return String(hostOrId.id);
  return "default";
}
function advisorStorageKey(hostOrId) {
  return ADVISOR_EXCLUSIONS_PREFIX + advisorHostId(hostOrId);
}
function advisorNowUnix() { return Math.floor(Date.now() / 1000); }
function advisorSafeStorage(storage) {
  if (storage) return storage;
  try {
    if (typeof localStorage !== "undefined") return localStorage;
  } catch (_) {}
  return null;
}
function defaultAdvisorExclusions(hostOrId) {
  return { version: 1, host_id: advisorHostId(hostOrId), items: [] };
}
function normalizeAdvisorExclusions(value, hostOrId) {
  const hostId = advisorHostId(hostOrId || (value && value.host_id));
  const rows = Array.isArray(value && value.items) ? value.items : [];
  const items = rows.map(item => {
    if (!item || typeof item !== "object") return null;
    const type = String(item.type || "").trim();
    if (!["recommendation", "path", "pattern", "action", "category"].includes(type)) return null;
    const out = { type, created_at: Number(item.created_at) || advisorNowUnix() };
    if (type === "recommendation") out.id = String(item.id || "");
    if (type === "path") out.path = normalizeAdvisorPath(item.path || "");
    if (type === "pattern") out.pattern = String(item.pattern || "");
    if (type === "action") out.action = String(item.action || "");
    if (type === "category") out.category = String(item.category || "");
    if ((type === "recommendation" && !out.id) ||
        (type === "path" && !out.path) ||
        (type === "pattern" && !out.pattern) ||
        (type === "action" && !out.action) ||
        (type === "category" && !out.category)) return null;
    return out;
  }).filter(Boolean);
  return { version: 1, host_id: hostId, items };
}
function loadAdvisorExclusions(hostOrId, storage) {
  const store = advisorSafeStorage(storage);
  if (!store || !store.getItem) return defaultAdvisorExclusions(hostOrId);
  try {
    const raw = store.getItem(advisorStorageKey(hostOrId));
    return raw ? normalizeAdvisorExclusions(JSON.parse(raw), hostOrId) : defaultAdvisorExclusions(hostOrId);
  } catch (_) {
    return defaultAdvisorExclusions(hostOrId);
  }
}
function saveAdvisorExclusions(hostOrId, exclusions, storage) {
  const normalized = normalizeAdvisorExclusions(exclusions, hostOrId);
  const store = advisorSafeStorage(storage);
  if (store && store.setItem) {
    try { store.setItem(advisorStorageKey(hostOrId), JSON.stringify(normalized)); }
    catch (_) {}
  }
  return normalized;
}
function addAdvisorExclusionItem(hostOrId, exclusions, item, storage) {
  const normalized = normalizeAdvisorExclusions(exclusions, hostOrId);
  const next = normalizeAdvisorExclusions({ items: [Object.assign({ created_at: advisorNowUnix() }, item || {})] }, hostOrId).items[0];
  if (!next) return normalized;
  const sig = JSON.stringify(Object.keys(next).sort().reduce((acc, key) => { if (key !== "created_at") acc[key] = next[key]; return acc; }, {}));
  const exists = normalized.items.some(row => {
    const rowSig = JSON.stringify(Object.keys(row).sort().reduce((acc, key) => { if (key !== "created_at") acc[key] = row[key]; return acc; }, {}));
    return rowSig === sig;
  });
  if (!exists) normalized.items.push(next);
  return saveAdvisorExclusions(hostOrId, normalized, storage);
}
function normalizeAdvisorStatus(status) {
  if (!status || typeof status !== "object") return Object.assign({}, ADVISOR_DISABLED_STATUS);
  return {
    enabled: status.enabled === true,
    provider: String(status.provider || (status.enabled ? "rule-only" : "disabled")),
    model: String(status.model || ""),
    cached: status.cached === true,
    mode: status.mode ? String(status.mode) : undefined,
    message: String(status.message || (status.enabled ? "AI Advisor is ready." : ADVISOR_DISABLED_STATUS.message)),
  };
}
async function fetchAdvisorStatus(fetchImpl) {
  const doFetch = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  if (!doFetch) return Object.assign({}, ADVISOR_DISABLED_STATUS);
  try {
    const response = await doFetch("/ai/status", { cache: "no-store" });
    if (!response || !response.ok) return Object.assign({}, ADVISOR_DISABLED_STATUS);
    return normalizeAdvisorStatus(await response.json());
  } catch (_) {
    return Object.assign({}, ADVISOR_DISABLED_STATUS);
  }
}
async function requestAdvisorRecommendations(options, fetchImpl) {
  const doFetch = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  if (!doFetch) throw new Error("AI Advisor endpoint is unavailable in static mode");
  const body = {
    host_id: advisorHostId(options && options.host_id),
    exclusions: normalizeAdvisorExclusions(options && options.exclusions, options && options.host_id),
    language: (options && options.language) || "auto",
    max_items: Number(options && options.max_items) || 25,
    force_refresh: !!(options && options.force_refresh),
  };
  const response = await doFetch("/ai/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let payload = null;
  try { payload = await response.json(); } catch (_) {}
  if (!response.ok) {
    const msg = payload && (payload.error || payload.message);
    throw new Error(msg || ("AI Advisor request failed with HTTP " + response.status));
  }
  return normalizeAdvisorPayload(payload, body.exclusions);
}
function normalizeAdvisorPath(path) {
  const raw = String(path || "").trim();
  if (!raw) return "";
  return raw.replace(/\/+/g, "/").replace(/\/+$/g, "") || "/";
}
function advisorPathMatches(path, target) {
  const p = normalizeAdvisorPath(path);
  const t = normalizeAdvisorPath(target);
  if (!p || !t) return false;
  return p === t || p.startsWith(t + "/") || t.startsWith(p + "/");
}
function advisorGlobToRegExp(pattern) {
  const escaped = String(pattern || "").replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*\*/g, "\u0000").replace(/\*/g, "[^/]*").replace(/\u0000/g, ".*");
  return new RegExp("^" + escaped + "$");
}
function advisorRecommendationExcluded(rec, exclusions) {
  const items = normalizeAdvisorExclusions(exclusions, rec && rec.host_id).items;
  const id = String(rec && rec.id || "");
  const action = String(rec && rec.action || "");
  const category = String(rec && rec.category || "");
  const path = normalizeAdvisorPath(rec && rec.target_path);
  return items.some(item => {
    if (item.type === "recommendation") return item.id === id;
    if (item.type === "action") return item.action === action;
    if (item.type === "category") return item.category === category;
    if (item.type === "path") return advisorPathMatches(path, item.path);
    if (item.type === "pattern") {
      try { return advisorGlobToRegExp(item.pattern).test(path); }
      catch (_) { return false; }
    }
    return false;
  });
}
function normalizeAdvisorRecommendation(rec, idx) {
  if (!rec || typeof rec !== "object") return null;
  const target = normalizeAdvisorPath(rec.target_path || rec.path || "");
  const id = String(rec.id || (rec.action || "advice") + ":" + target + ":" + idx);
  if (!target || !id) return null;
  return {
    id,
    action: String(rec.action || "investigate"),
    category: String(rec.category || "other"),
    target_path: target,
    related_paths: Array.isArray(rec.related_paths) ? rec.related_paths.map(normalizeAdvisorPath).filter(Boolean) : [],
    mount: rec.mount ? String(rec.mount) : "",
    owner: rec.owner ? String(rec.owner) : "",
    bytes: Number(rec.bytes) || 0,
    priority: String(rec.priority || "medium"),
    confidence: Math.max(0, Math.min(1, Number(rec.confidence == null ? 0.5 : rec.confidence))),
    risk: String(rec.risk || "medium"),
    badge: String(rec.badge || ("AI: " + (rec.category || rec.action || "advice"))),
    reason_short: String(rec.reason_short || rec.reason || "Review this storage finding."),
    reason_detail: String(rec.reason_detail || rec.reason_short || rec.reason || "Evidence-backed storage advisor recommendation."),
    evidence: Array.isArray(rec.evidence) ? rec.evidence : [],
    suggested_next_step: String(rec.suggested_next_step || "inspect-owner"),
  };
}
function normalizeAdvisorPayload(payload, exclusions) {
  const rows = Array.isArray(payload && payload.recommendations) ? payload.recommendations : [];
  const recommendations = rows.map(normalizeAdvisorRecommendation).filter(Boolean)
    .filter(rec => !advisorRecommendationExcluded(rec, exclusions));
  return {
    schema_version: Number(payload && payload.schema_version) || 1,
    host_id: String(payload && payload.host_id || ""),
    snapshot_fingerprint: String(payload && payload.snapshot_fingerprint || ""),
    generated_at_unix: Number(payload && payload.generated_at_unix) || advisorNowUnix(),
    mode: String(payload && payload.mode || "rule-only"),
    summary: Object.assign({ health: "ok", headline: "No AI recommendations yet.", top_drivers: [] }, payload && payload.summary || {}),
    recommendations,
  };
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, {
    ADVISOR_EXCLUSIONS_PREFIX,
    ADVISOR_DISABLED_STATUS,
    advisorHostId,
    advisorStorageKey,
    normalizeAdvisorPath,
    advisorPathMatches,
    defaultAdvisorExclusions,
    normalizeAdvisorExclusions,
    loadAdvisorExclusions,
    saveAdvisorExclusions,
    addAdvisorExclusionItem,
    normalizeAdvisorStatus,
    fetchAdvisorStatus,
    requestAdvisorRecommendations,
    advisorRecommendationExcluded,
    normalizeAdvisorRecommendation,
    normalizeAdvisorPayload,
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ADVISOR_EXCLUSIONS_PREFIX,
    ADVISOR_DISABLED_STATUS,
    advisorHostId,
    advisorStorageKey,
    normalizeAdvisorPath,
    advisorPathMatches,
    defaultAdvisorExclusions,
    normalizeAdvisorExclusions,
    loadAdvisorExclusions,
    saveAdvisorExclusions,
    addAdvisorExclusionItem,
    normalizeAdvisorStatus,
    fetchAdvisorStatus,
    requestAdvisorRecommendations,
    advisorRecommendationExcluded,
    normalizeAdvisorRecommendation,
    normalizeAdvisorPayload,
  };
}
