import assert from 'node:assert/strict';
import test from 'node:test';
import { experimentEtaLabel, parseExperimentNotes } from './experimentNotes.ts';

const observedAt = '2026-11-24T12:00:00Z';
const observedMs = Date.parse(observedAt);
const experiment = {
	id: 'run-1',
	summary: 'TransFuser training',
	gpu_indices: [0, 1],
	eta_seconds: 3600,
	observed_at: observedAt
};

test('disabled automatic notes discard stale results from the API response', () => {
	assert.deepEqual(
		parseExperimentNotes({ enabled: false, configured: true, by_server: { 2: [experiment] } }),
		{ enabled: false, configured: true, by_server: {} }
	);
});

test('a malformed automatic note cannot be rendered', () => {
	assert.throws(() =>
		parseExperimentNotes({
			enabled: true,
			configured: true,
			by_server: { 2: [{ ...experiment, gpu_indices: ['GPU 0'] }] }
		})
	);
});

test('ETA is shown while its estimate is in the future', () => {
	assert.match(experimentEtaLabel(experiment, observedMs), /^종료 예상 .+\d{2}:\d{2}$/);
	assert.equal(experimentEtaLabel(experiment, observedMs + 3600_000), '종료 예상 미확인');
	assert.equal(experimentEtaLabel({ ...experiment, eta_seconds: null }, observedMs), '종료 예상 미확인');
});
