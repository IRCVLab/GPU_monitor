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

test('V9 hidden file counts retain descending scoped usage under the storage filter', () => {
  const users = [
    {uid:1,name:'many-files',bytes:100,files:1000,by_mount:{'/data':10}},
    {uid:2,name:'big-on-data',bytes:50,files:1,by_mount:{'/data':40}}
  ];
  assert.deepEqual(sortRows('users',users,{key:'files',dir:-1},{mount:'/data'}).map(u=>u.name),['big-on-data','many-files']);
});
