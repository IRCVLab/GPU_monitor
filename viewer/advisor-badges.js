"use strict";

function advisorBadgeEscape(value) {
  if (typeof htmlEscape === "function") return advisorBadgeEscape(value);
  if (typeof escapeHtml === "function") return escapeHtml(value);
  return String(value == null ? "" : value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function recommendationMatchesPath(rec, path) {
  if (!rec || !path) return false;
  const target = String(rec.target_path || "").replace(/\/+$/g, "") || "/";
  const p = String(path || "").replace(/\/+$/g, "") || "/";
  if (!target || target === "/") return false;
  if (p === target || p.startsWith(target + "/")) return true;
  return ((rec.related_paths || []).some(r => {
    const rel = String(r || "").replace(/\/+$/g, "") || "/";
    return rel !== "/" && (p === rel || p.startsWith(rel + "/"));
  }));
}

function advisorEscapeHtml(value) {
  if (typeof escapeHtml === "function") return escapeHtml(value);
  return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function advisorPathNormalize(path) {
  if (typeof normalizeAdvisorPath === "function") return normalizeAdvisorPath(path);
  const raw = String(path || "").trim();
  return raw.replace(/\/+/g, "/").replace(/\/+$/g, "") || raw;
}

function advisorSetRecommendations(payload) {
  if (typeof advisorState !== "undefined") {
    advisorState.recommendations = Array.isArray(payload) ? payload : ((payload && payload.recommendations) || []);
  }
  return (typeof advisorState !== "undefined" && advisorState.recommendations) || [];
}
function advisorRecommendationsForPath(path) {
  return recommendationsForPath((typeof advisorState !== "undefined" && advisorState.recommendations) || [], path);
}
function advisorBadgeHtmlForPath(path) {
  const recs = advisorRecommendationsForPath(path).slice(0, 3);
  if (!recs.length) return "";
  return '<span class="ai-badges">' + recs.map(rec =>
    '<button type="button" class="ai-badge" data-advisor-rec="' + advisorBadgeEscape(String(rec.id)) + '" title="' + advisorBadgeEscape(rec.reason_short || advisorBadgeLabel(rec)) + '">' +
    advisorBadgeEscape(advisorBadgeLabel(rec)) + '</button>'
  ).join("") + '</span>';
}
function advisorRemoveBadges(root) {
  if (!root || !root.querySelectorAll) return;
  root.querySelectorAll(".ai-badges").forEach(el => el.remove && el.remove());
}
function annotateTreemapTileWithAdvisor(tile, path) {
  if (!tile || !path || !tile.appendChild || typeof document === "undefined") return;
  if (tile.querySelectorAll) tile.querySelectorAll(".tm-ai-badges").forEach(el => el.remove && el.remove());
  const recs = advisorRecommendationsForPath(path).slice(0, 2);
  if (!recs.length) return;
  const wrap = document.createElement("div");
  wrap.className = "ai-badges tm-ai-badges";
  wrap.dataset.advisorPath = path;
  recs.forEach(rec => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ai-badge" + advisorBadgeClass(rec);
    btn.dataset.advisorRecId = rec.id;
    btn.title = rec.reason_short || rec.reason_detail || advisorBadgeText(rec);
    btn.textContent = advisorBadgeText(rec);
    wrap.appendChild(btn);
  });
  tile.appendChild(wrap);
  tile.classList.add("has-ai-advice");
}
function advisorRefreshExistingTreemapBadges(root) {
  if (typeof document === "undefined") return;
  const scope = root || document;
  if (!scope.querySelectorAll) return;
  scope.querySelectorAll(".tmtile[data-cleanup-path]").forEach(tile => {
    annotateTreemapTileWithAdvisor(tile, tile.dataset && tile.dataset.cleanupPath);
  });
}
function advisorRefreshExistingPathBadges(root) {
  if (typeof document === "undefined") return;
  const scope = root || document;
  if (!scope.querySelectorAll) return;
  scope.querySelectorAll(".pathwrap[data-path]").forEach(wrap => {
    if (wrap.querySelectorAll) wrap.querySelectorAll(":scope > .ai-badges").forEach(el => el.remove && el.remove());
    const html = advisorBadgesHtml(wrap.dataset && wrap.dataset.path);
    if (html && wrap.insertAdjacentHTML) wrap.insertAdjacentHTML("beforeend", html);
  });
}
function advisorRefreshAnnotations() {
  advisorRefreshExistingTreemapBadges();
  advisorRefreshExistingPathBadges();
}

if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, {
    recommendationMatchesPath,
    recommendationsForPath,
    advisorSetRecommendations,
    advisorRecommendationsForPath,
    advisorBadgeHtmlForPath,
    advisorBadgesHtml: advisorBadgeHtmlForPath,
    appendAdvisorBadges,
    refreshAdvisorBadges,
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { recommendationMatchesPath, recommendationsForPath, advisorSetRecommendations, advisorRecommendationsForPath, advisorBadgeHtmlForPath, advisorBadgesHtml: advisorBadgeHtmlForPath };
}
