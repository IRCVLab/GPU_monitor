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

test('when one owner holds the bytes, tiles are coloured by first-level folder under the scan root', async () => {
  const {folderColoring} = await import('../views/treemap.js');
  // Live Nexus: NFS writes as nobody, while the dataset directories themselves belong to root.
  const users = [{uid:65534, name:'nobody', bytes:4.71e12}, {uid:0, name:'root', bytes:1e6}];
  const roots = [
    {path:'/nexus/datasets', tree:{name:'datasets', children:[{name:'NCLT', bytes:419e9}, {name:'Oxford', bytes:1.55e12}]}},
    {path:'/nexus/workspace', tree:{name:'workspace', children:[]}}];
  const coloring = folderColoring(users, roots);
  assert.equal(coloring.keyOf('/nexus/datasets/Oxford/2019-01-18/radar'), '/nexus/datasets/Oxford');
  assert.equal(coloring.keyOf('/nexus/datasets'), null, 'a scan root itself has no folder colour');
  assert.deepEqual([...coloring.colors.values()].map(c => c.name), ['Oxford', 'NCLT']);
  assert.notEqual(coloring.colors.get('/nexus/datasets/Oxford').color, coloring.colors.get('/nexus/datasets/NCLT').color);
  const shared = [{uid:1001, name:'kim', bytes:60}, {uid:1002, name:'lee', bytes:40}];
  assert.equal(folderColoring(shared, roots), null, 'shared storage keeps owner colours');
});
