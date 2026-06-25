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

function recommendationsForPath(recommendations, path) {
  return (Array.isArray(recommendations) ? recommendations : []).filter(rec => recommendationMatchesPath(rec, path));
}

function advisorBadgeLabel(rec) {
  return rec.badge || ("AI: " + (rec.category || rec.action || "review"));
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

function appendAdvisorBadges(el, path) {
  if (!el || typeof document === "undefined") return;
  const recs = recommendationsForPath((typeof advisorState !== "undefined" && advisorState.recommendations) || [], path).slice(0, 2);
  for (const rec of recs) {
    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "ai-badge ai-tile-badge";
    badge.textContent = advisorBadgeLabel(rec);
    badge.dataset.advisorRec = rec.id;
    badge.title = rec.reason_short || advisorBadgeLabel(rec);
    badge.onclick = (e) => {
      e.stopPropagation();
      if (typeof showAdvisorDetail === "function") showAdvisorDetail(rec.id);
    };
    el.appendChild(badge);
  }
}

function refreshAdvisorBadges() {
  if (typeof renderTreemap === "function" && typeof currentTab !== "undefined" && currentTab === "treemap") renderTreemap();
  if (typeof renderTopFiles === "function") renderTopFiles();
  if (typeof renderStaleWindow === "function") renderStaleWindow();
}

if (typeof document !== "undefined" && !document._advisorBadgeClickBound) {
  document._advisorBadgeClickBound = true;
  document.addEventListener("click", (e) => {
    const btn = e.target && e.target.closest ? e.target.closest(".ai-badge[data-advisor-rec]") : null;
    if (!btn) return;
    e.stopPropagation();
    if (typeof showAdvisorDetail === "function") showAdvisorDetail(btn.dataset.advisorRec);
  });
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
