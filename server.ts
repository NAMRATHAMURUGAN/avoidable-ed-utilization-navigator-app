import express from 'express';
import dotenv from 'dotenv';
import { GoogleGenAI, Type } from '@google/genai';
import {
  MOCK_PATIENTS,
  MOCK_PROVIDERS,
  MOCK_POPULATION_ANALYTICS
} from './src/data/mockCmsData';
import { TriageRequest, TriageResponse, ProviderAlternative } from './src/types';
import { evaluateSafety } from './src/services/safetyEngine';

dotenv.config();

const app = express();
const PORT = Number(process.env.PORT) || 3000;

app.use(express.json());

// Initialize Gemini Client
const ai = new GoogleGenAI({
  apiKey: process.env.GEMINI_API_KEY || '',
  httpOptions: {
    headers: {
      'User-Agent': 'aistudio-build',
    },
  },
});

// ==================== API ROUTES ====================

// GET /api/patients - Return synthetic CMS patient cohort
app.get('/api/patients', (req, res) => {
  const { risk, search } = req.query;
  let results = [...MOCK_PATIENTS];

  if (risk && typeof risk === 'string') {
    results = results.filter((p) => p.riskLevel.toLowerCase() === risk.toLowerCase());
  }

  if (search && typeof search === 'string') {
    const q = search.toLowerCase();
    results = results.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.beneficiaryId.toLowerCase().includes(q) ||
        p.chronicConditions.some((c) => c.toLowerCase().includes(q)) ||
        p.zipCode.includes(q)
    );
  }

  res.json(results);
});

// GET /api/patients/:id - Return patient detail
app.get('/api/patients/:id', (req, res) => {
  const patient = MOCK_PATIENTS.find((p) => p.id === req.params.id);
  if (!patient) {
    return res.status(404).json({ error: 'Patient not found' });
  }
  res.json(patient);
});

// GET /api/analytics - Return aggregate population ED analytics
app.get('/api/analytics', (req, res) => {
  res.json(MOCK_POPULATION_ANALYTICS);
});

// GET /api/providers - Return provider directory
app.get('/api/providers', (req, res) => {
  const { type, maxDistance, zip } = req.query;
  let results = [...MOCK_PROVIDERS];

  if (type && typeof type === 'string' && type !== 'ALL') {
    results = results.filter((p) => p.type === type);
  }

  if (maxDistance && !isNaN(Number(maxDistance))) {
    const maxDistNum = Number(maxDistance);
    results = results.filter((p) => p.distanceMiles <= maxDistNum);
  }

  res.json(results);
});

// POST /api/triage - Patient & Caregiver Symptom Triage with Safety First Guardrail
app.post('/api/triage', async (req, res) => {
  try {
    const triageReq: TriageRequest = req.body;
    // The deterministic engine is the authoritative configured-warning-sign
    // screen. It is not diagnostic and neither ML nor LLM output can override it.
    const safetyDecision = evaluateSafety(triageReq);

    if (safetyDecision.isEmergency) {
      const emergencyResponse: TriageResponse = {
        isEmergencyRedFlag: true,
        recommendedAcuity: 'EMERGENCY',
        recommendedSettingName: 'Nearest Hospital Emergency Department or Call 911',
        urgencyLevel: 'IMMEDIATE_911_ER',
        clinicalRationale:
          'Emergency Red Flags Detected: Symptoms such as chest pressure, acute severe shortness of breath, stroke signs, or loss of consciousness require immediate emergency medical evaluation. Do not delay seeking emergency services.',
        safetyDisclaimer:
          'EMERGENCY WARNING: Your reported symptoms indicate a potential life-threatening emergency. Call 911 or proceed immediately to the nearest Emergency Department. This tool is for care navigation only and NEVER replaces emergency medical care.',
        estimatedCostComparison: {
          erEstimatedCost: 1800,
          recommendedCost: 1800,
          potentialSavings: 0,
        },
        estimatedWaitTimeComparison: {
          erWaitMinutes: 0,
          recommendedWaitMinutes: 0,
        },
        suitableProviders: [],
      };
      return res.json(emergencyResponse);
    }

    // Call Gemini 3.6 Flash for Clinical Acuity Analysis & Recommendation
    if (process.env.GEMINI_API_KEY) {
      const prompt = `You are an expert clinical triage & care management navigator system.
Analyze the following patient reported symptoms and determine the appropriate level of care, prioritizing SAFETY FIRST.

Patient Age: ${triageReq.patientAge || 'Unknown'}
Chief Complaint: ${triageReq.chiefComplaint}
Symptom Duration: ${triageReq.symptomsDuration}
Associated Symptoms: ${triageReq.associatedSymptoms?.join(', ') || 'None reported'}

Instructions:
1. Assess acuity: EMERGENCY, URGENT_CARE, TELEHEALTH, or PRIMARY_CARE.
2. Provide a clear, empathetic clinical rationale.
3. Compare costs (ER average $1,800 vs Urgent Care $180 vs Telehealth $45 vs Primary Care $110).
4. Compare wait times (ER average 180 mins vs Urgent Care 20 mins vs Telehealth 10 mins).
5. Always include a safety disclaimer reinforcing that emergency care is never blocked.

Respond strictly in valid JSON format matching this schema:
{
  "isEmergencyRedFlag": boolean,
  "recommendedAcuity": "EMERGENCY" | "URGENT_CARE" | "TELEHEALTH" | "PRIMARY_CARE" | "RETAIL_CLINIC",
  "recommendedSettingName": string,
  "urgencyLevel": "IMMEDIATE_911_ER" | "SAME_DAY_URGENT" | "SAME_DAY_TELEHEALTH" | "SCHEDULED_PCP",
  "clinicalRationale": string,
  "safetyDisclaimer": string,
  "estimatedCostComparison": {
    "erEstimatedCost": number,
    "recommendedCost": number,
    "potentialSavings": number
  },
  "estimatedWaitTimeComparison": {
    "erWaitMinutes": number,
    "recommendedWaitMinutes": number
  }
}`;

      const aiRes = await ai.models.generateContent({
        model: 'gemini-3.6-flash',
        contents: prompt,
        config: {
          responseMimeType: 'application/json',
          responseSchema: {
            type: Type.OBJECT,
            properties: {
              isEmergencyRedFlag: { type: Type.BOOLEAN },
              recommendedAcuity: { type: Type.STRING },
              recommendedSettingName: { type: Type.STRING },
              urgencyLevel: { type: Type.STRING },
              clinicalRationale: { type: Type.STRING },
              safetyDisclaimer: { type: Type.STRING },
              estimatedCostComparison: {
                type: Type.OBJECT,
                properties: {
                  erEstimatedCost: { type: Type.NUMBER },
                  recommendedCost: { type: Type.NUMBER },
                  potentialSavings: { type: Type.NUMBER },
                },
                required: ['erEstimatedCost', 'recommendedCost', 'potentialSavings'],
              },
              estimatedWaitTimeComparison: {
                type: Type.OBJECT,
                properties: {
                  erWaitMinutes: { type: Type.NUMBER },
                  recommendedWaitMinutes: { type: Type.NUMBER },
                },
                required: ['erWaitMinutes', 'recommendedWaitMinutes'],
              },
            },
            required: [
              'isEmergencyRedFlag',
              'recommendedAcuity',
              'recommendedSettingName',
              'urgencyLevel',
              'clinicalRationale',
              'safetyDisclaimer',
              'estimatedCostComparison',
              'estimatedWaitTimeComparison',
            ],
          },
        },
      });

      const parsed: TriageResponse = JSON.parse(aiRes.text || '{}');

      // Gemini may assist non-emergency navigation only; deterministic safety
      // screening remains the sole authority for emergency-warning-sign status.
      parsed.isEmergencyRedFlag = false;

      // Filter matching local providers
      const suitable = MOCK_PROVIDERS.filter((p) => p.type === parsed.recommendedAcuity);
      parsed.suitableProviders = suitable.length > 0 ? suitable : MOCK_PROVIDERS.slice(0, 3);

      return res.json(parsed);
    } else {
      // Fallback heuristics if API Key is not set
      const isUrgent = triageReq.chiefComplaint.toLowerCase().includes('cut') || triageReq.chiefComplaint.toLowerCase().includes('sprain');
      const recommendedAcuity = isUrgent ? 'URGENT_CARE' : 'TELEHEALTH';

      const fallback: TriageResponse = {
        isEmergencyRedFlag: false,
        recommendedAcuity,
        recommendedSettingName: isUrgent ? 'Local Urgent Care Center' : '24/7 Telehealth Nurse Line',
        urgencyLevel: isUrgent ? 'SAME_DAY_URGENT' : 'SAME_DAY_TELEHEALTH',
        clinicalRationale: `Based on your reported complaint of "${triageReq.chiefComplaint}" without high-risk red flags, care can be safely delivered at a ${isUrgent ? 'Same-day Urgent Care Center' : '24/7 Virtual Telehealth session'}.`,
        safetyDisclaimer: 'SAFETY NOTICE: If symptoms worsen, or if you develop chest pain, shortness of breath, or severe pain, proceed to the nearest Emergency Room or call 911 immediately.',
        estimatedCostComparison: {
          erEstimatedCost: 1800,
          recommendedCost: isUrgent ? 180 : 45,
          potentialSavings: isUrgent ? 1620 : 1755,
        },
        estimatedWaitTimeComparison: {
          erWaitMinutes: 180,
          recommendedWaitMinutes: isUrgent ? 20 : 8,
        },
        suitableProviders: MOCK_PROVIDERS.filter((p) => p.type === recommendedAcuity),
      };

      return res.json(fallback);
    }
  } catch (error: any) {
    console.error('Error in /api/triage:', error);
    res.status(500).json({ error: error.message || 'Failed to process triage' });
  }
});

// POST /api/generate-care-plan - Generate AI Care Management Plan for Patient
app.post('/api/generate-care-plan', async (req, res) => {
  try {
    const { patientId, customNotes } = req.body;
    const patient = MOCK_PATIENTS.find((p) => p.id === patientId) || MOCK_PATIENTS[0];

    if (!process.env.GEMINI_API_KEY) {
      return res.json({
        patientName: patient.name,
        riskSummary: `${patient.name} has had ${patient.edVisitCount12m} ED visits in the past 12 months (${patient.avoidableEdCount12m} flagged as potentially avoidable). Total ED spend is $${patient.totalEdSpend12m}.`,
        nyuPatternAnalysis: 'Pattern shows frequent visits during late evening and weekend hours, primarily driven by mild respiratory flares and medication refill requests after primary care offices are closed.',
        rootCauseInsights: [
          'Lack of after-hours primary care contact options',
          'Barriers in automated pharmacy refills for chronic disease medications',
          'Patient uncertainty regarding when telehealth/nurse advice line is appropriate'
        ],
        recommendedInterventions: [
          {
            category: 'Care Access',
            action: 'Enroll patient in 24/7 Medicare Telehealth Nurse Hotline with speed-dial card',
            assignedTo: 'Care Manager RN',
            timeline: 'Within 48 Hours'
          },
          {
            category: 'Pharmacy Support',
            action: 'Set up 90-day automatic home delivery for maintenance inhalers and blood pressure medications',
            assignedTo: 'Pharmacy Coordinator',
            timeline: 'Within 3 Days'
          },
          {
            category: 'PCP Engagement',
            action: 'Schedule post-ED discharge check-in with Dr. Chen and setup transportation voucher',
            assignedTo: 'Community Health Worker',
            timeline: 'Within 5 Days'
          }
        ],
        memberOutreachScript: `Hello ${patient.name}, this is Sarah from your Medicare Care Management team. We noticed you recently had to visit the emergency department. First and foremost, we want to ensure you are feeling better! We also want to let you know that you have access to a 24/7 nurse advice phone line and video doctor visits at zero copay whenever your regular clinic is closed. If you ever feel you have a medical emergency, please go to the ER or call 911 right away. For minor issues or refills, would you like us to mail you a quick-contact card for our 24/7 nurse line?`,
        smsNotificationTemplate: `Hi ${patient.name.split(' ')[0]}, this is your care team! Feeling unwell after hours? You have 24/7 free access to a telehealth doctor line at 1-800-555-CARE. Save this number to your phone! (In a true emergency, always call 911).`
      });
    }

    const prompt = `You are a clinical care manager AI generating a personalized, empathetic, evidence-based care plan for a patient with a pattern of avoidable Emergency Department (ED) visits.

Patient Profile:
- Name: ${patient.name} (${patient.age} y/o, ${patient.gender})
- Medicare Type: ${patient.medicareType}
- Chronic Conditions: ${patient.chronicConditions.join(', ')}
- SDOH Barriers: ${patient.sdohBarriers.join(', ')}
- 12-Month ED Visits: ${patient.edVisitCount12m} (${patient.avoidableEdCount12m} potentially avoidable)
- Total ED Spend: $${patient.totalEdSpend12m} ($${patient.avoidableEdSpend12m} avoidable)
- Primary Care Provider: ${patient.primaryCareProvider} (Last Visit: ${patient.lastPcpVisitDate || 'Unknown'})
- Recent Claims Complaints: ${patient.claimsHistory.map((c) => `[${c.claimDate} - ${c.chiefComplaint} (${c.nyuCategory}) - $${c.totalCost}]`).join('; ')}
${customNotes ? `Care Manager Custom Notes: ${customNotes}` : ''}

CRITICAL MANDATE:
- Care plans must NEVER imply that emergency care should be discouraged or blocked in a true emergency.
- Use empathetic, empowering, non-punitive language. Focus on better access, navigation, 24/7 telehealth, pharmacy home delivery, and post-ED PCP follow-ups.

Return JSON strictly matching this schema:
{
  "patientName": string,
  "riskSummary": string,
  "nyuPatternAnalysis": string,
  "rootCauseInsights": string[],
  "recommendedInterventions": [
    {
      "category": string,
      "action": string,
      "assignedTo": string,
      "timeline": string
    }
  ],
  "memberOutreachScript": string,
  "smsNotificationTemplate": string
}`;

    const aiRes = await ai.models.generateContent({
      model: 'gemini-3.6-flash',
      contents: prompt,
      config: {
        responseMimeType: 'application/json',
        responseSchema: {
          type: Type.OBJECT,
          properties: {
            patientName: { type: Type.STRING },
            riskSummary: { type: Type.STRING },
            nyuPatternAnalysis: { type: Type.STRING },
            rootCauseInsights: {
              type: Type.ARRAY,
              items: { type: Type.STRING },
            },
            recommendedInterventions: {
              type: Type.ARRAY,
              items: {
                type: Type.OBJECT,
                properties: {
                  category: { type: Type.STRING },
                  action: { type: Type.STRING },
                  assignedTo: { type: Type.STRING },
                  timeline: { type: Type.STRING },
                },
                required: ['category', 'action', 'assignedTo', 'timeline'],
              },
            },
            memberOutreachScript: { type: Type.STRING },
            smsNotificationTemplate: { type: Type.STRING },
          },
          required: [
            'patientName',
            'riskSummary',
            'nyuPatternAnalysis',
            'rootCauseInsights',
            'recommendedInterventions',
            'memberOutreachScript',
            'smsNotificationTemplate',
          ],
        },
      },
    });

    const carePlan = JSON.parse(aiRes.text || '{}');
    res.json(carePlan);
  } catch (error: any) {
    console.error('Error generating care plan:', error);
    res.status(500).json({ error: error.message || 'Failed to generate care plan' });
  }
});

// POST /api/chat-copilot - AI Care Manager Copilot Assistant
app.post('/api/chat-copilot', async (req, res) => {
  try {
    const { message, patientId } = req.body;
    let contextStr = 'Overall Population Context: CMS Medicare Synthetic Enrollment & FFS Claims Data. Focus on identifying patterns of avoidable ED visits, recommending lower-acuity alternatives (Urgent Care, Telehealth, Primary Care), addressing SDOH barriers, and upholding safety-first protocols.';

    if (patientId) {
      const patient = MOCK_PATIENTS.find((p) => p.id === patientId);
      if (patient) {
        contextStr += `\nSelected Patient Context: ${patient.name}, Age ${patient.age}, ${patient.chronicConditions.join(', ')}. 12m ED visits: ${patient.edVisitCount12m} ($${patient.totalEdSpend12m}). Barriers: ${patient.sdohBarriers.join(', ')}.`;
      }
    }

    if (!process.env.GEMINI_API_KEY) {
      return res.json({
        reply: `[AI Copilot - Offline Mode] Thank you for your question: "${message}". In full-service mode with GEMINI_API_KEY, I analyze synthetic CMS claims data, NYU ED classification algorithms, and clinical guidelines to help care managers spot avoidable visit trends, organize member outreach, and optimize lower-acuity navigation.`,
      });
    }

    const aiRes = await ai.models.generateContent({
      model: 'gemini-3.6-flash',
      contents: message,
      config: {
        systemInstruction: `You are an expert Care Management AI Copilot specializing in Medicare emergency department utilization, risk stratification, NYU-ED algorithm classification, and lower-acuity care navigation.
Context: ${contextStr}

Safety Directive: Always remind care managers that emergency care must NEVER be discouraged or gatekept for true emergencies.
Be direct, clinical, actionable, and structured with bullet points.`,
      },
    });

    res.json({ reply: aiRes.text });
  } catch (error: any) {
    console.error('Error in chat copilot:', error);
    res.status(500).json({ error: error.message || 'Chat failed' });
  }
});

// ==================== STATIC FRONTEND ====================
function startServer() {
  const staticDirectory = process.env.NODE_ENV === 'production' ? 'dist/public' : 'public';
  app.use(express.static(staticDirectory));
  app.get('*', (req, res) => {
    res.sendFile('index.html', { root: staticDirectory });
  });
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`Avoidable ED Utilization Navigator running on http://0.0.0.0:${PORT}`);
  });
}

startServer();
