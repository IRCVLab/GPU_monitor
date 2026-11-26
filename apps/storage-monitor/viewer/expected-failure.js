'use strict';

const assert = require('assert');

// Runs a bug repro that must still fail, the node twin of pytest's xfail(strict=True).
// It passes only when the repro throws an AssertionError whose first message line
// equals `expectedMessage` (newer Node appends a diff after it). A pass, or any
// other error, fails the run, so the unit that fixes the bug has to delete the
// wrapper and keep the test as a regression test.
async function expectedFailure(bugIds, expectedMessage, test) {
  const label = `${bugIds.join('/')} ${test.name}`;
  try {
    await test();
  } catch (err) {
    if (err instanceof assert.AssertionError && err.message.split('\n')[0] === expectedMessage) {
      console.log(`expected failure ${label}: ${expectedMessage}`);
      return;
    }
    throw new Error(`${label} failed for the wrong reason: ${err && err.message}`, { cause: err });
  }
  throw new Error(`${label} passed: the bug is fixed, so remove its expectedFailure wrapper`);
}

module.exports = { expectedFailure };
