/**
 * Care Navigation Navigator — Frontend Application Logic
 * Role-Based Architecture (Patient, Care Manager, Payer Administrator)
 * Vanilla ES Module SPA driver serving Flask endpoints.
 */

const state = {
  activeRole: 'PATIENT', // 'PATIENT' | 'CARE_MANAGER' | 'PAYER_ADMIN'
  activeRoute: 'triage',
  patients: [],
  analytics: null,
  currentEncounter: null,
  currentSessionId: null,
  selectedMemberId: null,
  patientProfile: {
    name: 'Jane Doe',
    age: 42,
    zip: '90210',
    contact: 'jane.doe@example.com',
    insurance: 'Self-Pay / Uninsured',
    prefSetting: 'Virtual Telehealth First',
    commPref: 'Email Updates',
  },
  selectedTimeSlot: 'Today at 2:00 PM',
};

// Helper Utilities
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const formatMoney = value =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value || 0);

const formatNumber = value =>
  new Intl.NumberFormat('en-US').format(value || 0);

const escapeHtml = value =>
  String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[char]);

/**
 * Fetch wrapper with unified error handling
 */
async function request(path, options = {}) {
  try {
    const response = await fetch(path, options);
    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}));
      throw new Error(errBody.error || `HTTP error ${response.status}`);
    }
    return await response.json();
  } catch (err) {
    console.error(`API Error [${path}]:`, err);
    throw err;
  }
}

/**
 * DYNAMIC SIDEBAR NAVIGATION RENDERER
 */
function renderSidebarNav(role) {
  const container = $('#nav-container');
  const sectionTitle = $('#nav-section-title');

  if (role === 'PATIENT') {
    if (sectionTitle) sectionTitle.textContent = 'My Care';
    container.innerHTML = `
      <button data-route="triage" class="nav-item ${state.activeRoute === 'triage' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        <span>My Health & Triage</span>
      </button>
      <button data-route="providers" class="nav-item ${state.activeRoute === 'providers' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M3 10h18M5 6l7-3 7 3M4 10v11M20 10v11M8 14v3M12 14v3M16 14v3"/></svg>
        <span>Find Care Options</span>
      </button>
      <button data-route="history" class="nav-item ${state.activeRoute === 'history' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>My Care History</span>
      </button>
      <button data-route="patient-profile" class="nav-item ${state.activeRoute === 'patient-profile' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span>My Health Profile</span>
      </button>
    `;
  } else if (role === 'CARE_MANAGER') {
    if (sectionTitle) sectionTitle.textContent = 'Care Management';
    container.innerHTML = `
      <button data-route="dashboard" class="nav-item ${state.activeRoute === 'dashboard' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
        <span>Prioritization Queue</span>
      </button>
      <button data-route="cohort" class="nav-item ${state.activeRoute === 'cohort' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <span>Member Cohort</span>
      </button>
      <button data-route="history" class="nav-item ${state.activeRoute === 'history' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>Member Audit History</span>
      </button>
      <button data-route="care-plan" class="nav-item ${state.activeRoute === 'care-plan' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
        <span>Care Plan Coordinator</span>
      </button>
      <button data-route="providers" class="nav-item ${state.activeRoute === 'providers' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M3 10h18M5 6l7-3 7 3M4 10v11M20 10v11M8 14v3M12 14v3M16 14v3"/></svg>
        <span>Care Settings Directory</span>
      </button>
    `;
  } else if (role === 'PAYER_ADMIN') {
    if (sectionTitle) sectionTitle.textContent = 'Population Management';
    container.innerHTML = `
      <button data-route="dashboard" class="nav-item ${state.activeRoute === 'dashboard' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
        <span>Population Overview</span>
      </button>
      <button data-route="cohort" class="nav-item ${state.activeRoute === 'cohort' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <span>ED Utilization & Risk</span>
      </button>
      <button data-route="history" class="nav-item ${state.activeRoute === 'history' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>Population Audit Trail</span>
      </button>
      <button data-route="providers" class="nav-item ${state.activeRoute === 'providers' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21h18M3 10h18M5 6l7-3 7 3M4 10v11M20 10v11M8 14v3M12 14v3M16 14v3"/></svg>
        <span>Network Destinations</span>
      </button>
    `;
  }
}

/**
 * ROLE SWITCHER STATE MANAGER
 * Prototype Demo Role Selector (Non-production demo role navigation)
 */
function setRole(role) {
  state.activeRole = role;

  const selector = $('#role-selector');
  if (selector) selector.value = role;

  const workspaceTitle = $('#workspace-title');

  if (role === 'PATIENT') {
    if (workspaceTitle) workspaceTitle.textContent = 'Patient Portal';

    // Page titles
    if ($('#triage-eyebrow')) $('#triage-eyebrow').textContent = 'Direct Symptom Screening';
    if ($('#triage-title')) $('#triage-title').textContent = 'How can we help you today?';
    if ($('#triage-subtitle')) $('#triage-subtitle').textContent = "Tell us what you're experiencing and we'll help you understand the safest next step.";
    if ($('#history-title')) $('#history-title').textContent = 'My Care History';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Review your symptom assessments and recorded care navigation choices.';
    if ($('#providers-title')) $('#providers-title').textContent = 'Find Care Options';

    // Hide internal Payer / Care Manager role sensitive sections
    $$('.care-manager-only, .payer-only').forEach(el => el.classList.add('role-hidden'));
  } else if (role === 'CARE_MANAGER') {
    if (workspaceTitle) workspaceTitle.textContent = 'Care Management Workspace';

    // Page titles
    if ($('#triage-eyebrow')) $('#triage-eyebrow').textContent = 'Clinical Decision Support';
    if ($('#triage-title')) $('#triage-title').textContent = 'Clinical Symptom Triage';
    if ($('#triage-subtitle')) $('#triage-subtitle').textContent = 'Assess member symptoms against authoritative safety rules and ML risk stratification.';
    if ($('#history-title')) $('#history-title').textContent = 'Member Interaction Audit History';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Review persistent member triage encounters and recorded navigation choices.';
    if ($('#providers-title')) $('#providers-title').textContent = 'Care Settings Directory';

    // Show Care Manager sections
    $$('.care-manager-only').forEach(el => el.classList.remove('role-hidden'));
    $$('.payer-only').forEach(el => el.classList.add('role-hidden'));
  } else if (role === 'PAYER_ADMIN') {
    if (workspaceTitle) workspaceTitle.textContent = 'Payer Administrator';

    // Page titles
    if ($('#triage-eyebrow')) $('#triage-eyebrow').textContent = 'Population Health Triage';
    if ($('#triage-title')) $('#triage-title').textContent = 'Population Symptom Screening';
    if ($('#triage-subtitle')) $('#triage-subtitle').textContent = 'Screening portal for population care management and risk stratification.';
    if ($('#history-title')) $('#history-title').textContent = 'Population Audit Trail';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Database audit trail of care navigation decisions across members.';

    // Show Payer sections
    $$('.care-manager-only, .payer-only').forEach(el => el.classList.remove('role-hidden'));
  }

  // Render Sidebar for Active Role
  renderSidebarNav(role);

  // Set default view if switching to a route disallowed for current role
  if (role === 'PATIENT' && ['dashboard', 'cohort', 'care-plan'].includes(state.activeRoute)) {
    route('triage');
  } else if (role !== 'PATIENT' && state.activeRoute === 'patient-profile') {
    route('dashboard');
  } else {
    route(state.activeRoute);
  }
}

/**
 * SPA View Navigation Router
 */
function route(name) {
  state.activeRoute = name;

  // Toggle view section visibility
  $$('.view').forEach(view => {
    view.classList.toggle('active', view.id === `${name}-view`);
  });

  // Toggle sidebar navigation active state
  $$('.nav-item').forEach(button => {
    button.classList.toggle('active', button.dataset.route === name);
  });

  // Mobile sidebar close on navigation
  $('#sidebar-nav')?.classList.remove('open');
  $('#panel-backdrop')?.classList.remove('open');

  window.scrollTo({ top: 0, behavior: 'smooth' });

  // View-specific trigger hooks
  if (name === 'dashboard') renderDashboard();
  if (name === 'cohort') renderCohort();
  if (name === 'providers') loadProviders();
  if (name === 'history') {
    if (state.activeRole === 'PATIENT') {
      loadPatientCareHistory();
    } else if (state.selectedMemberId) {
      loadPatientHistory(state.selectedMemberId);
    }
  }
}

/**
 * Badge HTML Generators
 */
function riskBadge(risk) {
  const normalized = String(risk || 'LOW').toUpperCase();
  const label = normalized === 'HIGH' ? 'High Priority' : normalized === 'MODERATE' ? 'Moderate Priority' : 'Low Priority';
  return `<span class="badge ${normalized.toLowerCase()}">${escapeHtml(label)}</span>`;
}

function acuityBadge(acuity) {
  const normalized = String(acuity || 'TELEHEALTH').toUpperCase();
  const label = normalized.replace('_', ' ');
  return `<span class="acuity-badge ${normalized.toLowerCase()}">${escapeHtml(label)}</span>`;
}

/**
 * Populate member selector dropdowns across views
 */
function populateMemberSelectors() {
  const optionsHtml = state.patients
    .map(p => `<option value="${p.id}">${escapeHtml(p.name)} (${escapeHtml(p.beneficiaryId)}) — ${p.riskLevel} Priority</option>`)
    .join('');

  const triageSelect = $('#triage-member-select');
  if (triageSelect) {
    triageSelect.innerHTML = `<option value="">Anonymous Patient Session (Unlinked)</option>` + optionsHtml;
  }

  const carePlanSelect = $('#care-plan-member');
  if (carePlanSelect) {
    carePlanSelect.innerHTML = `<option value="">Select a member...</option>` + optionsHtml;
  }

  const historySelect = $('#history-patient-select');
  if (historySelect) {
    historySelect.innerHTML = `<option value="">Select a member...</option>` + optionsHtml;
  }
}

/**
 * VIEW 1: SYMPTOM TRIAGE FORM & RESULT RENDERER
 */
async function submitTriage(event) {
  event.preventDefault();
  const resultPanel = $('#triage-result');
  resultPanel.innerHTML = `
    <div class="loading-spinner-wrap">
      <div class="spinner"></div>
      <p>Evaluating clinical safety rules & risk context...</p>
    </div>
  `;

  try {
    const selectedRedFlags = $$('input[name="red-flag"]:checked').map(cb => cb.value);
    const chiefComplaint = $('#chief-complaint').value.trim();
    const symptomsDuration = $('#symptom-duration').value;
    const patientId = $('#triage-member-select')?.value;

    const payload = {
      chiefComplaint,
      symptomsDuration,
      selectedRedFlags,
      hasRedFlags: selectedRedFlags.length > 0,
      associatedSymptoms: [],
    };

    // If Care Manager / Payer selected a member ID, link it
    if (patientId && state.activeRole !== 'PATIENT') {
      payload.patientId = patientId;
    }

    const data = await request('/api/triage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    state.currentEncounter = data;
    state.currentSessionId = data.sessionId;

    // Render session overview card in Patient Profile if updated
    updatePatientProfileSessionCard();

    // HARD SAFETY BOUNDARY: Check for Emergency Result State
    if (data.isEmergencyRedFlag || data.recommendedAcuity === 'EMERGENCY') {
      renderEmergencyResult(data, resultPanel);
    } else {
      renderNonEmergencyResult(data, resultPanel);
    }
  } catch (error) {
    resultPanel.innerHTML = `
      <div class="notice emergency">
        <strong>Assessment Error:</strong> ${escapeHtml(error.message)}
      </div>
    `;
  }
}

/**
 * Emergency Result UI Render (Hard Safety Boundary)
 * ABSOLUTE EMERGENCY STATE: NO provider rankings, NO telehealth booking, NO cost comparisons.
 */
function renderEmergencyResult(data, container) {
  container.innerHTML = `
    <div class="emergency-result-card">
      <div class="emergency-header-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 3-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      </div>
      <h2 class="emergency-title">EMERGENCY MEDICAL WARNING</h2>
      <p class="emergency-desc">
        Your reported symptoms indicate potential emergency warning signs requiring immediate medical evaluation.
      </p>
      <div class="emergency-actions-row">
        <a href="tel:911" class="button emergency-btn flex-center">
          <svg class="icon-inline" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>
          <span>Call 911 Immediately</span>
        </a>
      </div>

      <div class="emergency-notice-box">
        <strong>Authoritative Safety Rule:</strong> ${escapeHtml(data.clinicalRationale)}<br /><br />
        <em>Note: Non-emergency provider options, telehealth bookings, and cost-saving comparisons are strictly disabled for emergency triage results.</em>
      </div>
    </div>
  `;
}

/**
 * Non-Emergency Care Recommendation UI Render (With Simulated Telehealth Demo Workflow)
 */
function renderNonEmergencyResult(data, container) {
  // Show patient context only if Care Manager or Payer role
  const patientContextHtml = (data.patientContext && state.activeRole !== 'PATIENT')
    ? `<div class="patient-context-chip">
        <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span>Beneficiary: ${escapeHtml(data.patientContext.beneficiaryId)} &bull; ${riskBadge(data.patientContext.riskLevel)}</span>
       </div>`
    : '';

  const isTelehealth = data.recommendedAcuity === 'TELEHEALTH';

  const suitableProvidersHtml = (data.suitableProviders || []).slice(0, 3).map((p, idx) => `
    <div class="provider-option-item ${idx === 0 ? 'recommended-option' : ''}">
      <div class="provider-option-info">
        <h4>${idx === 0 ? '<span class="acuity-badge telehealth" style="padding:2px 8px; font-size:10px; margin-right:6px;">RECOMMENDED</span> ' : ''}${escapeHtml(p.name)}</h4>
        <p>${escapeHtml(p.operatingHours)} &bull; ~${p.currentWaitTimeMins} min wait &bull; ${formatMoney(p.copayAmount)} copay</p>
      </div>
      <button class="button secondary btn-select-provider" data-provider-id="${p.id}" data-acuity="${data.recommendedAcuity}">
        ${isTelehealth ? 'Select Option' : 'Select Provider'}
      </button>
    </div>
  `).join('');

  const simulatedTelehealthBookingHtml = isTelehealth ? `
    <div class="telehealth-demo-card">
      <h4>Simulated Demo Telehealth Appointment Booking</h4>
      <p><em>Demo appointment — No real appointment or clinical visit will be scheduled.</em></p>
      <div class="slot-buttons-grid">
        <button class="time-slot-btn ${state.selectedTimeSlot === 'Today at 2:00 PM' ? 'selected' : ''}" data-slot="Today at 2:00 PM">Today at 2:00 PM</button>
        <button class="time-slot-btn ${state.selectedTimeSlot === 'Today at 4:30 PM' ? 'selected' : ''}" data-slot="Today at 4:30 PM">Today at 4:30 PM</button>
        <button class="time-slot-btn ${state.selectedTimeSlot === 'Tomorrow at 10:00 AM' ? 'selected' : ''}" data-slot="Tomorrow at 10:00 AM">Tomorrow at 10:00 AM</button>
      </div>
      <button id="btn-confirm-telehealth-demo" class="button primary full-width">
        Confirm Demo Appointment (${escapeHtml(state.selectedTimeSlot)})
      </button>
    </div>
  ` : '';

  container.innerHTML = `
    <div class="recommendation-card">
      <div class="rec-badge-header">
        <div class="rec-title-wrap">
          <p class="eyebrow">Care Guidance Recommendation</p>
          <h2>${escapeHtml(data.recommendedSettingName)}</h2>
        </div>
        ${acuityBadge(data.recommendedAcuity)}
      </div>

      ${patientContextHtml}

      <div class="rationale-box">
        <strong>Clinical & Navigation Rationale:</strong><br />
        ${escapeHtml(data.clinicalRationale)}
      </div>

      <div class="notice">
        ${escapeHtml(data.safetyDisclaimer)}
      </div>

      <div>
        <h3 class="eyebrow">Recommended Care Options</h3>
        <div class="rec-providers-list">
          ${suitableProvidersHtml || '<p class="muted">No specific local options listed; proceed to nearest primary care clinic.</p>'}
        </div>
      </div>

      ${simulatedTelehealthBookingHtml}
    </div>
  `;

  // Bind provider selection buttons
  $$('.btn-select-provider', container).forEach(btn => {
    btn.addEventListener('click', () => {
      recordNavigationAction(btn.dataset.providerId, btn.dataset.acuity, 'PROVIDER_SELECTED');
    });
  });

  // Bind time slot selection buttons
  $$('.time-slot-btn', container).forEach(btn => {
    btn.addEventListener('click', () => {
      $$('.time-slot-btn', container).forEach(b => b.classList.remove('selected'));
      btn.classList.add('selected');
      state.selectedTimeSlot = btn.dataset.slot;
      const confirmBtn = $('#btn-confirm-telehealth-demo');
      if (confirmBtn) confirmBtn.textContent = `Confirm Demo Appointment (${state.selectedTimeSlot})`;
    });
  });

  // Bind telehealth confirm demo button
  $('#btn-confirm-telehealth-demo', container)?.addEventListener('click', () => {
    const provId = (data.suitableProviders && data.suitableProviders[0]) ? data.suitableProviders[0].id : 'prov-telehealth-01';
    recordNavigationAction(provId, 'TELEHEALTH', 'APPOINTMENT_BOOKED', {
      appointmentTime: state.selectedTimeSlot,
      isSimulatedDemo: true,
      providerName: (data.suitableProviders && data.suitableProviders[0]) ? data.suitableProviders[0].name : '24/7 Virtual Telehealth Network',
    });
  });
}

/**
 * Record Navigation Action API call (POST /api/navigation/action)
 */
async function recordNavigationAction(providerId, acuity, actionType = 'PROVIDER_SELECTED', extraDetails = {}) {
  if (!state.currentEncounter) return;

  try {
    const payload = {
      encounterId: state.currentEncounter.encounterId,
      sessionId: state.currentSessionId,
      actionType,
      selectedProviderId: providerId,
      selectedAcuity: acuity,
      actionDetails: {
        selectedAt: new Date().toISOString(),
        roleMode: state.activeRole,
        ...extraDetails,
      },
    };
    if (state.currentEncounter.patientContext && state.activeRole !== 'PATIENT') {
      payload.patientId = state.currentEncounter.patientContext.patientId;
    }

    const res = await request('/api/navigation/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const msg = actionType === 'APPOINTMENT_BOOKED'
      ? `Simulated Telehealth Appointment Recorded! Action ID: ${res.actionId}. Slot: ${state.selectedTimeSlot}.`
      : `Selection Recorded! Action ID: ${res.actionId}. Your choice has been persisted.`;

    alert(msg);

    // Switch to history view
    route('history');
  } catch (err) {
    alert(`Failed to record action: ${err.message}`);
  }
}

/**
 * Update Patient Profile Session Card
 */
function updatePatientProfileSessionCard() {
  const container = $('#patient-profile-session-info');
  if (!container) return;

  if (state.currentEncounter) {
    container.innerHTML = `
      <div style="background: var(--paper); padding: 16px; border-radius: var(--radius); border: 1px solid var(--line);">
        <div class="flex-between">
          <div>
            <strong>Active Session #${state.currentEncounter.encounterId}</strong>
            <p class="muted" style="margin: 2px 0 0 0; font-size: 13px;">Chief Complaint: "${escapeHtml(state.currentEncounter.chiefComplaint)}"</p>
          </div>
          ${acuityBadge(state.currentEncounter.recommendedAcuity)}
        </div>
      </div>
    `;
  }
}

/**
 * VIEW 2: PATIENT CARE HISTORY (For Patient Role)
 */
async function loadPatientCareHistory() {
  const container = $('#history-timeline-container');
  if (!state.currentSessionId) {
    container.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        </div>
        <h3>No Care History in Current Session</h3>
        <p>Complete a symptom assessment on <strong>My Health</strong> to view your care timeline.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Loading your care history...</p></div>';

  try {
    const data = await request(`/api/navigation/session/${state.currentSessionId}`);
    const enc = data.encounter;
    const actions = data.actions || [];

    const actionsHtml = actions.map(act => `
      <div class="timeline-item">
        <div class="timeline-dot action-dot"></div>
        <div class="timeline-content">
          <div class="timeline-meta">
            <span>Action: <strong>${act.actionType === 'APPOINTMENT_BOOKED' ? 'Simulated Telehealth Booking' : escapeHtml(act.actionType)}</strong></span>
            <span>${new Date(act.recordedAt).toLocaleString()}</span>
          </div>
          <p class="timeline-desc">
            Care Setting: ${acuityBadge(act.selectedAcuity)} &bull; Provider ID: <strong>${escapeHtml(act.selectedProviderId || 'N/A')}</strong>
            ${act.actionDetails?.appointmentTime ? `<br /><small>Demo Appointment Slot: <strong>${escapeHtml(act.actionDetails.appointmentTime)}</strong></small>` : ''}
          </p>
        </div>
      </div>
    `).join('');

    container.innerHTML = `
      <div class="flex-between" style="margin-bottom: 20px;">
        <div>
          <h3>My Care Timeline</h3>
          <p class="muted">Patient Session: ${escapeHtml(state.currentSessionId.slice(0, 12))}...</p>
        </div>
      </div>
      <div class="timeline-list">
        <div class="timeline-item">
          <div class="timeline-dot"></div>
          <div class="timeline-content">
            <div class="timeline-meta">
              <span>Symptom Assessment #${enc.encounterId}</span>
              <span>${new Date(enc.createdAt).toLocaleString()}</span>
            </div>
            <h4 class="timeline-title">${escapeHtml(enc.chiefComplaint)}</h4>
            <p class="timeline-desc">
              Recommended Care: <strong>${escapeHtml(enc.recommendedSettingName)}</strong> (${acuityBadge(enc.recommendedAcuity)})
            </p>
          </div>
        </div>
        ${actionsHtml}
      </div>
    `;
  } catch (error) {
    container.innerHTML = `<div class="notice emergency">Failed to load care history: ${escapeHtml(error.message)}</div>`;
  }
}

/**
 * VIEW 3: PATIENT HISTORY TIMELINE (For Care Manager & Payer Roles)
 */
async function loadPatientHistory(patientId) {
  const container = $('#history-timeline-container');
  if (!patientId) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>No Member Selected</h3>
        <p>Select a beneficiary above to load persistent triage encounters and actions.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Loading interaction history from PostgreSQL...</p></div>';

  try {
    const data = await request(`/api/patients/${patientId}/history`);
    state.historyData = data;

    if (!data.encounters || data.encounters.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <h3>No History Found</h3>
          <p>No triage encounters have been recorded yet for beneficiary ${escapeHtml(data.beneficiaryId || patientId)}.</p>
        </div>
      `;
      return;
    }

    const encountersHtml = data.encounters.map(enc => {
      const actionsForEnc = (data.actions || []).filter(a => a.encounterId === enc.encounterId);
      const actionsListHtml = actionsForEnc.map(act => `
        <div class="timeline-item">
          <div class="timeline-dot action-dot"></div>
          <div class="timeline-content">
            <div class="timeline-meta">
              <span>Recorded Action: <strong>${escapeHtml(act.actionType)}</strong></span>
              <span>${new Date(act.recordedAt).toLocaleString()}</span>
            </div>
            <p class="timeline-desc">
              Selected Provider ID: <strong>${escapeHtml(act.selectedProviderId || 'None')}</strong> &bull; Acuity: ${acuityBadge(act.selectedAcuity)}
            </p>
          </div>
        </div>
      `).join('');

      return `
        <div class="timeline-item">
          <div class="timeline-dot"></div>
          <div class="timeline-content">
            <div class="timeline-meta">
              <span>Encounter ID: #${enc.encounterId} &bull; Session: ${escapeHtml(enc.sessionId.slice(0, 8))}...</span>
              <span>${new Date(enc.createdAt).toLocaleString()}</span>
            </div>
            <h4 class="timeline-title">${escapeHtml(enc.chiefComplaint)}</h4>
            <p class="timeline-desc">
              Recommendation: <strong>${escapeHtml(enc.recommendedSettingName)}</strong> (${acuityBadge(enc.recommendedAcuity)})
            </p>
          </div>
        </div>
        ${actionsListHtml}
      `;
    }).join('');

    container.innerHTML = `
      <div class="flex-between" style="margin-bottom: 20px;">
        <div>
          <h3>Beneficiary History Log</h3>
          <p class="muted">Member ID: ${escapeHtml(data.patientId)} &bull; Beneficiary ID: ${escapeHtml(data.beneficiaryId)}</p>
        </div>
        <div>
          <span class="badge low">${data.totalEncounters} Encounters</span>
          <span class="badge low">${data.totalActions} Actions</span>
        </div>
      </div>
      <div class="timeline-list">
        ${encountersHtml}
      </div>
    `;
  } catch (error) {
    container.innerHTML = `<div class="notice emergency">Failed to load history: ${escapeHtml(error.message)}</div>`;
  }
}

/**
 * VIEW 4: CARE OPTIONS PROVIDER DIRECTORY
 */
async function loadProviders() {
  const grid = $('#provider-grid');
  grid.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Loading care options...</p></div>';

  try {
    const type = $('#provider-type').value;
    const maxDist = $('#provider-distance').value;

    const params = new URLSearchParams();
    if (type !== 'ALL') params.set('type', type);
    if (maxDist) params.set('maxDistance', maxDist);

    const providers = await request(`/api/providers?${params}`);

    if (!providers || providers.length === 0) {
      grid.innerHTML = `
        <div class="empty-state">
          <h3>No Care Options Found</h3>
          <p>Try adjusting your search filters or distance radius.</p>
        </div>
      `;
      return;
    }

    grid.innerHTML = providers.map(p => `
      <article class="card provider-card">
        <div class="provider-header">
          <div>
            <p class="eyebrow">${escapeHtml((p.type || '').replace('_', ' '))}</p>
            <h3>${escapeHtml(p.name)}</h3>
          </div>
          <span class="distance-badge">${p.distanceMiles} mi</span>
        </div>
        <p class="provider-address">${escapeHtml(p.address)}<br />${escapeHtml(p.cityStateZip)}</p>
        <div class="provider-meta-row">
          <span>${p.isOpen247 ? 'Open 24/7' : escapeHtml(p.operatingHours)}</span>
          <span>~${p.currentWaitTimeMins} min wait</span>
        </div>
        <div class="provider-footer-row">
          <div>
            <strong>${formatMoney(p.copayAmount)} copay</strong>
            ${p.isDemo ? '<span class="demo-tag"> &bull; Demo Record</span>' : ''}
          </div>
          ${p.phone ? `<a class="provider-phone" href="tel:${escapeHtml(p.phone)}">${escapeHtml(p.phone)}</a>` : ''}
        </div>
      </article>
    `).join('');
  } catch (error) {
    grid.innerHTML = `<div class="notice emergency">Failed to load care options: ${escapeHtml(error.message)}</div>`;
  }
}

/**
 * VIEW 5: POPULATION OVERVIEW DASHBOARD
 */
function renderDashboard() {
  const a = state.analytics;
  if (!a) return;

  $('#stat-grid').innerHTML = [
    ['Total Members', formatNumber(a.totalPatients), 'PostgreSQL Member Population'],
    ['Total ED Visits', formatNumber(a.totalEdVisits), '12-Month Claims Ingestion'],
    ['Total ED Spend', formatMoney(a.totalEdSpend), '12-Month Emergency Department Spend'],
    ['Intervention Priority', `${a.avoidableEdPercentage || 0}%`, 'Population Prioritization Signal'],
  ].map(([label, val, detail]) => `
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value">${val}</div>
      <div class="stat-detail">${detail}</div>
    </div>
  `).join('');

  // Priority queue
  const highRiskMembers = state.patients.filter(p => p.riskLevel === 'HIGH').slice(0, 5);
  $('#priority-list').innerHTML = highRiskMembers.length
    ? highRiskMembers.map(p => `
        <div class="member-row-item">
          <div class="member-avatar">${p.name.split(' ').map(n => n[0]).slice(0, 2).join('')}</div>
          <div class="member-info">
            <strong>${escapeHtml(p.name)}</strong>
            <p>${escapeHtml(p.beneficiaryId)} &bull; ${p.edVisitCount12m} ED visits</p>
          </div>
          ${riskBadge(p.riskLevel)}
          <button class="button secondary btn-open-member" data-member-id="${p.id}">View Profile</button>
        </div>
      `).join('')
    : '<p class="muted">No high priority members found.</p>';

  // Bind queue view buttons
  $$('.btn-open-member', $('#priority-list')).forEach(btn => {
    btn.addEventListener('click', () => openMember(btn.dataset.memberId));
  });

  // Time pattern chart
  if (a.timeOfDayPattern && a.timeOfDayPattern.length > 0) {
    const maxVal = Math.max(...a.timeOfDayPattern.map(item => item.count), 1);
    $('#time-pattern').innerHTML = a.timeOfDayPattern.map(item => `
      <div class="bar-row">
        <span>${escapeHtml(item.timeSlot)}</span>
        <div class="bar-track">
          <div class="bar-fill" style="width: ${(item.count / maxVal) * 100}%"></div>
        </div>
        <strong>${item.count}</strong>
      </div>
    `).join('');
  } else {
    $('#time-pattern').innerHTML = '<p class="muted">Time pattern data is not available yet.</p>';
  }
}

/**
 * VIEW 6: MEMBER COHORT TABLE
 */
function renderCohort(patients = state.patients) {
  const container = $('#member-table');
  if (!patients || patients.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>No Members Found</h3>
        <p>No beneficiaries match the current filter criteria.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Beneficiary Member</th>
          <th>Priority Level</th>
          <th>12m ED Visits</th>
          <th>12m ED Spend</th>
          <th>Care Manager</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        ${patients.map(p => `
          <tr>
            <td>
              <strong>${escapeHtml(p.name)}</strong><br />
              <small class="muted">${escapeHtml(p.beneficiaryId)} &bull; Age ${p.age}</small>
            </td>
            <td>${riskBadge(p.riskLevel)}</td>
            <td><strong>${p.edVisitCount12m}</strong></td>
            <td>${formatMoney(p.totalEdSpend12m)}</td>
            <td>${escapeHtml(p.assignedCareManager || 'Unassigned')}</td>
            <td>
              <button class="button secondary btn-table-profile" data-member-id="${p.id}">
                Profile
              </button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;

  $$('.btn-table-profile', container).forEach(btn => {
    btn.addEventListener('click', () => openMember(btn.dataset.memberId));
  });
}

function filterCohort() {
  const query = $('#member-search').value.toLowerCase().trim();
  const risk = $('#risk-filter').value;

  const filtered = state.patients.filter(p => {
    const matchesRisk = !risk || p.riskLevel === risk;
    const matchesQuery = !query || [p.name, p.beneficiaryId, p.medicareType, ...(p.chronicConditions || [])]
      .join(' ')
      .toLowerCase()
      .includes(query);
    return matchesRisk && matchesQuery;
  });

  renderCohort(filtered);
}

/**
 * MEMBER PROFILE DRAWER (Care Manager & Payer Only)
 */
async function openMember(id) {
  if (state.activeRole === 'PATIENT') return; // Enforce privacy boundary

  const panel = $('#member-panel');
  const detail = $('#member-detail');
  panel.classList.add('open');
  $('#panel-backdrop').classList.add('open');
  detail.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div></div>';

  try {
    const patient = await request(`/api/patients/${id}`);
    detail.innerHTML = `
      <div class="flex-center" style="margin-bottom: 20px;">
        <div class="member-avatar" style="width: 52px; height: 52px; font-size: 18px;">
          ${patient.name.split(' ').map(p => p[0]).slice(0, 2).join('')}
        </div>
        <div>
          <h3 style="margin: 0; font-size: 18px;">${escapeHtml(patient.name)}</h3>
          <p class="muted" style="margin: 2px 0 4px 0;">${escapeHtml(patient.beneficiaryId)} &bull; Age ${patient.age}</p>
          ${riskBadge(patient.riskLevel)}
        </div>
      </div>

      <div style="border-top: 1px solid var(--line); padding-top: 16px; margin-top: 16px;">
        <h4 class="eyebrow">Utilization Context</h4>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px;">
          <div style="background: var(--paper); padding: 12px; border-radius: var(--radius);">
            <strong style="font-size: 18px; display: block;">${patient.edVisitCount12m}</strong>
            <span class="muted" style="font-size: 12px;">ED Visits (12m)</span>
          </div>
          <div style="background: var(--paper); padding: 12px; border-radius: var(--radius);">
            <strong style="font-size: 18px; display: block;">${formatMoney(patient.totalEdSpend12m)}</strong>
            <span class="muted" style="font-size: 12px;">ED Spend (12m)</span>
          </div>
        </div>
      </div>

      <div style="border-top: 1px solid var(--line); padding-top: 16px; margin-top: 16px;">
        <h4 class="eyebrow">Clinical Information</h4>
        <p style="margin: 6px 0;"><strong>Coverage:</strong> ${escapeHtml(patient.medicareType)}</p>
        <p style="margin: 6px 0;"><strong>Conditions:</strong> ${escapeHtml((patient.chronicConditions || []).join(', ') || 'None listed')}</p>
      </div>

      <div style="border-top: 1px solid var(--line); padding-top: 20px; margin-top: 20px;">
        <button id="btn-drawer-history" class="button secondary full-width" style="margin-bottom: 10px;">
          View Member Audit Log
        </button>
        <button id="btn-drawer-plan" class="button primary full-width">
          Create Care Plan
        </button>
      </div>
    `;

    $('#btn-drawer-history')?.addEventListener('click', () => {
      closeMember();
      state.selectedMemberId = patient.id;
      $('#history-patient-select').value = patient.id;
      route('history');
    });

    $('#btn-drawer-plan')?.addEventListener('click', () => {
      closeMember();
      $('#care-plan-member').value = patient.id;
      route('care-plan');
    });
  } catch (error) {
    detail.innerHTML = `<div class="notice emergency">Failed to load member profile: ${escapeHtml(error.message)}</div>`;
  }
}

function closeMember() {
  $('#member-panel').classList.remove('open');
  $('#panel-backdrop').classList.remove('open');
}

/**
 * VIEW 7: CARE COORDINATOR PLAN GENERATOR
 */
async function generateCarePlan(event) {
  event.preventDefault();
  const resultPanel = $('#care-plan-result');
  resultPanel.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Generating care navigation plan...</p></div>';

  try {
    const patientId = $('#care-plan-member').value;
    const customNotes = $('#care-plan-notes').value;

    const plan = await request('/api/generate-care-plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ patientId, customNotes }),
    });

    resultPanel.innerHTML = `
      <div class="recommendation-card">
        <p class="eyebrow">Outreach Plan</p>
        <h2>${escapeHtml(plan.patientName || 'Beneficiary Plan')}</h2>
        <p>${escapeHtml(plan.riskSummary || '')}</p>

        <h4 class="eyebrow">Priority Interventions</h4>
        <ol style="padding-left: 20px; margin: 8px 0 16px 0;">
          ${(plan.recommendedInterventions || []).map(item => `
            <li style="margin-bottom: 8px;">
              <strong>${escapeHtml(item.category)}:</strong> ${escapeHtml(item.action)}
              <span class="muted">(${escapeHtml(item.timeline)})</span>
            </li>
          `).join('')}
        </ol>

        <div class="notice">
          <strong>Member Outreach Script:</strong><br />
          ${escapeHtml(plan.memberOutreachScript || '')}
        </div>
      </div>
    `;
  } catch (error) {
    resultPanel.innerHTML = `<div class="notice emergency">Failed to generate care plan: ${escapeHtml(error.message)}</div>`;
  }
}

/**
 * PATIENT SELF-ENTRY PROFILE SAVE
 */
function saveSelfProfile(event) {
  event.preventDefault();
  state.patientProfile = {
    name: $('#self-patient-name').value.trim(),
    age: parseInt($('#self-patient-age').value, 10) || 40,
    zip: $('#self-patient-zip').value.trim(),
    contact: $('#self-patient-contact').value.trim(),
    insurance: $('#self-patient-insurance').value,
    prefSetting: $('#self-patient-pref-setting').value,
    commPref: $('#self-patient-comm').value,
  };

  alert(`Profile Saved! Hello, ${state.patientProfile.name}. Your personal profile has been updated.`);
}

/**
 * CARE COPILOT CHAT DRAWER
 */
async function submitChat(event) {
  event.preventDefault();
  const input = $('#chat-input');
  const messagesContainer = $('#chat-messages');
  const messageText = input.value.trim();

  if (!messageText) return;

  messagesContainer.insertAdjacentHTML('beforeend', `
    <div class="chat-message user">
      <p>${escapeHtml(messageText)}</p>
    </div>
  `);
  input.value = '';
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  try {
    const response = await request('/api/chat-copilot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: messageText }),
    });

    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message assistant">
        <p>${escapeHtml(response.reply || 'Assistant response received.')}</p>
      </div>
    `);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  } catch (error) {
    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message assistant">
        <p>I encountered an error: ${escapeHtml(error.message)}</p>
      </div>
    `);
  }
}

/**
 * Bind DOM Event Handlers
 */
function bindEvents() {
  // Role Selector listener
  $('#role-selector')?.addEventListener('change', e => {
    setRole(e.target.value);
  });

  // Navigation event delegation
  document.addEventListener('click', event => {
    const routeBtn = event.target.closest('[data-route]');
    if (routeBtn) {
      event.preventDefault();
      route(routeBtn.dataset.route);
    }

    if (event.target.closest('[data-close-panel]') || event.target === $('#panel-backdrop')) {
      closeMember();
      $('#sidebar-nav')?.classList.remove('open');
    }

    if (event.target.closest('[data-close-chat]')) {
      $('#chat-panel')?.classList.remove('open');
    }
  });

  // Mobile sidebar toggle
  $('#sidebar-toggle')?.addEventListener('click', () => {
    $('#sidebar-nav')?.classList.toggle('open');
    $('#panel-backdrop')?.classList.toggle('open');
  });

  // Filters & Form listeners
  $('#member-search')?.addEventListener('input', filterCohort);
  $('#risk-filter')?.addEventListener('change', filterCohort);
  $('#provider-type')?.addEventListener('change', loadProviders);
  $('#provider-distance')?.addEventListener('change', loadProviders);

  $('#triage-form')?.addEventListener('submit', submitTriage);
  $('#patient-self-profile-form')?.addEventListener('submit', saveSelfProfile);
  $('#care-plan-form')?.addEventListener('submit', generateCarePlan);

  $('#load-history-btn')?.addEventListener('click', () => {
    const pId = $('#history-patient-select').value;
    loadPatientHistory(pId);
  });

  $('#chat-toggle')?.addEventListener('click', () => $('#chat-panel')?.classList.add('open'));
  $('#chat-form')?.addEventListener('submit', submitChat);
}

/**
 * Application Initialization Entry Point
 */
async function init() {
  try {
    // Populate Patient Self Profile Form Defaults
    if ($('#self-patient-name')) $('#self-patient-name').value = state.patientProfile.name;
    if ($('#self-patient-age')) $('#self-patient-age').value = state.patientProfile.age;
    if ($('#self-patient-zip')) $('#self-patient-zip').value = state.patientProfile.zip;
    if ($('#self-patient-contact')) $('#self-patient-contact').value = state.patientProfile.contact;
    if ($('#self-patient-insurance')) $('#self-patient-insurance').value = state.patientProfile.insurance;
    if ($('#self-patient-pref-setting')) $('#self-patient-pref-setting').value = state.patientProfile.prefSetting;
    if ($('#self-patient-comm')) $('#self-patient-comm').value = state.patientProfile.commPref;

    // Load Core Datasets
    const [patientsData, analyticsData] = await Promise.all([
      request('/api/patients'),
      request('/api/analytics'),
    ]);

    state.patients = patientsData || [];
    state.analytics = analyticsData || null;

    // Populate Dynamic UI Elements
    populateMemberSelectors();
    bindEvents();

    // Default Role & View Initialization
    setRole('PATIENT');
  } catch (error) {
    console.error('Initialization error:', error);
    document.querySelector('.main-content').innerHTML = `
      <div class="card">
        <h2>Unable to load clinical workspace</h2>
        <div class="notice emergency">${escapeHtml(error.message)}</div>
      </div>
    `;
  }
}

// Start application
init();
