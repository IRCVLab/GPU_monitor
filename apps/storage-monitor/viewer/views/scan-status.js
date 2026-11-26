import {clockSeconds} from '../format.js';
"use strict";

function scanNumber(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function activeRescanState(job) {
  if (!job) return null;
  return job.state === "running" ? job.state : null;
}

function isScanActive(summary) {
  const status = summary.scan_status;
  return !!(activeRescanState(summary.active_job) || (
    (status?.activity ?? status?.state) === "running" && scanNumber(status.observed_unix) !== null && !status.disconnected
  ));
}

function scanStatusModel(status, snapshot, now = scanNumber(status?.host_now_unix) ?? clockSeconds()) {
  const s = status || {}, data = snapshot || {};
  const disconnected = !!s.disconnected;
  const state = disconnected ? "unknown" : (s.state || "unknown");
  const running = state === "running";
  const started = scanNumber(s.started_unix);
  const snapshotFinished = scanNumber(data.scan_finished_unix);
  const completions = [scanNumber(s.last_finished_unix), scanNumber(s.published_finished_unix), snapshotFinished].filter(time => time !== null);
  const lastFinished = completions.length ? Math.max(...completions) : null;
  const duration = scanNumber(s.last_duration_sec) ?? scanNumber(data.scan_duration_sec);
  const roots = Array.isArray(data.selected_roots) ? data.selected_roots : [];
  const completeSnapshot = roots.some(root => root.status === "complete") && roots.every(root => ["complete", "skipped"].includes(root.status));
  const estimateDuration = Object.prototype.hasOwnProperty.call(s, "estimate_duration_sec")
    ? scanNumber(s.estimate_duration_sec)
    : completeSnapshot ? scanNumber(data.scan_duration_sec) : null;
  const elapsed = running && started !== null ? Math.max(0, now - started) : null;
  const heartbeatStale = running && s.error_code === "STALE_PROGRESS";
  const measurable = running && s.progress_available && !heartbeatStale;
  const predicted = elapsed !== null && estimateDuration > 0 ? started + estimateDuration : null;
  const eta = predicted > now && !heartbeatStale ? predicted : null;
  const longest = scanNumber(s.estimate_max_duration_sec);
  const slowEta = eta === null && !heartbeatStale && predicted !== null && longest > estimateDuration && started + longest > now ? started + longest : null;
  const labels = { running: "스캔 중", complete: "스캔 완료", partial: "일부 항목 확인 필요", failed: "스캔 실패", idle: "대기 중", unknown: "상태 확인 중", starting: "스캔 요청 중", requested: "시작 대기 중", refreshing: "결과 반영 중", reconnecting: "상태 재확인 중" };
  let note = "";
  if (disconnected) note = "상태를 다시 확인하고 있습니다. 마지막으로 받은 데이터가 표시됩니다.";
  else if (s.error_code === "SCAN_INTERRUPTED") note = "스캔이 완료되지 않은 채 중단되었습니다. 서버 상태를 확인하세요.";
  else if (heartbeatStale) note = "진행 정보 갱신이 지연되고 있습니다.";
  else if (running && !s.progress_available) note = "현재 서버에서 상세 진행 정보를 제공하지 않습니다.";
  else if (state === "failed") note = "실패 원인: " + (s.error_code || "확인되지 않음");
  else if (state === "partial") note = "읽지 못한 항목이 있습니다. 아래 경고를 확인하세요.";
  const next = scanNumber(s.next_run_unix);
  return {
    state, running, label: disconnected ? "상태 확인 지연" : s.error_code === "SCAN_INTERRUPTED" ? "스캔 중단" : (labels[state] || labels.unknown),
    lastFinished, duration, elapsed, eta, slowEta, snapshotFinished,
    snapshotPending: false,
    etaNote: heartbeatStale ? "진행 정보 확인 후 예상 시간 갱신" : predicted !== null && predicted <= now ? (slowEta !== null ? "평소보다 오래 걸리는 중" : "최근 최장 소요 시간 초과 · 계속 진행 중") : started === null ? "현재 스캔 시작 시각 확인 중" : "같은 범위의 완료 기록이 없어 예상 시간 확인 불가",
    nextRunLabel: s.schedule_mode === "due-check" ? "다음 확인" : "다음",
    nextRun: !disconnected && s.timer_state === "active" && next > now ? next : null,
    scheduleNote: s.timer_state === "inactive" || s.timer_state === "disabled" ? "자동 스캔 비활성" : "예약 정보 확인 불가",
    root: measurable && typeof s.current_root === "string" ? s.current_root : null,
    files: measurable ? scanNumber(s.scanned_files) : null,
    rootsDone: measurable ? scanNumber(s.roots_completed) : null,
    rootsTotal: measurable ? scanNumber(s.roots_total) : null,
    phase: ({ preparing: "준비 중", scanning: "파일 확인 중", publishing: "결과 저장 중" })[s.phase] || "파일 확인 중",
    note,
  };
}

function scanDuration(seconds) {
  if (seconds === null) return "—";
  const n = Math.max(0, Math.floor(seconds));
  if (n < 60) return n + "초";
  if (n < 3600) return Math.floor(n / 60) + "분 " + (n % 60) + "초";
  return Math.floor(n / 3600) + "시간 " + Math.floor((n % 3600) / 60) + "분";
}

function scanDate(seconds) {
  if (seconds === null) return "—";
  return new Date(seconds * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}

function scanCompactDate(seconds) {
  if (seconds === null) return "기록 없음";
  const date = new Date(seconds * 1000);
  return (date.getMonth() + 1) + "/" + date.getDate() + " " + String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
}

function renderScanStatus(status, snapshot, options = {}) {
  const doc = options.document || document;
  const panel = doc.getElementById("scanStatusPanel");
  if (!panel) return;
  const now = options.now ?? scanNumber(status?.host_now_unix) ?? clockSeconds();
  const model = scanStatusModel(status, snapshot, now);
  const set = (id, value) => { const el = doc.getElementById(id); if (el && el.textContent !== value) el.textContent = value; };
  const title = (id, value) => { const el = doc.getElementById(id); if (el) el.title = value; };
  panel.dataset.state = model.state;
  panel.dataset.sample = options.sample ? "true" : "false";
  set("scanStateLabel", options.sample ? "샘플" : model.running && status.phase === "publishing" ? model.phase : model.label);
  const stateLabel = doc.getElementById("scanStateLabel");
  if (stateLabel) stateLabel.hidden = !options.sample && ["idle", "complete"].includes(model.state);
  title("scanStateLabel", options.sample ? "샘플 데이터 · 실제 스캔은 실행되지 않습니다" : model.label);
  set("scanLastCompleted", model.snapshotFinished === null ? "표시할 스캔 기록 없음" : "화면 데이터 " + scanCompactDate(model.snapshotFinished));
  title("scanLastCompleted", "화면 데이터 기준: " + scanDate(model.snapshotFinished));
  set("scanLastDuration", model.lastFinished !== model.snapshotFinished && model.lastFinished !== null
    ? "최근 스캔 완료 " + scanCompactDate(model.lastFinished)
    : model.duration !== null ? "(" + scanDuration(model.duration) + ")" : "");
  const wait = model.nextRun === null ? null : Math.max(1, Math.ceil((model.nextRun - now) / 60)) * 60;
  set("scanNextRun", model.nextRun !== null ? model.nextRunLabel + " " + scanDuration(wait).replace(/ 0초$/, "") + " 후" : model.scheduleNote);
  title("scanNextRun", model.nextRun !== null ? (model.nextRunLabel === "다음 확인" ? "다음 자동 스캔 대상 확인: " : "다음 자동 스캔: ") + scanDate(model.nextRun) + " · 서버 예약 기준" : model.scheduleNote);
  const nextRun = doc.getElementById("scanNextRun");
  if (nextRun) nextRun.hidden = !!options.sample;
  const progress = doc.getElementById("scanProgressDetails");
  if (progress) progress.hidden = !model.running;
  set("scanRoot", model.root || "대상 확인 중");
  title("scanRoot", model.phase + (model.root ? " · " + model.root : ""));
  set("scanFileCount", model.files !== null ? model.files.toLocaleString("ko-KR") + "개 파일" : "처리량 확인 중");
  set("scanElapsed", scanDuration(model.elapsed) + " 경과");
  set("scanEta", model.eta !== null ? "예상 " + scanDuration(model.eta - now) + " 남음"
    : model.slowEta ? "평소보다 오래 걸리는 중 · 최대 " + scanDuration(model.slowEta - now) + " 남음" : model.etaNote);
  title("scanEta", model.eta !== null ? "예상 완료: " + scanDate(model.eta) + " · 최근 성공 스캔의 보통 소요 시간 기준"
    : model.slowEta ? "최대 예상 완료: " + scanDate(model.slowEta) + " · 최근 성공 스캔 중 가장 오래 걸린 시간 기준" : model.etaNote);
  const meter = doc.getElementById("scanRootProgress");
  const hasRoots = model.rootsTotal > 0 && model.rootsDone !== null;
  if (meter) { meter.hidden = !hasRoots; meter.max = model.rootsTotal || 1; meter.value = Math.min(model.rootsDone || 0, meter.max); }
  set("scanRootCount", hasRoots ? model.rootsDone + "/" + model.rootsTotal + " 완료" : "");
  set("scanStatusNote", options.note || model.note);
  const note = doc.getElementById("scanStatusNote");
  if (note) note.hidden = !(options.note || model.note);
  return model;
}

export {isScanActive, scanStatusModel, renderScanStatus};
