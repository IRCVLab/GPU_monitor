const assert = require('node:assert/strict');
const fs = require('node:fs');
assert.ok(fs.existsSync(__dirname + '/scan-status.js'), 'scan status presentation module must exist');
const { pendingScanGeneration, scanStatusModel, createScanMonitor } = require('./scan-status.js');

const now = 2000;
const snapshot = { scan_started_unix: 1000, scan_finished_unix: 1100, scan_duration_sec: 100, scan_generation: 'old', selected_roots: [{status:'complete'}] };
let model = scanStatusModel({}, snapshot, now);
assert.equal(model.lastFinished, 1100, 'last updated must be completion, not start');
assert.equal(model.nextRun, null, 'never invent next run by adding six hours');
assert.equal(scanStatusModel({}, { scan_started_unix: 1000 }, now).lastFinished, null);
model = scanStatusModel({ state: 'running', started_unix: 1950, updated_unix: 1998, observed_unix: now, progress_available: true, scanned_files: 12500, current_root: '/data', roots_completed: 1, roots_total: 3 }, snapshot, now);
assert.equal(model.eta, 2050);
assert.equal(model.elapsed, 50);
assert.equal(model.files, 12500);
assert.equal(model.root, '/data');
assert.equal(model.label, '스캔 중');
assert.equal(scanStatusModel({ state: 'running', started_unix: 1800, observed_unix: now }, snapshot, now).eta, null, 'overrun cannot look almost complete');
assert.match(scanStatusModel({ state: 'running', started_unix: 1800, observed_unix: now }, snapshot, now).etaNote, /^최근 최장 소요 시간 초과/);
assert.equal(scanStatusModel({ state: 'running', started_unix: 1950, observed_unix: now }, null, now).eta, null);
model = scanStatusModel({ state: 'running', started_unix: 1800, updated_unix: 1801, error_code: "STALE_PROGRESS", observed_unix: now, progress_available: true, scanned_files: 900 }, snapshot, now);
assert.equal(model.files, null, 'stale heartbeat must not look live');
assert.match(model.note, /갱신/);
assert.equal(scanStatusModel({ state: 'complete', disconnected: true, observed_unix: 1800 }, snapshot, now).label, '상태 확인 지연');
assert.equal(scanStatusModel({ next_run_unix: 2100, timer_state: 'inactive', observed_unix: now }, snapshot, now).nextRun, null);
assert.equal(scanStatusModel({ next_run_unix: 2100, timer_state: 'active', observed_unix: now }, snapshot, now).nextRun, 2100);
assert.equal(scanStatusModel({ state: 'failed', error_code: 'SCANNER_FAILED', observed_unix: now }, snapshot, now).label, '스캔 실패');

model = scanStatusModel({state: 'running', started_unix: 1950, updated_unix: 1960, observed_unix: now, error_code: 'STALE_PROGRESS', progress_available: false}, snapshot, now);
assert.equal(model.eta, null, 'explicit backend stale observation suppresses ETA');
assert.match(model.note, /갱신/);

assert.equal(scanStatusModel({state:'running',started_unix:1950,observed_unix:now,estimate_duration_sec:null}, snapshot, now).eta, null, 'explicit absent successful baseline is not replaced');
assert.equal(scanStatusModel({state:'running',started_unix:1950,observed_unix:now}, {...snapshot, selected_roots:[{status:'partial'}]}, now).eta, null, 'partial snapshot cannot estimate full scan');
model = scanStatusModel({state:'running',started_unix:1950,observed_unix:now,last_duration_sec:20,estimate_duration_sec:100}, snapshot, now);
assert.equal(model.duration, 20, 'display latest actual duration');
assert.equal(model.eta, 2050, 'estimate uses last complete scan duration');

model = scanStatusModel({ state: "running", estimate_duration_sec: 91, observed_unix: now }, snapshot, now);
assert.match(model.etaNote, /시작 시각/, "start timestamp and history are different evidence");
assert.equal(pendingScanGeneration({ state: "idle", published_generation: "new", published_finished_unix: 1200, snapshot_generation: "old" }, snapshot), "new");
assert.equal(pendingScanGeneration({ state: "running", snapshot_generation: "new", snapshot_finished_unix: 1200 }, snapshot), "new");
assert.equal(pendingScanGeneration({ state: "idle", published_generation: "older", published_finished_unix: 1000 }, snapshot), null);
model = scanStatusModel({ state: "idle", published_generation: "new", published_finished_unix: 1200 }, snapshot, now);
assert.equal(model.snapshotFinished, 1100);
assert.equal(model.lastFinished, 1200);
assert.equal(model.snapshotPending, true);
assert.equal(scanStatusModel({ snapshot_finished_unix: 1200 }, null, now).snapshotFinished, null);
assert.equal(scanStatusModel({ schedule_mode: "due-check" }, snapshot, now).nextRunLabel, "다음 확인");

async function testMonitor() {
  let resolveA;
  const events = [], scheduled = [];
  const monitor = createScanMonitor({
    fetchStatus: id => id === 'a' ? new Promise(resolve => { resolveA = resolve; }) : Promise.resolve({ state: 'running' }),
    onStatus: (id, value) => events.push([id, value.state]),
    schedule: (fn, ms) => { const timer = { fn, ms }; scheduled.push(timer); return timer; },
    cancel: timer => { timer.cancelled = true; },
  });
  monitor.start('a');
  monitor.start('b');
  await new Promise(resolve => setImmediate(resolve));
  resolveA({ state: 'complete' });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(events, [['b', 'running']], 'navigation must discard old responses');
  assert.equal(scheduled.length, 1);
  assert.equal(scheduled[0].ms, 5000);
  monitor.stop();
  assert.equal(scheduled[0].cancelled, true);
}
{
  const slow = scanStatusModel({ state: 'running', started_unix: 1000, observed_unix: 1100, updated_unix: 1100, estimate_duration_sec: 19, estimate_max_duration_sec: 1396 }, null, 1100);
  assert.equal(slow.eta, null, 'past the typical duration there is no near ETA');
  assert.equal(slow.slowEta, 2396, 'past the typical duration the longest recent run bounds the ETA');
  assert.match(slow.etaNote, /^평소보다 오래 걸리는 중/);
  const fast = scanStatusModel({ state: 'running', started_unix: 1000, observed_unix: 1010, updated_unix: 1010, estimate_duration_sec: 19, estimate_max_duration_sec: 1396 }, null, 1010);
  assert.equal(fast.eta, 1019, 'within the typical duration the ETA follows the typical run');
  const over = scanStatusModel({ state: 'running', started_unix: 1000, observed_unix: 2500, updated_unix: 2500, estimate_duration_sec: 19, estimate_max_duration_sec: 1396 }, null, 2500);
  assert.equal(over.slowEta, null);
  assert.match(over.etaNote, /^최근 최장 소요 시간 초과/, 'only past the longest recent run is the scan overdue');
}
testMonitor().then(() => console.log('scan status tests passed')).catch(error => { console.error(error); process.exitCode = 1; });
