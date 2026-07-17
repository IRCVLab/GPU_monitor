"use strict";

/* Optional AI Advisor UI: global run/status, compact grouped recommendations, details, exclusions, safe cleanup handoff. */
function advisorEscape(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function advisorBytes(value) {
  return typeof humanBytes === "function" ? humanBytes(Number(value) || 0) : String(value || 0) + " B";
}
function advisorPathDepth(path) {
  return String(path || "").split("/").filter(Boolean).length;
}
function isAdvisorSafeDeleteRecommendation(rec) {
  if (!rec || rec.action !== "delete" || rec.suggested_next_step !== "review-delete-command") return false;
  if (!rec.target_path || !String(rec.target_path).startsWith("/") || String(rec.target_path).includes("\0")) return false;
  if (typeof isTopLevelCleanupPath !== "function" && advisorPathDepth(rec.target_path) <= 1) return false;
  if (typeof isTopLevelCleanupPath === "function" && isTopLevelCleanupPath(rec.target_path)) return false;
  return true;
}
function advisorCurrentHostId() {
  const sel = typeof document !== "undefined" ? document.getElementById("hostSel") : null;
  return sel && sel.value ? sel.value : (typeof HOSTS !== "undefined" && HOSTS[0] ? HOSTS[0].id : "hinton");
}
function advisorRows() {
  return (typeof advisorState !== "undefined" && Array.isArray(advisorState.recommendations)) ? advisorState.recommendations : [];
}
function advisorFindRecommendation(id) {
  return advisorRows().find(rec => rec && rec.id === id) || null;
}
function advisorHealthClass(health) {
  const value = String(health || (advisorState && advisorState.payload && advisorState.payload.summary && advisorState.payload.summary.health) || "ok");
  return value === "critical" ? "crit" : value === "warning" ? "warn" : "ok";
}
function advisorStatusLabel() {
  if (advisorState && advisorState.running) return "분석 중";
  if (!advisorState || !advisorState.status || !advisorState.status.enabled) return "비활성";
  if (advisorState.payload && advisorState.payload.mode === "rule-only") return "규칙 기반";
  if (advisorState && advisorState.error) return "오류";
  return "준비됨";
}
function advisorModeLabel() {
  const payload = advisorState && advisorState.payload;
  if (!payload) return advisorState && advisorState.status && advisorState.status.provider ? advisorState.status.provider : "대기";
  if (payload.mode === "rule+llm") return "LLM 2-pass";
  if (payload.mode === "rule-only") return "규칙 기반 fallback";
  if (payload.mode === "mock") return "테스트 mock";
  return payload.mode || "분석 완료";
}
function advisorRecommendationCount() { return advisorRows().length; }
function renderAdvisorGlobalControls() {
  if (typeof document === "undefined") return;
  const run = document.getElementById("advisorGlobalRun");
  const pill = document.getElementById("advisorGlobalStatus");
  const count = document.getElementById("advisorGlobalCount");
  const scanWithLlm = document.getElementById("rescanWithLlm");
  const label = advisorStatusLabel();
  if (pill) {
    pill.className = "advisor-global-status " + (label === "비활성" ? "disabled" : label === "오류" ? "failed" : advisorState && advisorState.running ? "running" : "ready");
    pill.textContent = "AI " + label;
    pill.title = (advisorState && (advisorState.error || advisorState.warning || (advisorState.status && advisorState.status.message))) || "";
  }
  if (count) {
    const n = advisorRecommendationCount();
    count.textContent = n ? (n.toLocaleString() + "개 추천") : advisorModeLabel();
  }
  if (run) {
    run.disabled = !!(advisorState && advisorState.running) || !(advisorState && advisorState.status && advisorState.status.enabled);
    run.textContent = advisorState && advisorState.running ? "AI 분석 중…" : "AI 분석";
    run.title = "현재 host를 분석하고 treemap/top/stale에 추천 배지를 표시합니다";
  }
  if (scanWithLlm) {
    const enabled = !!(advisorState && advisorState.status && advisorState.status.enabled);
    scanWithLlm.disabled = false;
    scanWithLlm.title = enabled
      ? "다음 Rescan이 끝나면 로컬 LLM 분석을 자동 실행합니다"
      : "항상 선택 가능합니다. AI가 비활성/미연결이면 scan 후 상태에 오류가 표시됩니다";
  }
}
function renderAdvisorSummaryHtml() {
  const payload = advisorState && advisorState.payload;
  if (!payload) {
    return '<div class="advisor-empty">상단의 <b>AI 분석</b>을 누르면 treemap, Top files, Stale 표에 추천 배지가 붙습니다. 이 탭은 상세/제외 관리용입니다.</div>';
  }
  const summary = payload.summary || {};
  const drivers = summary.top_drivers || [];
  const mode = advisorModeLabel();
  const error = advisorState && advisorState.error ? '<div class="advisor-error">' + advisorEscape(advisorState.error) + '</div>' : '';
  const warning = advisorState && advisorState.warning ? '<div class="advisor-warning">' + advisorEscape(advisorState.warning) + '</div>' : '';
  return '<div class="advisor-health ' + advisorHealthClass(summary.health) + '">' + advisorEscape(summary.health || "ok") + '</div>' +
    '<div><b>' + advisorEscape(summary.headline || "AI 정리 추천") + '</b><br>' +
    '<span class="muted">모드: ' + advisorEscape(mode) + ' · 출력: 한국어 · 결과는 모든 탭에 배지로 표시됩니다.</span><br>' +
    drivers.slice(0, 3).map(d => '<span class="advisor-driver">' + advisorEscape(d) + '</span>').join("") + '</div>' + warning + error;
}
function advisorRecommendationHtml(rec) {
  const canDelete = isAdvisorSafeDeleteRecommendation(rec);
  return '<article class="advisor-rec" data-advisor-rec="' + advisorEscape(rec.id) + '">' +
    '<div class="advisor-rec-head"><button type="button" class="linklike ai-badge" data-advisor-detail="' + advisorEscape(rec.id) + '">' + advisorEscape(rec.badge || rec.category || rec.action || "AI") + '</button>' +
    '<span class="advisor-pill">' + advisorEscape((typeof advisorActionLabel === "function" ? advisorActionLabel(rec.action) : rec.action) || "검토") + '</span><span class="advisor-pill risk-' + advisorEscape(rec.risk || "medium") + '">' + advisorEscape(rec.risk || "medium") + '</span></div>' +
    '<div class="advisor-target"><bdi>' + advisorEscape(rec.target_path || "") + '</bdi></div>' +
    '<div class="advisor-reason">' + advisorEscape(rec.reason_short || "") + '</div>' +
    '<div class="advisor-actions"><button type="button" data-advisor-detail="' + advisorEscape(rec.id) + '">자세히</button>' +
    (canDelete ? '<button type="button" data-advisor-select="' + advisorEscape(rec.id) + '">삭제 후보로 선택</button>' : '') +
    '<button type="button" data-advisor-exclude="recommendation" data-advisor-rec="' + advisorEscape(rec.id) + '">제외</button></div>' +
    '</article>';
}
function renderAdvisorListHtml() {
  const rows = advisorRows();
  if (!rows.length) return '<div class="advisor-empty">아직 활성 추천이 없습니다. AI 분석을 실행했거나 제외 조건이 너무 넓은지 확인하세요.</div>';
  const groups = typeof groupAdvisorRecommendations === "function" ? groupAdvisorRecommendations(rows) : [{ action: "review", recommendations: rows, bytes: rows.reduce((s, r) => s + (Number(r.bytes) || 0), 0) }];
  return '<div class="advisor-group-list">' + groups.map(group => {
    const label = typeof advisorActionLabel === "function" ? advisorActionLabel(group.action) : group.action;
    const recs = group.recommendations.slice(0, 8).map(advisorRecommendationHtml).join("");
    const more = group.recommendations.length > 8 ? '<div class="advisor-more">외 ' + (group.recommendations.length - 8).toLocaleString() + '개는 Top/Stale/treemap 배지에서 확인하세요.</div>' : '';
    return '<section class="advisor-group"><div class="advisor-group-head"><b>' + advisorEscape(label) + '</b><span>' + group.recommendations.length.toLocaleString() + '개 · ' + advisorBytes(group.bytes) + '</span></div><div class="advisor-list">' + recs + more + '</div></section>';
  }).join("") + '</div>';
}
function renderAdvisorExclusionsHtml() {
  const rows = (advisorState && Array.isArray(advisorState.exclusions)) ? advisorState.exclusions : [];
  if (!rows.length) return '<span class="muted">제외 조건 없음</span>';
  return rows.map(item => {
    const value = item.id || item.path || item.pattern || item.action || item.category || "";
    return '<span class="advisor-exclusion">' + advisorEscape(item.type || "exclude") + ': <code>' + advisorEscape(value) + '</code></span>';
  }).join("") + '<button type="button" data-advisor-clear-exclusions>제외 모두 해제</button>';
}
function renderAdvisorPanel() {
  if (typeof document === "undefined") return;
  renderAdvisorGlobalControls();
  const status = document.getElementById("advisorStatus");
  const run = document.getElementById("advisorRun");
  const summary = document.getElementById("advisorSummary");
  const list = document.getElementById("advisorList");
  const exclusions = document.getElementById("advisorExclusions");
  if (status) {
    const label = advisorStatusLabel();
    status.className = "advisor-status " + (label === "비활성" ? "disabled" : "enabled");
    status.textContent = "AI Advisor: " + label;
    status.title = (advisorState && (advisorState.error || advisorState.warning || (advisorState.status && advisorState.status.message))) || "";
  }
  if (run) run.disabled = !!(advisorState && advisorState.running) || !(advisorState && advisorState.status && advisorState.status.enabled);
  if (summary) summary.innerHTML = renderAdvisorSummaryHtml();
  if (list) list.innerHTML = renderAdvisorListHtml();
  if (exclusions) exclusions.innerHTML = renderAdvisorExclusionsHtml();
}
async function advisorRefreshStatus() {
  if (typeof fetchAdvisorStatus === "function") await fetchAdvisorStatus();
  renderAdvisorPanel();
}
async function advisorRunNow(forceRefresh) {
  if (typeof runAdvisor === "function") await runAdvisor({ force_refresh: !!forceRefresh, language: "ko" });
  renderAdvisorPanel();
  if (typeof refreshAdvisorBadges === "function") refreshAdvisorBadges();
}
function showAdvisorDetail(id) {
  const rec = advisorFindRecommendation(id);
  const drawer = document.getElementById("advisorDetail");
  if (!rec || !drawer) return;
  const evidence = Array.isArray(rec.evidence) ? rec.evidence : [];
  drawer.classList.add("show");
  drawer.setAttribute("aria-hidden", "false");
  drawer.innerHTML = '<div class="advisor-detail-head"><h3>' + advisorEscape(rec.badge || rec.category || "AI 추천") + '</h3><button type="button" id="advisorDetailClose">×</button></div>' +
    '<p><b>작업:</b> ' + advisorEscape(typeof advisorActionLabel === "function" ? advisorActionLabel(rec.action) : rec.action) + ' · <b>위험:</b> ' + advisorEscape(rec.risk) + ' · <b>신뢰도:</b> ' + Math.round(Number(rec.confidence || 0) * 100) + '%</p>' +
    '<p><b>크기:</b> ' + advisorBytes(rec.bytes) + '</p>' +
    '<p><b>대상:</b> <bdi>' + advisorEscape(rec.target_path) + '</bdi></p>' +
    (rec.related_paths && rec.related_paths.length ? '<p><b>관련:</b> ' + rec.related_paths.slice(0, 6).map(p => '<bdi>' + advisorEscape(p) + '</bdi>').join(', ') + '</p>' : '') +
    '<p>' + advisorEscape(rec.reason_detail || rec.reason_short || '') + '</p>' +
    '<ul class="advisor-evidence">' + evidence.slice(0, 8).map(ev => '<li><b>' + advisorEscape(ev.label || ev.type || '근거') + ':</b> ' + advisorEscape(String(ev.value)) + '</li>').join('') + '</ul>' +
    '<div class="advisor-actions">' + (isAdvisorSafeDeleteRecommendation(rec) ? '<button type="button" data-advisor-select="' + advisorEscape(rec.id) + '">삭제 후보로 선택</button>' : '') +
    '<button type="button" data-advisor-exclude="recommendation" data-advisor-rec="' + advisorEscape(rec.id) + '">이 추천 제외</button>' +
    '<button type="button" data-advisor-exclude="path" data-advisor-rec="' + advisorEscape(rec.id) + '">이 경로 제외</button>' +
    '<button type="button" data-advisor-exclude="action" data-advisor-rec="' + advisorEscape(rec.id) + '">이 작업 유형 제외</button></div>';
  const close = document.getElementById("advisorDetailClose");
  if (close) close.onclick = hideAdvisorDetail;
}
function hideAdvisorDetail() {
  const drawer = typeof document !== "undefined" ? document.getElementById("advisorDetail") : null;
  if (drawer) { drawer.classList.remove("show"); drawer.setAttribute("aria-hidden", "true"); }
}
function selectAdvisorDeleteCandidate(id) {
  const rec = advisorFindRecommendation(id);
  if (!rec || !isAdvisorSafeDeleteRecommendation(rec) || typeof setCleanupSelectedItem !== "function") return false;
  const selected = setCleanupSelectedItem({ path: rec.target_path, bytes: rec.bytes || 0, owner: rec.owner || "", source: "ai-advisor" }, true);
  if (typeof renderCleanupPanel === "function") renderCleanupPanel();
  return selected;
}
function advisorAddExclusion(type, id) {
  const rec = advisorFindRecommendation(id);
  if (!rec) return;
  const exclusion = type === "path" ? { type: "path", path: rec.target_path } : type === "action" ? { type: "action", action: rec.action } : { type: "recommendation", id: rec.id };
  if (typeof addAdvisorExclusion === "function") addAdvisorExclusion(advisorCurrentHostId(), exclusion);
  renderAdvisorPanel();
}
function advisorClearExclusions() {
  if (typeof clearAdvisorExclusions === "function") clearAdvisorExclusions(advisorCurrentHostId());
  renderAdvisorPanel();
}
function bindAdvisorUi() {
  if (typeof document === "undefined" || document._advisorUiBound) return;
  document._advisorUiBound = true;
  document.addEventListener("click", e => {
    const target = e.target && e.target.closest ? e.target.closest("[data-advisor-detail], [data-advisor-select], [data-advisor-exclude], [data-advisor-clear-exclusions]") : null;
    if (!target) return;
    if (target.dataset.advisorDetail) { e.stopPropagation(); showAdvisorDetail(target.dataset.advisorDetail); return; }
    if (target.dataset.advisorSelect) { e.stopPropagation(); selectAdvisorDeleteCandidate(target.dataset.advisorSelect); return; }
    if (target.dataset.advisorExclude) { e.stopPropagation(); advisorAddExclusion(target.dataset.advisorExclude, target.dataset.advisorRec || target.dataset.advisorDetail || ""); return; }
    if (target.dataset.advisorClearExclusions) { e.stopPropagation(); advisorClearExclusions(); }
  });
  const run = document.getElementById("advisorRun");
  if (run) run.onclick = () => advisorRunNow(false);
  const globalRun = document.getElementById("advisorGlobalRun");
  if (globalRun) globalRun.onclick = () => advisorRunNow(false);
}
function onAdvisorHostChanged(host, data) {
  if (typeof advisorState !== "undefined") {
    advisorState.payload = null;
    advisorState.recommendations = [];
    advisorState.error = null;
    advisorState.warning = null;
    advisorState.host = host || null;
    advisorState.data = data || null;
    if (typeof loadAdvisorExclusions === "function") loadAdvisorExclusions((host && host.id) || advisorCurrentHostId());
  }
  if (typeof advisorSetRecommendations === "function") advisorSetRecommendations([]);
  renderAdvisorPanel();
  advisorRefreshStatus();
  if (typeof loadAdvisorLatest === "function") loadAdvisorLatest({ hostId: (host && host.id) || advisorCurrentHostId(), silent: true });
}
function initAdvisorUI() {
  bindAdvisorUi();
  renderAdvisorPanel();
  advisorRefreshStatus();
}
if (typeof globalThis !== "undefined") {
  Object.assign(globalThis, {
    initAdvisorUI,
    bindAdvisorUi,
    bindAdvisorUI: bindAdvisorUi,
    onAdvisorHostChanged,
    renderAdvisorPanel,
    renderAdvisorGlobalControls,
    advisorRefreshStatus,
    advisorRun: advisorRunNow,
    showAdvisorDetail,
    hideAdvisorDetail,
    advisorAddExclusion,
    advisorClearExclusions,
    selectAdvisorDeleteCandidate,
    advisorSelectDeleteCandidate: selectAdvisorDeleteCandidate,
    isAdvisorSafeDeleteRecommendation,
  });
}
if (typeof module !== "undefined" && module.exports) {
  module.exports = { advisorRecommendationHtml, advisorHealthClass, isAdvisorSafeDeleteRecommendation };
}
