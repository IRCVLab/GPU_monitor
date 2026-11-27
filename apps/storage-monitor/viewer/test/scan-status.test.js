import test from 'node:test';
import assert from 'node:assert/strict';
import {scanStatusModel} from '../views/scan-status.js';

test('scan labels use each accepted finish and switch from typical to longest ETA', () => {
  const detail = {volumes:[{capture:{scan_finished_unix:1100}},{capture:null},{capture:{scan_finished_unix:1200}}]};
  const facts = {activity:{state:'running',started_unix:1950,progress:{files:100,roots_completed:1,roots_total:3}},
    eta:{typical_sec:100,longest_sec:150}};
  const current = scanStatusModel(facts,detail,2000);
  assert.equal(current.files,100);
  assert.equal(current.snapshotFinished,1200);
  assert.equal(current.elapsed,50);
  assert.equal(current.eta,2050);
  assert.equal(current.label,'스캔 중');
  assert.equal(scanStatusModel(facts,detail,2050).slowEta,2100);
  assert.equal(scanStatusModel(facts,detail,2101).slowEta,null);
  assert.equal(scanStatusModel({...facts,eta:{typical_sec:null,longest_sec:null}},detail,2000).eta,null);
});
