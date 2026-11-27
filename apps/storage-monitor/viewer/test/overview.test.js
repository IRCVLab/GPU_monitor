import test from 'node:test';
import assert from 'node:assert/strict';
import {buildOverviewRows, buildFleetUserUsage} from '../views/overview.js';

const summary = {server_id:'alpha', display_name:'Alpha', freshness:'fresh', users_top:[{uid:1001,name:'kim',bytes:250}],
  volumes:[{volume_id:'vol', mountpoint:'/data', media:'ssd', severity:'critical', capacity:{total:100,used:25,avail:75,used_pct:25},
    roots:[{scan_root:'/data/a',bytes:27},{scan_root:'/data/b',bytes:0}]}]};

test('V2 overview presents API severity, one capacity per volume and both scan roots', () => {
  const [row] = buildOverviewRows([summary]);
  assert.equal(row.mountCount,1);
  assert.equal(row.aggregate.availableBytes,75);
  assert.equal(row.mounts[0].pressure,'critical');
  assert.equal(row.mounts[0].usedPctText,'25%');
  assert.deepEqual(row.mounts[0].scanRoots,[{path:'/data/a',scannedBytes:27},{path:'/data/b',scannedBytes:0}]);
});

test('fleet usage merges named accounts across host summaries', () => {
  const usage = buildFleetUserUsage([summary,{...summary,server_id:'beta'}]);
  assert.equal(usage.rows[0].bytes,500);
  assert.equal(usage.rows[0].servers.size,2);
});

test('a single-owner subtree is coloured by top-level folder, a shared one by owner', async () => {
  const {folderColors} = await import('../views/treemap.js');
  const nobody = 65534;
  const nexus = {name:'datasets', uid:nobody, bytes:30, children:[
    {name:'NCLT', uid:nobody, bytes:10, children:[{name:'a', uid:nobody, bytes:10}]},
    {name:'Oxford', uid:nobody, bytes:20}]};
  const colours = folderColors(nexus);
  assert.deepEqual([...colours.keys()], ['Oxford', 'NCLT']);
  assert.notEqual(colours.get('Oxford'), colours.get('NCLT'));
  const shared = {name:'home', uid:0, bytes:30, children:[{name:'kim', uid:1001, bytes:10}, {name:'lee', uid:1002, bytes:20}]};
  assert.equal(folderColors(shared), null);
});
