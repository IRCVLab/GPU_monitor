"use strict";

function scanStatusModel(facts, detail, now) {
  const {activity,eta} = facts;
  const {state,progress,started_unix:start} = activity;
  const running = state === 'running';
  const elapsed = running && start != null && now != null ? Math.max(0,now-start) : null;
  const typical = eta.typical_sec;
  const longest = eta.longest_sec;
  const hasEstimate = elapsed != null && typical > 0;
  const typicalEnd = hasEstimate && elapsed < typical ? start+typical : null;
  const longestEnd = hasEstimate && elapsed >= typical && elapsed < longest ? start+longest : null;
  const finishes = detail.volumes.flatMap(v => v.capture ? [v.capture.scan_finished_unix] : []);
  return {
    state, running, label:({running:'스캔 중',idle:'대기 중',interrupted:'스캔 중단'})[state] ?? '상태 확인 중',
    snapshotFinished:finishes.length ? Math.max(...finishes) : null, elapsed, eta:typicalEnd, slowEta:longestEnd,
    etaNote:elapsed == null ? '현재 스캔 시작 시각 확인 중' : hasEstimate ? '최근 최장 소요 시간 초과 · 계속 진행 중' :
      '같은 범위의 완료 기록이 없어 예상 시간 확인 불가',
    root:progress?.group, files:progress?.files, rootsDone:progress?.roots_completed, rootsTotal:progress?.roots_total,
    note:state === 'interrupted' ? '스캔이 완료되지 않은 채 중단되었습니다. 서버 상태를 확인하세요.' : '',
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
  const now = options.now ?? status.activity.host_now_unix;
  const model = scanStatusModel(status, snapshot, now);
  const set = (id, value) => { const el = doc.getElementById(id); if (el && el.textContent !== value) el.textContent = value; };
  const title = (id, value) => { const el = doc.getElementById(id); if (el) el.title = value; };
  panel.dataset.state = model.state;
  set("scanStateLabel",model.label);
  const stateLabel = doc.getElementById("scanStateLabel");
  if (stateLabel) stateLabel.hidden = model.state === "idle";
  title("scanStateLabel",model.label);
  set("scanLastCompleted", model.snapshotFinished === null ? "표시할 스캔 기록 없음" : "화면 데이터 " + scanCompactDate(model.snapshotFinished));
  title("scanLastCompleted", "화면 데이터 기준: " + scanDate(model.snapshotFinished));
  set("scanLastDuration","");
  const nextRun = doc.getElementById("scanNextRun");
  if (nextRun) nextRun.hidden = true;
  const progress = doc.getElementById("scanProgressDetails");
  if (progress) progress.hidden = !model.running;
  set("scanRoot", model.root || "대상 확인 중");
  title("scanRoot", model.root ?? "");
  set("scanFileCount", model.files != null ? model.files.toLocaleString("ko-KR") + "개 파일" : "처리량 확인 중");
  set("scanElapsed", scanDuration(model.elapsed) + " 경과");
  set("scanEta", model.eta !== null ? "예상 " + scanDuration(model.eta - now) + " 남음"
    : model.slowEta ? "평소보다 오래 걸리는 중 · 최대 " + scanDuration(model.slowEta - now) + " 남음" : model.etaNote);
  title("scanEta", model.eta !== null ? "예상 완료: " + scanDate(model.eta) + " · 최근 성공 스캔의 보통 소요 시간 기준"
    : model.slowEta ? "최대 예상 완료: " + scanDate(model.slowEta) + " · 최근 성공 스캔 중 가장 오래 걸린 시간 기준" : model.etaNote);
  const meter = doc.getElementById("scanRootProgress");
  const hasRoots = model.rootsTotal > 0 && model.rootsDone != null;
  if (meter) { meter.hidden = !hasRoots; meter.max = model.rootsTotal || 1; meter.value = Math.min(model.rootsDone || 0, meter.max); }
  set("scanRootCount", hasRoots ? model.rootsDone + "/" + model.rootsTotal + " 완료" : "");
  set("scanStatusNote", options.note || model.note);
  const note = doc.getElementById("scanStatusNote");
  if (note) note.hidden = !(options.note || model.note);
  return model;
}

export { scanStatusModel, renderScanStatus};
