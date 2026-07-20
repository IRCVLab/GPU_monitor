# Compact overview polish report

## Scope
- Tightened only the compact overview surface.
- Detail rail behavior and markup were not changed.

## TDD evidence
- RED: `node viewer/viewer_regression_test.js` failed before implementation on the new assertion `compact overview lead must be absent from markup`.
- GREEN: after minimal implementation, `node viewer/viewer_regression_test.js` passed.

## Changes
- `viewer/index.html`: removed the explanatory compact overview lead paragraph from markup.
- `viewer/overview.js`: changed server header metadata to render only actionable mount count (`N개 마운트`).
- `viewer/overview.js`: removed redundant `정상` from healthy mount free text while retaining Korean warning/critical status text.
- `viewer/viewer_regression_test.js`: added/updated regression assertions for the approved compact spec.

## Verification
Final verification was run after this report was written; see final assistant response for exact command outputs.

## Concerns
- Existing untracked `.playwright-cli/` and `output/` artifacts were left untouched.
