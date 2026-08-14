import assert from 'node:assert/strict';
import test from 'node:test';

import { evaluateSafety, normalizeSafetyText } from './safetyEngine.ts';

const baseRequest = (overrides: Record<string, unknown> = {}) => ({
  chiefComplaint: '',
  symptomsDuration: 'Today',
  associatedSymptoms: [],
  hasRedFlags: false,
  selectedRedFlags: [],
  ...overrides,
});

test('detects configured emergency warning signs from symptom text', () => {
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'chest pain and pressure' })).isEmergency, true);
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'severe trouble breathing' })).isEmergency, true);
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'sudden weakness on one side and difficulty speaking' })).isEmergency, true);
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'I passed out and lost consciousness' })).isEmergency, true);
});

test('normalizes case, punctuation, and repeated whitespace', () => {
  assert.equal(normalizeSafetyText('  CHEST-pain!  '), 'chest pain');
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'CHEST PAIN' })).isEmergency, true);
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'chest-pain!' })).isEmergency, true);
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: ' chest    pain ' })).isEmergency, true);
});

test('uses selected flags and the explicit hasRedFlags signal', () => {
  const expectedRuleIds = new Map([
    ['Chest pain/pressure', 'RF-CARDIO-001'],
    ['Severe trouble breathing', 'RF-RESP-001'],
    ['Stroke symptoms', 'RF-NEURO-001'],
    ['Loss of consciousness', 'RF-CONSCIOUSNESS-001'],
  ]);
  for (const [label, ruleId] of expectedRuleIds) {
    const result = evaluateSafety(baseRequest({ selectedRedFlags: [label] }));
    assert.deepEqual(result.triggeredRules.map((rule) => rule.id), [ruleId]);
  }
  assert.deepEqual(
    evaluateSafety(baseRequest({ selectedRedFlags: ['Chest pain or pressure'] })).triggeredRules.map((rule) => rule.id),
    ['RF-CARDIO-001'],
  );
  assert.equal(evaluateSafety(baseRequest({ selectedRedFlags: ['Unrecognized warning'] })).isEmergency, false);
  const result = evaluateSafety(baseRequest({ hasRedFlags: true }));
  assert.equal(result.isEmergency, true);
  assert.ok(result.triggeredRules.some((rule) => rule.id === 'RF-USER-REPORTED-001'));
});

test('does not flag non-emergency, substring-only, blank, or malformed input', () => {
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'mild sore throat for two days' })).isEmergency, false);
  assert.equal(evaluateSafety(baseRequest({ chiefComplaint: 'mychestpaintracker is broken' })).isEmergency, false);
  assert.equal(evaluateSafety(baseRequest()).isEmergency, false);
  assert.equal(evaluateSafety({ chiefComplaint: 42, associatedSymptoms: [null, 7], selectedRedFlags: 'chest pain', hasRedFlags: 'true' }).isEmergency, false);
});

test('returns each matched rule once and ignores unrelated ML context', () => {
  const result = evaluateSafety(baseRequest({
    chiefComplaint: 'chest pain and severe trouble breathing',
    selectedRedFlags: ['Chest pain or pressure'],
    riskScore: 0,
    anomalyScore: 1,
  }));
  assert.deepEqual(result.triggeredRules.map((rule) => rule.id), ['RF-CARDIO-001', 'RF-RESP-001']);
  assert.equal(evaluateSafety(baseRequest({ riskScore: 999, anomalyScore: 999 })).isEmergency, false);
});
