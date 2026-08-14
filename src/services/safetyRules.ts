 /**
 * Versioned, auditable emergency warning-sign configuration.
 *
 * These rules support deterministic safety screening only. They do not
 * diagnose conditions or determine emergency-department avoidability or
 * medical necessity.
 */

export const SAFETY_RULE_SET_VERSION = '1.0.0';

export interface SafetyRule {
  id: string;
  category: string;
  description: string;
  aliases: readonly string[];
  enabled: boolean;
}

export const EMERGENCY_WARNING_SIGN_RULES: readonly SafetyRule[] = [
  {
    id: 'RF-CARDIO-001',
    category: 'cardiac_warning_sign',
    description: 'Chest pain or chest pressure',
    aliases: ['chest pain', 'chest pressure', 'pain in my chest', 'pressure in my chest'],
    enabled: true,
  },
  {
    id: 'RF-RESP-001',
    category: 'breathing_warning_sign',
    description: 'Severe trouble breathing',
    aliases: [
      'severe trouble breathing',
      'severe difficulty breathing',
      'severe shortness of breath',
      'cannot breathe',
      'cant breathe',
    ],
    enabled: true,
  },
  {
    id: 'RF-NEURO-001',
    category: 'stroke_like_warning_sign',
    description: 'Stroke-like symptoms',
    aliases: [
      'stroke symptoms',
      'stroke signs',
      'sudden weakness on one side',
      'difficulty speaking',
      'trouble speaking',
    ],
    enabled: true,
  },
  {
    id: 'RF-CONSCIOUSNESS-001',
    category: 'consciousness_warning_sign',
    description: 'Loss of consciousness',
    aliases: ['loss of consciousness', 'lost consciousness', 'passed out', 'pass out'],
    enabled: true,
  },
  {
    id: 'RF-USER-REPORTED-001',
    category: 'user_reported_warning_sign',
    description: 'User reported an emergency warning sign',
    aliases: [],
    enabled: true,
  },
];

/**
 * Explicit UI warning-sign label to rule-ID mapping. The current markup uses
 * "Chest pain or pressure"; the slash variant is retained as an equivalent
 * label so this mapping remains tolerant of the established UI wording.
 */
export const UI_SELECTED_RED_FLAG_RULE_IDS: Readonly<Record<string, string>> = {
  'Chest pain/pressure': 'RF-CARDIO-001',
  'Chest pain or pressure': 'RF-CARDIO-001',
  'Severe trouble breathing': 'RF-RESP-001',
  'Stroke symptoms': 'RF-NEURO-001',
  'Loss of consciousness': 'RF-CONSCIOUSNESS-001',
};
