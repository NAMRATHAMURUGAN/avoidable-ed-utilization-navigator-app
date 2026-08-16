/**
 * Care Navigation Navigator — Frontend Application Logic
 * Two-role architecture: Patient / Member, and Payer.
 * Vanilla ES Module SPA driver serving Flask endpoints.
 */

const state = {
  currentUser: null, // { id, email, role } from GET /api/auth/me; role is 'PATIENT' | 'PAYER'
  activeRole: 'PATIENT', // 'PATIENT' | 'PAYER_ADMIN'
  activeRoute: 'triage',
  patients: [],
  patientsLoaded: false,
  analytics: null,
  analyticsLoaded: false,
  currentEncounter: null,
  currentSessionId: null,
  selectedMemberId: null,
  urgentCareMap: {
    origin: null, facilities: [], selectedFacility: null, route: null,
    status: 'idle', error: null, leafletMap: null,
  },
  patientProfile: {
    name: 'Jane Doe',
    age: 42,
    zip: '90210',
    contact: 'jane.doe@example.com',
    insurance: 'Self-Pay / Uninsured',
    prefSetting: 'Virtual Telehealth First',
    commPref: 'Email Updates',
  },
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
 * AUTHENTICATION: login / register / logout / session bootstrap
 * The server is the sole source of truth for role; the frontend never lets
 * a caller pick a role for themselves.
 */
function showAuthScreen(mode = 'login') {
  $('#app-shell')?.classList.add('role-hidden');
  $('#auth-screen')?.classList.remove('role-hidden');
  setAuthMode(mode);
}

function showAppShell() {
  $('#auth-screen')?.classList.add('role-hidden');
  $('#app-shell')?.classList.remove('role-hidden');
  const emailLabel = $('#current-user-email');
  if (emailLabel) emailLabel.textContent = state.currentUser?.email || '';
}

function setAuthMode(mode) {
  const isLogin = mode === 'login';
  $('#login-form')?.classList.toggle('role-hidden', !isLogin);
  $('#register-form')?.classList.toggle('role-hidden', isLogin);
  const title = $('#auth-screen-title');
  if (title) title.textContent = isLogin ? 'Log In' : 'Create Account';
  const subtitle = $('#auth-screen-subtitle');
  if (subtitle) subtitle.textContent = isLogin
    ? 'Sign in to continue to Care Navigation Navigator.'
    : 'Register for a Patient or Payer account.';
  const toggleBtn = $('#auth-toggle-mode');
  if (toggleBtn) toggleBtn.textContent = isLogin ? 'New here? Create an account' : 'Already have an account? Log in';
  const errorBox = $('#auth-error');
  if (errorBox) { errorBox.textContent = ''; errorBox.classList.add('role-hidden'); }
}

function toggleAuthMode() {
  const isLoginVisible = !$('#login-form')?.classList.contains('role-hidden');
  setAuthMode(isLoginVisible ? 'register' : 'login');
}

function showAuthError(message) {
  const errorBox = $('#auth-error');
  if (!errorBox) return;
  errorBox.textContent = message;
  errorBox.classList.remove('role-hidden');
}

/**
 * Enter the authenticated workspace for the given /api/auth/me user,
 * mapping the backend's PATIENT/PAYER role onto the frontend's existing
 * PATIENT/PAYER_ADMIN internal role labels.
 */
async function enterAuthenticatedWorkspace(user) {
  state.currentUser = user;
  showAppShell();
  populateMemberSelectors();
  await setRole(user.role === 'PAYER' ? 'PAYER_ADMIN' : 'PATIENT');
}

async function checkSession() {
  try {
    const user = await request('/api/auth/me');
    await enterAuthenticatedWorkspace(user);
  } catch (error) {
    // Unauthenticated (401) is the expected state for a fresh visit — show
    // the login screen instead of an error.
    showAuthScreen('login');
  }
}

async function handleLogin(event) {
  event.preventDefault();
  const email = $('#login-email').value.trim();
  const password = $('#login-password').value;
  try {
    const user = await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    await enterAuthenticatedWorkspace(user);
  } catch (error) {
    showAuthError(error.message);
  }
}

async function handleRegister(event) {
  event.preventDefault();
  const email = $('#register-email').value.trim();
  const password = $('#register-password').value;
  try {
    // Public self-registration only ever creates a PATIENT account; the
    // backend rejects any other role outright (see backend/routes/auth.py).
    await request('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, role: 'PATIENT' }),
    });
    const user = await request('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    await enterAuthenticatedWorkspace(user);
  } catch (error) {
    showAuthError(error.message);
  }
}

async function handleLogout() {
  try {
    await request('/api/auth/logout', { method: 'POST' });
  } catch (error) {
    console.error('Logout error:', error);
  }
  // Clear cached payer-only/session data so it can never leak into whatever
  // session (or role) logs in next in this browser tab.
  state.currentUser = null;
  state.patients = [];
  state.patientsLoaded = false;
  state.analytics = null;
  state.analyticsLoaded = false;
  state.currentEncounter = null;
  state.currentSessionId = null;
  state.selectedMemberId = null;
  showAuthScreen('login');
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
        <span>Find Care Near You</span>
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
  } else if (role === 'PAYER_ADMIN') {
    if (sectionTitle) sectionTitle.textContent = 'Population Management';
    container.innerHTML = `
      <button data-route="dashboard" class="nav-item ${state.activeRoute === 'dashboard' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
        <span>Population Overview</span>
      </button>
      <button data-route="cohort" class="nav-item ${state.activeRoute === 'cohort' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <span>Member Population & Risk</span>
      </button>
      <button data-route="history" class="nav-item ${state.activeRoute === 'history' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span>Population Audit Trail</span>
      </button>
    `;
  }
}

/**
 * ROLE SWITCHER STATE MANAGER
 * Switches between the Patient / Member experience and the Payer experience.
 */
async function setRole(role) {
  state.activeRole = role;

  const workspaceTitle = $('#workspace-title');

  if (role === 'PATIENT') {
    if (workspaceTitle) workspaceTitle.textContent = 'Patient Portal';

    if ($('#history-title')) $('#history-title').textContent = 'My Care History';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Review your symptom assessments and recorded care navigation choices.';

    // Hide Payer-only sections; the patient experience never surfaces population data
    $$('.payer-only').forEach(el => el.classList.add('role-hidden'));
  } else if (role === 'PAYER_ADMIN') {
    if (workspaceTitle) workspaceTitle.textContent = 'Payer Analytics Workspace';

    if ($('#history-title')) $('#history-title').textContent = 'Population Audit Trail';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Database audit trail of care navigation decisions across members.';

    $$('.payer-only').forEach(el => el.classList.remove('role-hidden'));

    // Population data and analytics are only fetched once the Payer
    // experience is actually opened, and only for an authenticated PAYER —
    // never for a PATIENT session.
    if (!state.patientsLoaded) {
      try {
        state.patients = (await request('/api/patients')) || [];
        state.patientsLoaded = true;
        populateMemberSelectors();
      } catch (error) {
        console.error('Failed to load population data:', error);
      }
    }
    if (!state.analyticsLoaded) {
      try {
        state.analytics = (await request('/api/analytics')) || null;
        state.analyticsLoaded = true;
      } catch (error) {
        console.error('Failed to load analytics:', error);
      }
    }
  }

  // Render Sidebar for Active Role
  renderSidebarNav(role);

  // Keep each role on routes that exist in its own navigation
  if (role === 'PATIENT' && ['dashboard', 'cohort'].includes(state.activeRoute)) {
    route('triage');
  } else if (role === 'PAYER_ADMIN' && ['patient-profile', 'triage', 'providers'].includes(state.activeRoute)) {
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
  if (name === 'history') {
    if (state.activeRole === 'PATIENT') {
      loadPatientCareHistory();
    } else if (state.selectedMemberId) {
      loadPatientHistory(state.selectedMemberId);
    }
  }
  if (name === 'urgent-care-map') renderUrgentCareMap();
}

const formatDistance = meters => Number.isFinite(meters)
  ? (meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`)
  : 'Distance unavailable';
const formatDuration = seconds => Number.isFinite(seconds)
  ? `${Math.max(1, Math.round(seconds / 60))} min drive`
  : 'Travel time unavailable';

function urgentCareMapContent() {
  const mapState = state.urgentCareMap;
  if (mapState.status === 'requesting-location') return '<div class="card urgent-care-message"><div class="loading-spinner-wrap"><div class="spinner"></div><p>Requesting your location to find nearby urgent care…</p></div></div>';
  if (mapState.status === 'location-denied') return '<div class="card urgent-care-message"><h1>Urgent Care Navigation</h1><div class="notice"><strong>Location access is needed to find nearby urgent care.</strong><p>Please allow location access, then try again.</p><button class="button primary" data-urgent-care-action="retry-location">Use My Location</button></div></div>';
  if (mapState.status === 'error') return `<div class="card urgent-care-message"><h1>Urgent Care Navigation</h1><div class="notice emergency"><strong>Unable to load urgent care options.</strong><p>${escapeHtml(mapState.error || 'Please try again.')}</p><button class="button secondary" data-urgent-care-action="retry-location">Try Again</button></div></div>`;
  if (!mapState.origin) return '<div class="card urgent-care-message"><h1>Urgent Care Navigation</h1><p class="subtitle">Find nearby urgent care based on your location.</p><button class="button primary" data-urgent-care-action="retry-location">Use My Location</button></div>';
  const cards = mapState.facilities.length ? mapState.facilities.map(facility => {
    const selected = mapState.selectedFacility?.id === facility.id;
    const details = selected && mapState.route ? `<strong>${formatDistance(mapState.route.distanceMeters)}</strong> · ${formatDuration(mapState.route.durationSeconds)}` : `${formatDistance(facility.distanceMeters)} away (straight-line)`;
    const rating = Number.isFinite(facility.rating) ? '<span title="Ratings should be verified with the provider">⭐⭐⭐⭐</span>' : '';
    const phone = facility.phone ? `<span>Phone: ${escapeHtml(facility.phone)}</span>` : '';
    const specialties = Array.isArray(facility.specialties) && facility.specialties.length ? `<span>Specialties: ${escapeHtml(facility.specialties.join(', '))}</span>` : '';
    return `<button class="urgent-care-facility-card ${selected ? 'selected' : ''}" data-urgent-care-action="select-facility" data-facility-id="${escapeHtml(facility.id)}"><span class="facility-card-title">${escapeHtml(facility.name)}</span><span>${facility.type === 'hospital' ? 'Hospital' : 'Urgent care'}</span><span>${escapeHtml(facility.address || 'Address unavailable')}</span>${rating}${phone}${specialties}<span>${details}</span><span class="facility-availability">${facility.openingHours ? `Hours: ${escapeHtml(facility.openingHours)}` : 'Hours unavailable'}</span></button>`;
  }).join('') : mapState.error
    ? `<div class="notice"><strong>Facility search is temporarily unavailable.</strong><p>${escapeHtml(mapState.error)}</p><button class="button secondary" data-urgent-care-action="retry-location">Try Again</button></div>`
    : '<div class="empty-state compact"><h3>No nearby care options found</h3><p>No urgent-care facilities or hospitals were found in available OpenStreetMap data.</p></div>';
  const selected = mapState.selectedFacility;
  const selection = selected ? `<div class="card selected-urgent-care"><p class="eyebrow">Selected urgent care</p><h2>${escapeHtml(selected.name)}</h2><p>${escapeHtml(selected.address || 'Address unavailable')}</p>${mapState.status === 'loading-route' ? '<p class="muted">Calculating your driving route…</p>' : ''}${mapState.error ? `<div class="notice emergency">${escapeHtml(mapState.error)}</div>` : ''}${mapState.route ? `<p><strong>Distance:</strong> ${formatDistance(mapState.route.distanceMeters)}<br /><strong>Estimated travel time:</strong> ${formatDuration(mapState.route.durationSeconds)}</p><a class="button primary" target="_blank" rel="noopener" href="https://www.openstreetmap.org/directions?engine=fossgis_osrm_car&route=${mapState.origin.latitude}%2C${mapState.origin.longitude}%3B${selected.latitude}%2C${selected.longitude}">Open Directions</a>` : ''}</div>` : '<div class="card selected-urgent-care"><p class="muted">Select an urgent-care facility to view a driving route and travel time.</p></div>';
  return `<div class="page-header"><div><p class="eyebrow">Urgent Care</p><h1>Find nearby urgent care</h1><p class="subtitle">Location-based options for your existing urgent-care recommendation.</p></div><button class="button secondary" data-urgent-care-action="retry-location">Update Location</button></div><div class="urgent-care-layout"><div class="urgent-care-map-panel"><div id="urgent-care-leaflet-map" aria-label="Urgent care map"></div><p class="map-attribution-note">Map data © OpenStreetMap contributors</p></div><div class="urgent-care-list-panel"><h2>Nearby care options</h2>${mapState.status === 'loading-facilities' ? '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Finding nearby care…</p></div>' : cards}</div></div>${selection}`;
}

function destroyUrgentCareMap() {
  if (state.urgentCareMap.leafletMap) { state.urgentCareMap.leafletMap.remove(); state.urgentCareMap.leafletMap = null; }
}

function renderUrgentCareMap() {
  const container = $('#urgent-care-map-content');
  if (!container) return;
  destroyUrgentCareMap(); container.innerHTML = urgentCareMapContent();
  const element = $('#urgent-care-leaflet-map'); const origin = state.urgentCareMap.origin;
  if (!element || !window.L || !origin) return;
  const map = window.L.map(element).setView([origin.latitude, origin.longitude], 13);
  state.urgentCareMap.leafletMap = map;
  window.L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);
  const patient = window.L.circleMarker([origin.latitude, origin.longitude], { radius: 9, color: '#fff', weight: 3, fillColor: '#167f8a', fillOpacity: 1 }).addTo(map).bindPopup('Your location');
  const bounds = window.L.latLngBounds([patient.getLatLng()]);
  state.urgentCareMap.facilities.forEach(facility => {
    const selected = state.urgentCareMap.selectedFacility?.id === facility.id;
    const marker = window.L.circleMarker([facility.latitude, facility.longitude], { radius: selected ? 10 : 7, color: '#fff', weight: 2, fillColor: selected ? '#b45309' : '#123047', fillOpacity: 1 }).addTo(map).bindPopup(escapeHtml(facility.name));
    marker.on('click', () => selectUrgentCareFacility(facility.id)); bounds.extend(marker.getLatLng());
  });
  if (state.urgentCareMap.route?.geometry) bounds.extend(window.L.geoJSON(state.urgentCareMap.route.geometry, { style: { color: '#167f8a', weight: 5 } }).addTo(map).getBounds());
  if (bounds.isValid()) map.fitBounds(bounds.pad(0.18));
}

function requestBrowserLocation() {
  if (!navigator.geolocation) { state.urgentCareMap.status = 'location-denied'; renderUrgentCareMap(); return; }
  state.urgentCareMap.status = 'requesting-location'; renderUrgentCareMap();
  navigator.geolocation.getCurrentPosition(
    position => loadUrgentCareFacilities({ latitude: position.coords.latitude, longitude: position.coords.longitude }),
    () => { state.urgentCareMap.status = 'location-denied'; renderUrgentCareMap(); },
    { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 },
  );
}

function openUrgentCareMap() { if (state.currentEncounter?.recommendedAcuity === 'URGENT_CARE') { route('urgent-care-map'); requestBrowserLocation(); } }

async function loadUrgentCareFacilities(origin) {
  state.urgentCareMap = { ...state.urgentCareMap, origin, facilities: [], selectedFacility: null, route: null, status: 'loading-facilities', error: null }; renderUrgentCareMap();
  try {
    const result = await request(`/api/navigation/urgent-care/facilities?latitude=${encodeURIComponent(origin.latitude)}&longitude=${encodeURIComponent(origin.longitude)}&radiusMeters=5000`);
    state.urgentCareMap = { ...state.urgentCareMap, origin: result.origin, facilities: result.facilities || [], status: 'loaded' };
  } catch (error) { state.urgentCareMap = { ...state.urgentCareMap, status: 'loaded', error: error.message }; }
  renderUrgentCareMap();
}

async function selectUrgentCareFacility(facilityId) {
  const facility = state.urgentCareMap.facilities.find(item => item.id === facilityId); if (!facility || !state.urgentCareMap.origin) return;
  state.urgentCareMap = { ...state.urgentCareMap, selectedFacility: facility, route: null, status: 'loading-route', error: null }; renderUrgentCareMap();
  try {
    const routeData = await request('/api/navigation/urgent-care/route', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ origin: state.urgentCareMap.origin, destination: { latitude: facility.latitude, longitude: facility.longitude } }) });
    state.urgentCareMap = { ...state.urgentCareMap, route: routeData, status: 'loaded' };
  } catch (error) { state.urgentCareMap = { ...state.urgentCareMap, status: 'loaded', error: error.message }; }
  renderUrgentCareMap();
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

    // If the Payer experience selected a member ID, link it
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
 * Non-Emergency Care Recommendation UI Render
 */
function renderNonEmergencyResult(data, container) {
  // Show patient context only in the Payer experience
  const patientContextHtml = (data.patientContext && state.activeRole !== 'PATIENT')
    ? `<div class="patient-context-chip">
        <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        <span>Beneficiary: ${escapeHtml(data.patientContext.beneficiaryId)} &bull; ${riskBadge(data.patientContext.riskLevel)}</span>
       </div>`
    : '';

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

      <div class="safety-check-row">
        <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        <span>Safety screening complete — no emergency warning signs detected.</span>
      </div>

      <div class="rationale-box">
        <strong>Clinical & Navigation Rationale:</strong><br />
        ${escapeHtml(data.clinicalRationale)}
      </div>

      <div class="notice">
        ${escapeHtml(data.safetyDisclaimer)}
      </div>

      <div class="next-step-card">
        <h3 class="eyebrow">Next Step</h3>
        <p>Based on this assessment, <strong>${escapeHtml(data.recommendedSettingName)}</strong> is the appropriate care setting. Use Find Care Near You to locate options once location-based discovery is available, or contact your primary care provider directly.</p>
        ${data.recommendedAcuity === 'URGENT_CARE' ? '<button class="button primary" data-urgent-care-action="open-map">Find Nearby Urgent Care</button>' : ''}
        <button class="button secondary" data-route="providers">Find Care Near You</button>
      </div>
    </div>
  `;
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
 * VIEW 3: PATIENT HISTORY TIMELINE (For the Payer Role)
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
 * VIEW 5: POPULATION OVERVIEW DASHBOARD
 */
function renderDashboard() {
  const a = state.analytics;
  if (!a) return;

  $('#stat-grid').innerHTML = [
    ['Total Members', formatNumber(a.totalPatients), 'PostgreSQL Member Population'],
    ['Total ED Visits', formatNumber(a.totalEdVisits), '12-Month Claims Ingestion'],
    ['Total ED Spend', formatMoney(a.totalEdSpend), '12-Month Emergency Department Spend'],
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
    $('#time-pattern').innerHTML = `
      <div class="empty-state" style="padding: 24px 12px;">
        <div class="empty-icon" style="width: 48px; height: 48px;">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        </div>
        <h3 style="font-size: 15px;">Time-of-day signal pending</h3>
        <p>This view requires claims-level visit timestamps not yet available in the ingested dataset.</p>
      </div>
    `;
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
 * MEMBER PROFILE DRAWER (Payer Only)
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
        <button id="btn-drawer-history" class="button primary full-width">
          View Member Audit Log
        </button>
      </div>
    `;

    $('#btn-drawer-history')?.addEventListener('click', () => {
      closeMember();
      state.selectedMemberId = patient.id;
      $('#history-patient-select').value = patient.id;
      route('history');
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
  // Authentication listeners
  $('#login-form')?.addEventListener('submit', handleLogin);
  $('#register-form')?.addEventListener('submit', handleRegister);
  $('#auth-toggle-mode')?.addEventListener('click', toggleAuthMode);
  $('#logout-btn')?.addEventListener('click', handleLogout);

  // Navigation event delegation
  document.addEventListener('click', event => {
    const routeBtn = event.target.closest('[data-route]');
    if (routeBtn) {
      event.preventDefault();
      route(routeBtn.dataset.route);
    }

    const urgentCareAction = event.target.closest('[data-urgent-care-action]');
    if (urgentCareAction) {
      event.preventDefault();
      if (urgentCareAction.dataset.urgentCareAction === 'open-map') openUrgentCareMap();
      if (urgentCareAction.dataset.urgentCareAction === 'retry-location') requestBrowserLocation();
      if (urgentCareAction.dataset.urgentCareAction === 'select-facility') selectUrgentCareFacility(urgentCareAction.dataset.facilityId);
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

  $('#triage-form')?.addEventListener('submit', submitTriage);
  $('#patient-self-profile-form')?.addEventListener('submit', saveSelfProfile);

  $('#load-history-btn')?.addEventListener('click', () => {
    const pId = $('#history-patient-select').value;
    loadPatientHistory(pId);
  });
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

    bindEvents();

    // Session bootstrap: GET /api/auth/me determines whether to show the
    // login/register screen or the workspace for the authenticated role.
    // Population analytics and the member cohort are fetched lazily, only
    // once an authenticated PAYER opens that experience (see setRole), so
    // they are never pulled into memory during a Patient session.
    await checkSession();
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
