import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {buildCleanupCommandPlan, cleanupRemovalScript, removal} from '../removal.js';
import fixtures from './removal-golden.json' with {type:'json'};

const detail = {hostname:'hinton.lab', selected_roots:[{mount_id:'data', scan_root:'/data', status:'complete'}]};
test('classic removal output is byte-identical and nested picks count once', () => {
  for (const {items, command} of fixtures) {
    const result = removal(detail, items);
    assert.equal(result.command, command);
    if (items.length === 3) assert.match(result.summary, /1 inside a selected folder/);
  }
});
function testRemovalScriptRemovesOnlyOnItsOwnServerWithoutPrompts() {
  
  
  
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rm-script-'));
  const bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, 'sudo'), '#!/bin/sh\nexec "$@"\n', { mode: 0o755 });
  const data = path.join(root, 'data');
  const detail = { hostname: 'hinton.lab', server_id: 'hinton', selected_roots: [{ mount_id: 'data', mount_root: data, mountpoint: data, scan_root: data, status: 'complete' }], mounts: [{ mount_id: 'data', path: data, scan_root: data }] };
  const items = [{ path: data + "/it's dir", kind: 'directory', bytes: 1 }, { path: data + '/-model.bin', kind: 'file', bytes: 1 }];
  const plans = items.map(item => ({ item, plan: buildCleanupCommandPlan(detail, item, {}) }));
  const script = cleanupRemovalScript(detail, plans);
  assert.ok(!script.includes('\n'), 'the removal script must be a single line');
  const run = host => {
    fs.mkdirSync(path.join(data, "it's dir", 'sub'), { recursive: true });
    const readOnly = path.join(data, "it's dir", 'sub', 'ro.txt');
    if (!fs.existsSync(readOnly)) fs.writeFileSync(readOnly, 'x', { mode: 0o444 });
    fs.writeFileSync(path.join(data, '-model.bin'), 'x');
    fs.writeFileSync(path.join(bin, 'hostname'), '#!/bin/sh\necho ' + host + '\n', { mode: 0o755 });
    execFileSync('bash', ['-c', script], { env: { PATH: bin + ':' + process.env.PATH }, stdio: ['ignore', 'ignore', 'ignore'] });
    return fs.readdirSync(data).sort();
  };
  assert.deepStrictEqual(run('neo'), ['-model.bin', "it's dir"], 'pasted on another server, the script must remove nothing');
  detail.hostname = 'gpu_01';
  assert.match(cleanupRemovalScript(detail, plans), /^if \[ "\$\(hostname -s\)" = 'gpu_01' \]; then /, 'an unusual but valid hostname still gets the host check');
  detail.hostname = 'hinton.lab';
  assert.deepStrictEqual(run('hinton'), [], 'pasted on its own server, the script must remove every selected path without asking');
  for (const bad of ['/data/a\u202Eb', '/data/a\ud800b', '/data/a\u0085b']) {
    const plan = buildCleanupCommandPlan(detail, { path: data + bad.slice(5), kind: 'directory', bytes: 1 }, {});
    assert.strictEqual(plan.destructiveCommand, null, 'paths with bidi, lone surrogate or C1 characters must not get a removal command');
  }
  fs.rmSync(root, { recursive: true, force: true });
}

function testRemovalScriptRefusesDirectoriesThatContainMounts() {
  
  
  try { execFileSync('unshare', ['-rm', 'true'], { stdio: 'ignore' }); } catch (_) { return; }
  
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rm-mount-'));
  const bin = path.join(root, 'bin');
  fs.mkdirSync(bin);
  fs.writeFileSync(path.join(bin, 'sudo'), '#!/bin/sh\nexec "$@"\n', { mode: 0o755 });
  fs.writeFileSync(path.join(bin, 'hostname'), '#!/bin/sh\necho hinton\n', { mode: 0o755 });
  const data = path.join(root, 'data');
  const source = path.join(root, 'elsewhere');
  fs.mkdirSync(path.join(data, 'proj', 'bound'), { recursive: true });
  fs.mkdirSync(path.join(data, 'plain'), { recursive: true });
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, 'keep.bin'), 'x');
  const detail = { hostname: 'hinton', server_id: 'hinton', selected_roots: [{ mount_id: 'data', mount_root: data, mountpoint: data, scan_root: data, status: 'complete' }], mounts: [{ mount_id: 'data', path: data, scan_root: data }] };
  fs.renameSync(path.join(data, 'proj'), path.join(data, 'my proj\\x'));
  const items = [{ path: data + '/my proj\\x', kind: 'directory', bytes: 1 }, { path: data + '/plain', kind: 'directory', bytes: 1 }];
  const script = cleanupRemovalScript(detail, items.map(item => ({ item, plan: buildCleanupCommandPlan(detail, item, {}) })));
  const inNamespace = 'mount --bind "$1" "$2" && ' + script;
  let stderr = '';
  try {
    execFileSync('unshare', ['-rm', 'bash', '-c', inNamespace, 'bash', source, path.join(data, 'my proj\\x', 'bound')], { env: { PATH: bin + ':' + process.env.PATH }, stdio: ['ignore', 'ignore', 'pipe'] });
  } catch (error) { stderr = String(error.stderr || ''); }
  assert.ok(fs.existsSync(path.join(source, 'keep.bin')), 'a bind mount inside a selected folder must keep its source data');
  assert.ok(fs.existsSync(path.join(data, 'plain')), 'when any selected folder holds a mount, nothing is removed');
  fs.rmSync(root, { recursive: true, force: true });
}


test('own host and quoting', testRemovalScriptRemovesOnlyOnItsOwnServerWithoutPrompts);
test('mount guard', testRemovalScriptRefusesDirectoriesThatContainMounts);

test('unreadable tables, same-device bind mounts and mismatched raw bytes refuse copy', () => {
  const dir = {kind:'directory',path:'/data/folder'};
  for (const table of [{status:'unreadable'}, {status:'ok',within_roots:[{path:'/data/folder/bind',same_device:true}]}]) {
    assert.equal(removal({...detail,mount_table:table},[dir]).command,'');
  }
  assert.equal(buildCleanupCommandPlan(detail,{...dir,path_b64:btoa('/data/different')}).accepted,false);
});
