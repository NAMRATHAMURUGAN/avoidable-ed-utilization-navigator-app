export type AcuityLevel = 'EMERGENCY' | 'URGENT_CARE' | 'TELEHEALTH' | 'PRIMARY_CARE' | 'RETAIL_CLINIC';

export type NyuEdCategory = 
  | 'NON_EMERGENT' // e.g., skin rash, suture removal
  | 'EMERGENT_PRIMARY_CARE_TREATABLE' // e.g., mild asthma flare, low fever, UTI
  | 'EMERGENT_PREVENTABLE' // e.g., unmanaged hypertension, diabetic ketoacidosis flare
  | 'EMERGENT_ED_NEEDED'; // e.g., acute appendicitis, heart attack, major fracture

export type RiskLevel = 'HIGH' | 'MODERATE' | 'LOW';

export interface EdClaim {
  id: string;
  claimDate: string;
  facilityName: string;
  chiefComplaint: string;
  primaryIcd10: string;
  diagnosisDescription: string;
  nyuCategory: NyuEdCategory;
  totalCost: number;
  avoidableCostEstimate: number;
  timeOfDay: 'MORNING' | 'AFTERNOON' | 'EVENING' | 'NIGHT_AFTER_HOURS';
  dayOfWeek: 'WEEKDAY' | 'WEEKEND';
  wasAdmitted: boolean;
  alternativeRecommended: AcuityLevel;
}

export interface PatientProfile {
  id: string;
  beneficiaryId: string;
  name: string;
  age: number;
  gender: 'Female' | 'Male' | 'Other';
  zipCode: string;
  medicareType: 'Part A & B (FFS)' | 'Medicare Advantage';
  primaryCareProvider: string;
  lastPcpVisitDate: string | null;
  chronicConditions: string[];
  sdohBarriers: string[]; // e.g. "Transportation Deficit", "Lack of Evening PCP Hours", "Pharmacy Non-Adherence"
  edVisitCount12m: number;
  avoidableEdCount12m: number;
  totalEdSpend12m: number;
  avoidableEdSpend12m: number;
  riskLevel: RiskLevel;
  claimsHistory: EdClaim[];
  latestCarePlan?: string;
  assignedCareManager?: string;
}

export interface ProviderAlternative {
  id: string;
  name: string;
  type: AcuityLevel;
  address: string;
  cityStateZip: string;
  distanceMiles: number;
  isOpen247: boolean;
  operatingHours: string;
  currentWaitTimeMins: number;
  copayAmount: number;
  averageTotalCost: number;
  phone: string;
  telehealthUrl?: string;
  acceptsMedicare: boolean;
  services: string[];
}

export interface TriageRequest {
  patientAge?: number;
  chiefComplaint: string;
  symptomsDuration: string;
  associatedSymptoms: string[];
  hasRedFlags: boolean;
  selectedRedFlags: string[];
  preferredLocationZip?: string;
}

export interface TriageResponse {
  isEmergencyRedFlag: boolean;
  recommendedAcuity: AcuityLevel;
  recommendedSettingName: string;
  urgencyLevel: 'IMMEDIATE_911_ER' | 'SAME_DAY_URGENT' | 'SAME_DAY_TELEHEALTH' | 'SCHEDULED_PCP';
  clinicalRationale: string;
  safetyDisclaimer: string;
  estimatedCostComparison: {
    erEstimatedCost: number;
    recommendedCost: number;
    potentialSavings: number;
  };
  estimatedWaitTimeComparison: {
    erWaitMinutes: number;
    recommendedWaitMinutes: number;
  };
  suitableProviders: ProviderAlternative[];
}

export interface CarePlanRequest {
  patientId: string;
  focusArea?: string;
  customNotes?: string;
}

export interface CarePlanResponse {
  patientName: string;
  riskSummary: string;
  nyuPatternAnalysis: string;
  rootCauseInsights: string[];
  recommendedInterventions: {
    category: string;
    action: string;
    assignedTo: string;
    timeline: string;
  }[];
  memberOutreachScript: string;
  smsNotificationTemplate: string;
}

export interface PopulationAnalytics {
  totalPatients: number;
  totalEdVisits: number;
  avoidableEdVisitsCount: number;
  avoidableEdPercentage: number;
  totalEdSpend: number;
  potentialSavings: number;
  nyuCategoryBreakdown: {
    category: NyuEdCategory;
    label: string;
    count: number;
    percentage: number;
    color: string;
  }[];
  timeOfDayPattern: {
    timeSlot: string;
    count: number;
    avoidableCount: number;
  }[];
  dayOfWeekPattern: {
    dayType: string;
    count: number;
    avoidableCount: number;
  }[];
  topAvoidableDiagnoses: {
    icd10: string;
    description: string;
    count: number;
    avgCostEr: number;
    avgCostClinic: number;
  }[];
  sdohBarrierDistribution: {
    barrier: string;
    patientCount: number;
  }[];
}

export interface ChatMessage {
  id: string;
  sender: 'user' | 'assistant';
  text: string;
  timestamp: string;
  patientContext?: string;
}
