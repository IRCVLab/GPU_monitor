// @ts-ignore Node types are provided by the test runner.
import test from 'node:test';
// @ts-ignore Node types are provided by the test runner.
import assert from 'node:assert/strict';
// @ts-expect-error TS5097: native TypeScript tests resolve explicit suffixes.
import {kilowatts,kilowattsLabel,wattsFromKilowatts} from './powerUnits.ts';

test('power labels stay in kW for small readings, zero, overload headroom and unknown values',()=>{
 for(const [watts,label] of [[16,'0.016 kW'],[1,'0.001 kW'],[0,'0 kW'],[5312,'5.312 kW'],[-100,'-0.1 kW'],[null,'— kW'],[undefined,'— kW'],[NaN,'— kW']] as const) assert.equal(kilowattsLabel(watts),label);
});
test('kW editing round trips to stored watts, preserving zero and empty input',()=>{
 for(const watts of [0,16,17.5,5312]) assert.equal(wattsFromKilowatts(kilowatts(watts)),watts);
 assert.equal(wattsFromKilowatts(0.016),16);
 assert.equal(wattsFromKilowatts(null),null);
 assert.equal(kilowatts(null),null);
});
