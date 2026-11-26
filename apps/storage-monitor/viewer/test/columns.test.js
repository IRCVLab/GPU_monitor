import assert from 'node:assert/strict';
import test from 'node:test';
import {columnValue,sortRows} from '../columns.js';
test('V5 both file tables read scan-relative age_days', () => {
  for (const table of ['top','stale']) assert.equal(columnValue(table,'age',{mtime:0,age_days:400},{}),400);
});
test('V9 filtered users sort by the displayed file count', () => {
  const users = [{name:'a',files:4},{name:'b',files:12}];
  assert.equal(columnValue('users','files',users[1],{mount:'/data'}),null);
  assert.deepEqual(sortRows('users',users,{key:'files',dir:-1},{mount:'/data'}),users);
  assert.deepEqual(sortRows('users',users,{key:'files',dir:-1},{}),[users[1],users[0]]);
});
