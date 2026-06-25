"use strict";

/* =========================================================================
   AI Advisor badges — path matching + compact row/tile annotations
   ========================================================================= */
let advisorActiveRecommendations = [];

function advisorEscapeHtml(value) {
  if (typeof escapeHtml === "function") return escapeHtml(value);
  return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function advisorPathNormalize(path) {
  if (typeof normalizeAdvisorPath === "function") return normalizeAdvisorPath(path);
  const raw = String(path || "").trim();
  return raw.replace(/\/+/g, "/").replace(/\/+$/g, "") || raw;
}
function advisorPathsMatch(path, target) {
  if (typeof advisorPathMatches === "function") return advisorPathMatches(path, target);
  const p = advisorPathNormalize(path), t = advisorPathNormalize(target);
  return !!(p && t && (p === t || p.startsWith(t + "/") || t.startsWith(p + "/")));
}
function advisorSetRecommendations(payloadOrRecommendations) {
  const rows = Array.isArray(payloadOrRecommendations)
    ? payloadOrRecommendations
    : (payloadOrRecommendations && Array.isArray(payloadOrRecommendations.recommendations) ? payloadOrRecommendations.recommendations : []);
  advisorActiveRecommendations = rows.filter(rec => rec && rec.id && rec.target_path);
  advisorRefreshAnnotations();
  return advisorActiveRecommendations;
}
function advisorGetRecommendations() {
  return advisorActiveRecommendations.slice();
}
function advisorRecommendationsForPath(path) {
  const p = advisorPathNormalize(path);
  if (!p) return [];
  return advisorActiveRecommendations.filter(rec => advisorPathsMatch(p, rec.target_path));
}
function advisorBadgeText(rec) {
  return String((rec && rec.badge) || ("AI: " + ((rec && rec.action) || "advice")));
}
function advisorBadgeClass(rec) {
  const risk = String(rec && rec.risk || "medium").toLowerCase();
  const priority = String(rec && rec.priority || "medium").toLowerCase();
  if (risk === "high" || priority === "critical") return " high";
  if (risk === "low" || priority === "low") return " low";
  return "";
}
function advisorBadgesHtml(path, limit) {
  const recs = advisorRecommendationsForPath(path).slice(0, limit || 2);
  if (!recs.length) return "";
  return '<span class="ai-badges" data-advisor-path="' + advisorEscapeHtml(path) + '">' + recs.map(rec =>
    '<button type="button" class="ai-badge' + advisorBadgeClass(rec) + '" data-advisor-rec-id="' + advisorEscapeHtml(rec.id) + '" title="' +
      advisorEscapeHtml(rec.reason_short || rec.reason_detail || advisorBadgeText(rec)) + '">' + advisorEscapeHtml(advisorBadgeText(rec)) + '</button>'
  ).join("") + (advisorRecommendationsForPath(path).length > recs.length ? '<span class="ai-badge-more">+' + (advisorRecommendationsForPath(path).length - recs.length) + '</span>' : '') + '</span>';
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
    advisorSetRecommendations,
    advisorGetRecommendations,
    advisorRecommendationsForPath,
    advisorBadgesHtml,
    annotateTreemapTileWithAdvisor,
    advisorRefreshAnnotations,
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    advisorSetRecommendations,
    advisorGetRecommendations,
    advisorRecommendationsForPath,
    advisorBadgesHtml,
    annotateTreemapTileWithAdvisor,
  };
}
