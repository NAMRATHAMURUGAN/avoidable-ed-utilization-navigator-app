import type { TriageRequest } from '../types';
import {
  EMERGENCY_WARNING_SIGN_RULES,
  SAFETY_RULE_SET_VERSION,
  UI_SELECTED_RED_FLAG_RULE_IDS,
  type SafetyRule,
} from './safetyRules.ts';

export interface TriggeredSafetyRule {
  id: string;
  category: string;
  description: string;
}

export interface SafetyDecision {
  isEmergency: boolean;
  ruleSetVersion: string;
  triggeredRules: TriggeredSafetyRule[];
}

type SafetyInput = Partial<TriageRequest> | Record<string, unknown> | null | undefined;

/** Normalize untrusted symptom text without changing its word boundaries. */
export function normalizeSafetyText(value: unknown): string {
  if (typeof value !== 'string') {
    return '';
  }

  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function phraseMatches(text: string, phrase: string): boolean {
  const normalizedPhrase = normalizeSafetyText(phrase);
  if (!text || !normalizedPhrase) {
    return false;
  }

  // Delimiter boundaries prevent a phrase such as "chest pain" from matching
  // inside an unrelated joined word.
  return ` ${text} `.includes(` ${normalizedPhrase} `);
}

function stringValues(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

const normalizedSelectedRedFlagRuleIds = new Map(
  Object.entries(UI_SELECTED_RED_FLAG_RULE_IDS).map(([label, ruleId]) => [normalizeSafetyText(label), ruleId]),
);

function toTriggeredRule(rule: SafetyRule): TriggeredSafetyRule {
  return { id: rule.id, category: rule.category, description: rule.description };
}

/**
 * Deterministic warning-sign screening. This engine is intentionally pure:
 * no LLM, ML, database, network, time, or external state can override it.
 */
export function evaluateSafety(input: SafetyInput): SafetyDecision {
  const request = input && typeof input === 'object' ? input : {};
  const record = request as Record<string, unknown>;
  const textToScreen = [record.chiefComplaint, ...stringValues(record.associatedSymptoms)]
    .map(normalizeSafetyText)
    .filter(Boolean);
  const selectedRuleIds = new Set(
    stringValues(record.selectedRedFlags)
      .map(normalizeSafetyText)
      .map((label) => normalizedSelectedRedFlagRuleIds.get(label))
      .filter((ruleId): ruleId is string => Boolean(ruleId)),
  );

  const triggeredRules = EMERGENCY_WARNING_SIGN_RULES
    .filter((rule) => rule.enabled && (
      selectedRuleIds.has(rule.id)
      || rule.aliases.some((alias) => textToScreen.some((text) => phraseMatches(text, alias)))
    ))
    .map(toTriggeredRule);

  // This is an explicit user-reported safety flag. It intentionally escalates
  // instead of clinically interpreting the flag, and is not a diagnosis.
  if (record.hasRedFlags === true && !triggeredRules.some((rule) => rule.id === 'RF-USER-REPORTED-001')) {
    const userReportedRule = EMERGENCY_WARNING_SIGN_RULES.find((rule) => rule.id === 'RF-USER-REPORTED-001');
    if (userReportedRule?.enabled) {
      triggeredRules.push(toTriggeredRule(userReportedRule));
    }
  }

  return {
    isEmergency: triggeredRules.length > 0,
    ruleSetVersion: SAFETY_RULE_SET_VERSION,
    triggeredRules,
  };
}
