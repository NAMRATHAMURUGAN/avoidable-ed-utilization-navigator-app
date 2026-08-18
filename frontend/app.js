/**
 * Care Navigation Navigator — Frontend Application Logic
 * Two-role architecture: Patient / Member, and Payer.
 * Vanilla ES Module SPA driver serving Flask endpoints.
 */

const state = {
  currentUser: null, // { id, email, role } from GET /api/auth/me; role is 'PATIENT' | 'PAYER'
  activeRole: 'PATIENT', // 'PATIENT' | 'PAYER_ADMIN'
  activeRoute: 'triage',
  // Intentionally never populated with the full ~8,671-member cohort (see
  // renderCohort()'s module note) -- stays empty so member-linking
  // dropdowns (populateMemberSelectors) show only their default option
  // until a proper lightweight/typeahead member-lookup exists.
  patients: [],
  analytics: null,
  analyticsLoaded: false,
  rightpathAnalytics: null,
  rightpathAnalyticsLoaded: false,
  mlAnomalySummary: null,
  mlAnomalySummaryLoaded: false,
  interventions: [],
  interventionsLoaded: false,
  currentEncounter: null,
  currentSessionId: null,
  selectedMemberId: null,
  urgentCareMap: {
    origin: null, facilities: [], selectedFacility: null, route: null,
    status: 'idle', error: null, leafletMap: null,
  },
  // UI context only, set by the portal-selection screen -- NEVER used to
  // grant authorization. The authenticated backend role returned by
  // /api/auth/* remains the sole source of truth for routing (see
  // enterAuthenticatedWorkspace).
  selectedPortal: null,
  charts: {}, // Chart.js instances keyed by canvas id, so a revisited view destroys and recreates cleanly.
  cohortFilters: { search: '', risk: '', band: '', anomaly: '' },
  cohortPage: 1,
};

// RightPath brand palette (matches frontend/styles.css :root custom properties)
// so Chart.js visuals stay on-brand without a build step.
const BRAND_COLORS = {
  deep: '#123047',
  teal: '#087c75',
  tealLight: '#4db6ac',
  coral: '#d65b4f',
  amber: '#bd7a11',
  green: '#0b6b62',
  muted: '#5a6e7f',
  line: '#dce6eb',
};

/**
 * Destroy any existing Chart.js instance bound to ``canvasId`` and create a
 * new one from ``config``. Mirrors the destroy-before-recreate pattern
 * already used for the Leaflet urgent-care map (destroyUrgentCareMap).
 */
function renderChartJs(canvasId, config) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || typeof window.Chart === 'undefined') return null;
  if (state.charts[canvasId]) {
    state.charts[canvasId].destroy();
    delete state.charts[canvasId];
  }
  const chart = new window.Chart(canvas, config);
  state.charts[canvasId] = chart;
  return chart;
}

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
function showPortalSelect() {
  $('#auth-screen')?.classList.add('role-hidden');
  $('#app-shell')?.classList.add('role-hidden');
  $('#portal-select-screen')?.classList.remove('role-hidden');
}

function showAuthScreen(mode = 'login') {
  $('#portal-select-screen')?.classList.add('role-hidden');
  $('#app-shell')?.classList.add('role-hidden');
  $('#auth-screen')?.classList.remove('role-hidden');

  // The Payer / Healthcare Manager portal selection is UI context only: it
  // never grants a role. It does, however, hide the self-registration
  // affordance in this context, since public PAYER self-registration is not
  // offered by the backend (POST /api/auth/register only ever creates a
  // PATIENT account) -- offering a "Create Account" flow here would be a
  // misleading fake Payer registration path.
  const isPayerContext = state.selectedPortal === 'PAYER';
  $('#auth-toggle-row')?.classList.toggle('role-hidden', isPayerContext);
  $('#payer-registration-note')?.classList.toggle('role-hidden', !isPayerContext);
  setAuthMode(isPayerContext ? 'login' : mode);
}

function selectPortal(portal) {
  state.selectedPortal = portal;
  showAuthScreen('login');
}

function showAppShell() {
  $('#portal-select-screen')?.classList.add('role-hidden');
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
    // the portal-selection landing screen instead of an error.
    showPortalSelect();
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
  state.analytics = null;
  state.analyticsLoaded = false;
  state.rightpathAnalytics = null;
  state.rightpathAnalyticsLoaded = false;
  state.mlAnomalySummary = null;
  state.mlAnomalySummaryLoaded = false;
  state.interventions = [];
  state.interventionsLoaded = false;
  state.currentEncounter = null;
  state.currentSessionId = null;
  state.selectedMemberId = null;
  state.selectedPortal = null;
  state.cohortFilters = { search: '', risk: '', band: '', anomaly: '' };
  state.cohortPage = 1;
  showPortalSelect();
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
      <button data-route="urgent-care-map" class="nav-item ${state.activeRoute === 'urgent-care-map' ? 'active' : ''}">
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
    if (sectionTitle) sectionTitle.textContent = 'Payer Intelligence';
    container.innerHTML = `
      <button data-route="dashboard" class="nav-item ${state.activeRoute === 'dashboard' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>
        <span>Command Center</span>
      </button>
      <button data-route="cohort" class="nav-item ${state.activeRoute === 'cohort' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
        <span>Priority Members</span>
      </button>
      <button data-route="utilization-insights" class="nav-item ${state.activeRoute === 'utilization-insights' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/></svg>
        <span>Utilization Insights</span>
      </button>
      <button data-route="interventions" class="nav-item ${state.activeRoute === 'interventions' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        <span>Interventions</span>
      </button>
      <button data-route="cost-reduction" class="nav-item ${state.activeRoute === 'cost-reduction' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
        <span>Cost &amp; Outcomes</span>
      </button>
      <div class="sidebar-section-title" style="margin-top: 10px; padding-top: 14px; border-top: 1px solid var(--line);">More</div>
      <button data-route="assistant" class="nav-item ${state.activeRoute === 'assistant' ? 'active' : ''}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M9 13v2M15 13v2"/></svg>
        <span>Payer Intelligence</span>
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
  const portalBanner = $('#portal-banner');
  const portalBannerIcon = $('#portal-banner-icon');
  const portalBannerKicker = $('#portal-banner-kicker');
  const portalBannerTagline = $('#portal-banner-tagline');

  if (role === 'PATIENT') {
    if (workspaceTitle) workspaceTitle.textContent = 'Patient Portal';
    if (portalBanner) portalBanner.dataset.role = 'PATIENT';
    if (portalBannerKicker) portalBannerKicker.textContent = 'Patient Portal';
    if (portalBannerTagline) portalBannerTagline.textContent = 'Personalized Care Navigation';
    if (portalBannerIcon) portalBannerIcon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>';

    if ($('#history-title')) $('#history-title').textContent = 'My Care History';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Review your symptom assessments and recorded care navigation choices.';

    // Hide Payer-only sections; the patient experience never surfaces population data
    $$('.payer-only').forEach(el => el.classList.add('role-hidden'));
  } else if (role === 'PAYER_ADMIN') {
    if (workspaceTitle) workspaceTitle.textContent = 'Payer Analytics Workspace';
    if (portalBanner) portalBanner.dataset.role = 'PAYER_ADMIN';
    if (portalBannerKicker) portalBannerKicker.textContent = 'Payer Portal';
    if (portalBannerTagline) portalBannerTagline.textContent = 'Population & Utilization Intelligence';
    if (portalBannerIcon) portalBannerIcon.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>';

    if ($('#history-title')) $('#history-title').textContent = 'Population Audit Trail';
    if ($('#history-subtitle')) $('#history-subtitle').textContent = 'Database audit trail of care navigation decisions across members.';

    $$('.payer-only').forEach(el => el.classList.remove('role-hidden'));

    // Population-level analytics are fetched once the Payer experience is
    // actually opened, and only for an authenticated PAYER -- never for a
    // PATIENT session. The full unpaginated member cohort (GET /api/patients
    // with no page/pageSize) is intentionally NOT fetched here: at the
    // current ~8,671-member population it produces a ~7MB response that the
    // local Werkzeug dev server was observed to reset the connection on
    // (Windows). Every view that needs cohort data below now requests only
    // the page/filtered slice it actually displays.
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
  if (role === 'PATIENT' && ['dashboard', 'cohort', 'utilization-insights', 'interventions', 'cost-reduction', 'assistant'].includes(state.activeRoute)) {
    route('triage');
  } else if (role === 'PAYER_ADMIN' && ['patient-profile', 'triage', 'urgent-care-map'].includes(state.activeRoute)) {
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
  if (name === 'cohort') applyCohortFilters();
  if (name === 'utilization-insights') renderUtilizationInsights();
  if (name === 'interventions') renderInterventions();
  if (name === 'cost-reduction') renderCostReduction();
  if (name === 'patient-profile') loadPatientProfile();
  if (name === 'history') {
    if (state.activeRole === 'PATIENT') {
      loadPatientCareHistory();
    } else if (state.selectedMemberId) {
      loadPatientHistory(state.selectedMemberId);
    }
  }
  if (name === 'urgent-care-map') {
    // Reuse the existing urgent-care map implementation as-is. Auto-request
    // location only on first entry (no origin yet) so revisiting the route
    // after it's already loaded doesn't re-prompt for permission; the
    // triage-result "Find Nearby Urgent Care" flow (openUrgentCareMap)
    // still explicitly requests a fresh location every time, unchanged.
    if (!state.urgentCareMap.origin) requestBrowserLocation();
    else renderUrgentCareMap();
  }
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
  if (mapState.status === 'insecure-context') return '<div class="card urgent-care-message"><h1>Urgent Care Navigation</h1><div class="notice emergency"><strong>Location access is unavailable on this connection.</strong><p>Browsers only allow location access over a secure (HTTPS) connection, or on localhost. Open this app via <code>http://localhost:5000</code> (or over HTTPS) to use "Find Care Near You."</p></div></div>';
  if (mapState.status === 'location-denied') return '<div class="card urgent-care-message"><h1>Urgent Care Navigation</h1><div class="notice"><strong>Location access is needed to find nearby urgent care.</strong><p>Please allow location access in your browser, then try again.</p><button class="button primary" data-urgent-care-action="retry-location">Use My Location</button></div></div>';
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
  // Browsers only expose navigator.geolocation on a secure context (HTTPS,
  // or localhost/127.0.0.1) -- on a plain-HTTP LAN address the permission
  // prompt never appears and getCurrentPosition fails immediately. Detecting
  // this up front avoids telling the user to "allow location access" when
  // no such prompt will ever be offered.
  if (window.isSecureContext === false) { state.urgentCareMap.status = 'insecure-context'; renderUrgentCareMap(); return; }
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

  // Real pathway selection: the patient picked a specific live facility.
  // Persisted regardless of whether the driving-route calculation above
  // succeeded -- the selection itself is the navigation action, independent
  // of ORS availability. OSM facility ids are not backend Provider rows
  // (they're live Overpass results, never seeded into ProviderRepository),
  // so selectedProviderId is intentionally omitted -- sending one would 404
  // against a real provider lookup; the facility identity instead lives in
  // actionDetails, which is never validated against ProviderRepository.
  persistNavigationAction({
    actionType: 'PROVIDER_SELECTED',
    selectedAcuity: 'URGENT_CARE',
    actionDetails: {
      facilityName: facility.name,
      facilityAddress: facility.address || null,
      facilitySourceId: facility.id,
      source: 'openstreetmap',
    },
  });
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
        <button class="button secondary flex-center" data-urgent-care-action="find-emergency">
          <span>Find Emergency Department</span>
        </button>
      </div>

      <div class="emergency-notice-box">
        <strong>Authoritative Safety Rule:</strong> ${escapeHtml(data.clinicalRationale)}<br /><br />
        <em>Note: Non-emergency provider options, telehealth bookings, and cost-saving comparisons are strictly disabled for emergency triage results.</em>
      </div>
    </div>
  `;
}

/**
 * Map a backend recommendedAcuity to a short, human-readable pathway
 * headline. This is a presentation-only mapping -- the acuity value itself
 * is entirely backend-authoritative; nothing here invents a clinical
 * classification.
 */
const PATHWAY_HEADLINES = {
  URGENT_CARE: 'Urgent care is recommended.',
  TELEHEALTH: 'Virtual care may be appropriate.',
  PRIMARY_CARE: 'Primary care follow-up is recommended.',
};

/**
 * Persist a real patient navigation/pathway-selection action against the
 * active triage encounter (POST /api/navigation/action). No-ops -- never
 * calls the backend -- when there is no active encounter to attach the
 * action to, exactly matching the guard the existing telehealth-booking
 * flow already relied on (anonymous/pre-triage browsing never records a
 * stray action). Returns the parsed response, or null if nothing was
 * recorded (no active encounter) or the request failed.
 */
async function persistNavigationAction({ actionType, selectedAcuity, selectedProviderId, actionDetails }) {
  if (!state.currentEncounter?.encounterId) return null;
  try {
    return await request('/api/navigation/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        encounterId: state.currentEncounter.encounterId,
        actionType,
        selectedAcuity,
        ...(selectedProviderId ? { selectedProviderId } : {}),
        ...(actionDetails ? { actionDetails } : {}),
      }),
    });
  } catch (error) {
    console.error('Failed to record navigation action:', error);
    return null;
  }
}

/**
 * Render a list of real providers (from POST /api/triage's own
 * suitableProviders, or a live GET /api/providers call), or an honest
 * "not currently available" state -- never a fabricated provider. Each
 * provider is a real ProviderRepository row, so its id can safely be sent
 * as navigation_actions.selected_provider_id (validated backend-side).
 */
function renderProviderOptionsOrEmptyState(providers, acuity) {
  if (!providers || providers.length === 0) {
    return '<div class="notice">Primary care navigation is available through the provider directory.</div>';
  }
  return `
    <div class="rec-providers-list">
      ${providers.map(p => `
        <div class="provider-option-item recommended-option">
          <div class="provider-option-info">
            <h4>${escapeHtml(p.name)}</h4>
            <p>${escapeHtml(p.address || 'Address unavailable')}${p.isDemo ? ' <span class="demo-tag">Demo</span>' : ''}</p>
          </div>
          <div class="provider-option-actions">
            ${p.phone ? `<a href="tel:${escapeHtml(p.phone)}" class="button secondary">Call</a>` : ''}
            <button type="button" class="button primary" data-select-provider-id="${escapeHtml(p.id)}" data-select-provider-acuity="${escapeHtml(acuity)}" data-select-provider-name="${escapeHtml(p.name)}">Select This Provider</button>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

/**
 * Records a real PROVIDER_SELECTED navigation action when a patient picks a
 * specific provider from a recommendation list (e.g. Primary Care). Mirrors
 * the existing telehealth-booking persistence, but for a lighter-weight
 * "selected this provider" action rather than a completed booking.
 */
async function selectRecommendedProvider(button) {
  const providerId = button.dataset.selectProviderId;
  const acuity = button.dataset.selectProviderAcuity;
  const providerName = button.dataset.selectProviderName;
  button.disabled = true;
  button.textContent = 'Recording selection…';
  const result = await persistNavigationAction({
    actionType: 'PROVIDER_SELECTED',
    selectedAcuity: acuity,
    selectedProviderId: providerId,
    actionDetails: { providerName },
  });
  button.textContent = result ? 'Selected ✓' : 'Selection could not be recorded';
}

/**
 * Acuity-specific primary/secondary next-step actions. The backend's
 * recommendedAcuity remains authoritative: each branch only ever offers
 * actions appropriate to that specific pathway, never a generic list that
 * makes every care setting look equally appropriate.
 */
function renderPathwayNextStep(data) {
  const acuity = data.recommendedAcuity;

  if (acuity === 'URGENT_CARE') {
    return `
      <p><strong>${escapeHtml(data.recommendedSettingName)}</strong> is the recommended care setting based on your symptoms.</p>
      <button class="button primary" data-urgent-care-action="open-map">Find Nearby Urgent Care</button>
      <button class="button ghost" data-pathway-action="show-primary-care" style="margin-top: 8px;">Consider Primary Care Follow-Up</button>
      <div id="primary-care-options"></div>
    `;
  }

  if (acuity === 'TELEHEALTH') {
    return `
      <p><strong>${escapeHtml(data.recommendedSettingName)}</strong> can safely deliver care for this complaint.</p>
      <button class="button primary" data-pathway-action="start-telehealth">Start Virtual Consultation</button>
      <button class="button ghost" data-pathway-action="show-primary-care" style="margin-top: 8px;">Consider Primary Care Follow-Up</button>
      <div id="primary-care-options"></div>
    `;
  }

  if (acuity === 'PRIMARY_CARE') {
    return `
      <p><strong>${escapeHtml(data.recommendedSettingName)}</strong> is the recommended setting for ongoing management of this complaint.</p>
      <button class="button primary" data-pathway-action="show-primary-care">Find Primary Care</button>
      <button class="button ghost" data-pathway-action="start-telehealth" style="margin-top: 8px;">Consider Telehealth</button>
      <div id="primary-care-options"></div>
    `;
  }

  // Defensive fallback for any acuity outside the four pathways this UI
  // maps explicitly; should not occur given the current safety engine.
  // Deliberately does NOT route to the urgent-care map or any other
  // specific pathway's experience -- an acuity this UI doesn't recognize
  // must never be silently treated as if it were URGENT_CARE.
  return `
    <p>Based on this assessment, <strong>${escapeHtml(data.recommendedSettingName)}</strong> is the appropriate care setting. Contact your care team for next steps.</p>
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

  // Additional, informational care-coordination suggestions derived from
  // historical utilization/ML data (see CareNavigationService, backend).
  // Deliberately calm, non-diagnostic language -- this is a coordination
  // option, never a risk score, priority label, or clinical finding shown
  // to the patient. Only rendered in the Patient experience; the Payer
  // experience already sees the equivalent analytical detail elsewhere.
  const proactiveRecommendationHtml = (data.proactiveRecommendation && state.activeRole === 'PATIENT')
    ? `<div class="next-step-card">
        <h3 class="eyebrow">Additional Care Coordination Options</h3>
        <p>Based on your care history, these additional support options may help:</p>
        ${(data.proactiveRecommendation.recommendations || []).map(rec => `
          <p><strong>${escapeHtml(rec.recommendation)}</strong> — ${escapeHtml(rec.reason)}</p>
        `).join('')}
       </div>`
    : '';

  const headline = PATHWAY_HEADLINES[data.recommendedAcuity] || 'A care recommendation is available.';

  container.innerHTML = `
    <div class="recommendation-card">
      <div class="rec-badge-header">
        <div class="rec-title-wrap">
          <p class="eyebrow">RightPath Recommendation</p>
          <h2>${escapeHtml(headline)}</h2>
        </div>
        ${acuityBadge(data.recommendedAcuity)}
      </div>

      ${patientContextHtml}

      <div class="safety-check-row">
        <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
        <span>Safety screening complete — no emergency warning signs detected.</span>
      </div>

      <div class="rationale-box">
        <strong>Why this pathway:</strong><br />
        ${escapeHtml(data.clinicalRationale)}
      </div>

      <div class="notice">
        ${escapeHtml(data.safetyDisclaimer)}
      </div>

      <div class="next-step-card">
        <h3 class="eyebrow">Next Step</h3>
        ${renderPathwayNextStep(data)}
      </div>

      ${proactiveRecommendationHtml}

      ${state.activeRole === 'PATIENT' ? `
        <div class="ai-assistant-cta">
          <button type="button" class="button ghost" data-open-chat>
            <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 8V4H8"/><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M2 14h2M20 14h2M9 13v2M15 13v2"/></svg>
            <span>Need more guidance? Ask RightPath AI</span>
          </button>
        </div>
      ` : ''}
    </div>
  `;
}

/**
 * SIMULATED TELEHEALTH FLOW (demo only -- ElevenLabs integration is a
 * separate, later task). Reuses the real TELEHEALTH provider already
 * returned by POST /api/triage when available (or a live GET /api/providers
 * lookup as a fallback), and records a real, persisted NavigationAction
 * using the existing APPOINTMENT_BOOKED contract -- nothing here is a
 * fabricated clinician, and no real medical/payment data is collected.
 */
async function startSimulatedTelehealth() {
  const container = $('#triage-result');
  if (!container || !state.currentEncounter) return;

  let provider = (state.currentEncounter.suitableProviders || []).find(p => p.type === 'TELEHEALTH');
  if (!provider) {
    try {
      const providers = await request('/api/providers?type=TELEHEALTH');
      provider = providers[0] || null;
    } catch (error) {
      provider = null;
    }
  }

  container.innerHTML = `
    <div class="recommendation-card">
      <div class="telehealth-demo-card" style="text-align: center;">
        <span class="demo-banner-chip">DEMO / SIMULATED EXPERIENCE — No real clinician is connected.</span>
        <div class="loading-spinner-wrap" style="margin-top: 20px;"><div class="spinner"></div></div>
        <h4 style="margin-top: 16px;">Preparing your consultation…</h4>
        <p>Connecting you to a simulated virtual-care session.</p>
      </div>
    </div>
  `;

  setTimeout(() => renderSimulatedTelehealthReady(provider), 1800);
}

async function renderSimulatedTelehealthReady(provider) {
  const container = $('#triage-result');
  if (!container) return;

  container.innerHTML = `
    <div class="recommendation-card">
      <div class="telehealth-demo-card">
        <span class="demo-banner-chip">DEMO / SIMULATED EXPERIENCE — No real clinician is connected.</span>
        <h4 style="margin-top: 14px;">Your simulated consultation is ready</h4>
        ${provider ? `
          <div class="provider-option-item" style="margin-top: 10px;">
            <div class="provider-option-info">
              <h4>${escapeHtml(provider.name)}</h4>
              <p>${escapeHtml((provider.services || []).slice(0, 2).join(', ') || 'Virtual care')} <span class="demo-tag">Demo</span></p>
            </div>
          </div>
        ` : ''}
        <p style="margin-top: 12px;">This is a simulated demo screen. No real medical or payment information has been collected, and no real clinician has joined this session.</p>
        <button class="button secondary" data-pathway-action="return-to-recommendation" style="margin-top: 12px;">Return to Care Recommendation</button>
      </div>
    </div>
  `;

  await persistNavigationAction({
    actionType: 'APPOINTMENT_BOOKED',
    selectedAcuity: 'TELEHEALTH',
    selectedProviderId: provider?.id,
    actionDetails: {
      providerName: provider?.name || 'Simulated Telehealth Provider',
      appointmentTime: 'Now (simulated)',
      isSimulatedDemo: true,
    },
  });
}

async function showPrimaryCareOptions(triggerButton) {
  const container = $('#primary-care-options');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div></div>';
  try {
    const providers = await request('/api/providers?type=PRIMARY_CARE');
    container.innerHTML = renderProviderOptionsOrEmptyState(providers, 'PRIMARY_CARE');
  } catch (error) {
    container.innerHTML = `<div class="notice emergency">Unable to load primary care options: ${escapeHtml(error.message)}</div>`;
  }
  triggerButton?.remove();
}

function returnToCareRecommendation() {
  const container = $('#triage-result');
  if (container && state.currentEncounter) {
    renderNonEmergencyResult(state.currentEncounter, container);
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
 * Loaded from the authenticated caller's own persistent PostgreSQL history
 * (GET /api/navigation/my-history) rather than in-memory frontend state, so
 * it survives a browser refresh or a brand-new session on any device -- it
 * never depends on state.currentSessionId being present in JS memory.
 */
async function loadPatientCareHistory() {
  const container = $('#history-timeline-container');
  container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Loading your care history...</p></div>';

  try {
    const data = await request('/api/navigation/my-history');
    const encounters = data.encounters || [];

    if (encounters.length === 0) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          </div>
          <h3>No Care History Yet</h3>
          <p>Complete a symptom assessment on <strong>My Health</strong> to start building your care timeline.</p>
        </div>
      `;
      return;
    }

    // Backend already orders encounters most-recent-first; actions are
    // grouped under their own encounter, same pattern as the Payer history view.
    const encountersHtml = encounters.map(enc => {
      const actionsForEnc = (data.actions || []).filter(act => act.encounterId === enc.encounterId);
      const actionsHtml = actionsForEnc.map(act => `
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

      return `
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
      `;
    }).join('');

    container.innerHTML = `
      <div class="flex-between" style="margin-bottom: 20px;">
        <div>
          <h3>My Care Timeline</h3>
          <p class="muted">${data.totalEncounters} assessment${data.totalEncounters === 1 ? '' : 's'} on record</p>
        </div>
      </div>
      <div class="timeline-list">
        ${encountersHtml}
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
          <h3>No RightPath activity recorded for this member.</h3>
          <p>No triage encounters have been recorded yet for beneficiary ${escapeHtml(data.beneficiaryId || patientId)}. This means the member has no recorded patient-portal activity, not that the database is unavailable.</p>
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
async function renderDashboard() {
  const a = state.analytics;
  if (!a) return;

  // Derived from the same real GET /api/analytics priority matrix used
  // below (not a separate full-population fetch): High Risk = the two
  // "high risk" quadrants (with and without an anomaly) combined.
  const highRiskCount = Number.isFinite(a.priorityMatrix?.highRiskHighAnomaly) && Number.isFinite(a.priorityMatrix?.highRiskLowAnomaly)
    ? a.priorityMatrix.highRiskHighAnomaly + a.priorityMatrix.highRiskLowAnomaly
    : null;

  $('#stat-grid').innerHTML = [
    ['Total Members', formatNumber(a.totalPatients), 'PostgreSQL Member Population'],
    ['ED Utilization', formatNumber(a.totalEdVisits), '12-Month ED Visit Count'],
    ['ED Spend', formatMoney(a.totalEdSpend), '12-Month Emergency Department Spend'],
    Number.isFinite(highRiskCount)
      ? ['Members with Risk Signals', formatNumber(highRiskCount), 'Composite: high-utilization prediction or utilization anomaly -- model-based signal, not a clinical diagnosis']
      : ['Members with Risk Signals', 'N/A', 'Priority matrix data unavailable'],
    Number.isFinite(a.highUtilizationMemberCount)
      ? ['High-Utilization Members', formatNumber(a.highUtilizationMemberCount), 'XGBoost historical high-utilization prediction']
      : ['High-Utilization Members', 'N/A', 'XGBoost prediction data unavailable'],
    Number.isFinite(a.anomalousMemberCount)
      ? ['Anomalous Utilization', formatNumber(a.anomalousMemberCount), 'Isolation Forest unusual-utilization detection']
      : ['Anomalous Utilization', 'N/A', 'Anomaly-detection data unavailable'],
    Number.isFinite(a.priorityMatrix?.highRiskHighAnomaly)
      ? ['Priority Review Population', formatNumber(a.priorityMatrix.highRiskHighAnomaly), 'High risk AND anomalous utilization']
      : ['Priority Review Population', 'N/A', 'Priority matrix data unavailable'],
  ].map(([label, val, detail]) => `
    <div class="stat-card">
      <div class="stat-label">${label}</div>
      <div class="stat-value">${val}</div>
      <div class="stat-detail">${detail}</div>
    </div>
  `).join('');

  renderPriorityMatrix(a.priorityMatrix);

  // Priority queue: a small, dedicated server-paginated request (top 5 High
  // priority members) rather than fetching the entire ~8,671-member cohort
  // just to show 5 rows -- see renderCohort()'s module note for why.
  const priorityList = $('#priority-list');
  priorityList.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div></div>';
  try {
    const page = await request('/api/patients?risk=HIGH&page=1&pageSize=5');
    const highRiskMembers = page.items || [];
    priorityList.innerHTML = highRiskMembers.length
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
    $$('.btn-open-member', priorityList).forEach(btn => {
      btn.addEventListener('click', () => openMember(btn.dataset.memberId));
    });
  } catch (error) {
    priorityList.innerHTML = `<div class="notice emergency">Unable to load the prioritization queue: ${escapeHtml(error.message)}</div>`;
  }

  // RightPath Program: real, database-backed patient-app activity
  // (triage_encounters / navigation_actions), fetched separately from and
  // never merged into the CMS/member analytics above.
  if (!state.rightpathAnalyticsLoaded) {
    try {
      state.rightpathAnalytics = await request('/api/payer/analytics/rightpath');
      state.rightpathAnalyticsLoaded = true;
    } catch (error) {
      console.error('Failed to load RightPath analytics:', error);
    }
  }
  renderRightPathProgram(state.rightpathAnalytics);
}

/**
 * RIGHTPATH PROGRAM (Command Center)
 * Unified activity + potential-impact section, real database-backed
 * RightPath patient-app activity from GET /api/payer/analytics/rightpath
 * (aggregate counts only -- see analytics_service.get_rightpath_analytics).
 * Additive to, and kept separate from, the CMS/member population analytics
 * above. GROUP A (Activity) and GROUP B (Potential Impact) each show a
 * metric exactly once -- no field is duplicated across the two groups. No
 * causal "prevented" or "saved" claim is made anywhere in this section.
 */
function renderRightPathProgram(data) {
  const activityGrid = $('#rightpath-activity-stat-grid');
  const impactGrid = $('#rightpath-impact-stat-grid');
  if (!activityGrid || !impactGrid) return;

  if (!data) {
    activityGrid.innerHTML = '<div class="empty-state" style="padding: 20px 12px;"><p>Unable to load RightPath activity data.</p></div>';
    impactGrid.innerHTML = '';
    return;
  }

  // GROUP A -- ACTIVITY
  activityGrid.innerHTML = [
    ['RightPath Assessments', formatNumber(data.totalRightPathAssessments),
      `Triage assessments recorded, across ${formatNumber(data.totalRightPathUsers)} distinct authenticated patient${data.totalRightPathUsers === 1 ? '' : 's'}`],
    ['Emergency Assessments', formatNumber(data.emergencyAssessments), 'Flagged by the deterministic safety engine'],
    ['Non-Emergency Recommendations', formatNumber(data.nonEmergencyRecommendations), 'Assessments that recommended a non-emergency care setting'],
    ['Confirmed Non-ED Navigation', formatNumber(data.confirmedNonEdNavigationActions), 'Patients who actually selected or booked a non-ED pathway'],
  ].map(([label, val, detail]) => `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value">${escapeHtml(String(val))}</div>
      <div class="stat-detail">${escapeHtml(detail)}</div>
    </div>
  `).join('');

  // GROUP B -- POTENTIAL IMPACT
  const hasCostBaseline = Number.isFinite(data.averageEdClaimCost);
  const costOpportunityValue = hasCostBaseline ? formatMoney(data.potentialEdCostOpportunity) : 'Not yet estimable';
  const costOpportunityDetail = hasCostBaseline
    ? `Illustrative estimate using the observed average ED claim cost from the CMS population. Not measured savings.`
    : 'Requires at least one CMS-recorded ED visit to compute a baseline';

  impactGrid.innerHTML = [
    ['Potential ED Utilization Opportunity', formatNumber(data.potentialEdUtilizationOpportunities), 'Equal to confirmed non-ED navigation actions, not every recommendation'],
    ['Estimated Potential Cost Opportunity', costOpportunityValue, costOpportunityDetail],
  ].map(([label, val, detail]) => `
    <div class="stat-card">
      <div class="stat-label">${escapeHtml(label)}</div>
      <div class="stat-value">${escapeHtml(String(val))}</div>
      <div class="stat-detail">${escapeHtml(detail)}</div>
    </div>
  `).join('');

  renderRightPathAcuityChart(data.acuityDistribution);
  renderRightPathConfirmedPathwayChart(data);
  renderRightPathActivityTrendSection(data.activityTrend);
  renderRightPathImpactMethodology(data);
}

function renderRightPathAcuityChart(distribution) {
  const el = document.getElementById('chart-rightpath-acuity');
  if (!distribution || distribution.length === 0) {
    if (el) el.closest('.chart-canvas-wrap').innerHTML = '<p class="muted">No RightPath activity yet.</p>';
    return;
  }
  renderChartJs('chart-rightpath-acuity', {
    type: 'bar',
    data: {
      labels: distribution.map(row => (row.acuity || 'Unknown').replace(/_/g, ' ')),
      datasets: [{
        label: 'Assessments',
        data: distribution.map(row => row.count),
        backgroundColor: BRAND_COLORS.teal,
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

/**
 * Confirmed care-pathway distribution -- the ONE chart for what patients
 * actually navigated to (consolidates what were previously two separate,
 * duplicative pathway charts). Built from the four named navigation-count
 * fields rather than the raw pathwayDistribution array so it always shows
 * exactly the four recognized pathways (never an "Unknown" bucket).
 *
 * A bar chart with only one non-zero category is not analytically useful,
 * so with fewer than two non-zero pathways this renders a short compact
 * sentence instead of a near-empty chart.
 */
function renderRightPathConfirmedPathwayChart(data) {
  const wrap = $('#rightpath-pathway-chart-wrap');
  const segments = [
    ['Telehealth', data.telehealthNavigations || 0],
    ['Primary Care', data.primaryCareNavigations || 0],
    ['Urgent Care', data.urgentCareNavigations || 0],
    ['Emergency', data.emergencyNavigations || 0],
  ];
  const nonZero = segments.filter(([, count]) => count > 0);

  if (nonZero.length === 0) {
    if (wrap) wrap.innerHTML = '<p class="muted">No confirmed navigation activity yet.</p>';
    return;
  }

  if (nonZero.length < 2) {
    const [label, count] = nonZero[0];
    if (wrap) {
      wrap.innerHTML = `
        <div class="empty-state" style="padding: 20px 12px;">
          <p><strong>${count} ${escapeHtml(label)} navigation action${count === 1 ? '' : 's'} recorded.</strong></p>
          <p class="muted">Additional pathway distribution will appear as more confirmed navigation activity is recorded.</p>
        </div>
      `;
    }
    return;
  }

  if (!wrap?.querySelector('canvas')) {
    if (wrap) wrap.innerHTML = '<canvas id="chart-rightpath-pathway"></canvas>';
  }
  renderChartJs('chart-rightpath-pathway', {
    type: 'bar',
    data: {
      labels: segments.map(([label]) => label),
      datasets: [{
        label: 'Confirmed navigation actions',
        data: segments.map(([, count]) => count),
        backgroundColor: [BRAND_COLORS.teal, BRAND_COLORS.tealLight, BRAND_COLORS.amber, BRAND_COLORS.coral],
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

/**
 * A one-point time series is not a meaningful trend line. With exactly one
 * date, this renders a compact summary sentence instead ("RightPath
 * Activity" heading); with 2+ dates it renders the real trend line chart
 * ("RightPath Activity Trend" heading), unchanged. Never fabricates missing
 * dates or interpolates between the ones that exist.
 */
function renderRightPathActivityTrendSection(trend) {
  const eyebrowEl = $('#rightpath-trend-eyebrow');
  const headingEl = $('#rightpath-trend-heading');
  const descEl = $('#rightpath-trend-desc');
  const contentEl = $('#rightpath-trend-content');
  if (!contentEl) return;

  if (!trend || trend.length === 0) {
    if (eyebrowEl) eyebrowEl.textContent = 'Daily Volume';
    if (headingEl) headingEl.textContent = 'RightPath Activity Trend';
    if (descEl) descEl.textContent = 'Daily count of triage assessments recorded through RightPath.';
    contentEl.innerHTML = '<p class="muted">No RightPath activity yet.</p>';
    return;
  }

  if (trend.length === 1) {
    const [{ date, count }] = trend;
    const formattedDate = new Date(`${date}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    if (eyebrowEl) eyebrowEl.textContent = 'Daily Volume';
    if (headingEl) headingEl.textContent = 'RightPath Activity';
    if (descEl) descEl.textContent = 'A single day of recorded activity is not yet a meaningful trend.';
    contentEl.innerHTML = `
      <div class="empty-state" style="padding: 20px 12px;">
        <p><strong>${formatNumber(count)} assessment${count === 1 ? '' : 's'} recorded on ${formattedDate}.</strong></p>
        <p class="muted">A trend line will appear once activity has been recorded across multiple days.</p>
      </div>
    `;
    return;
  }

  if (eyebrowEl) eyebrowEl.textContent = 'Daily Volume';
  if (headingEl) headingEl.textContent = 'RightPath Activity Trend';
  if (descEl) descEl.textContent = 'Daily count of triage assessments recorded through RightPath.';
  if (!contentEl.querySelector('canvas')) {
    contentEl.innerHTML = '<canvas id="chart-rightpath-trend"></canvas>';
  }
  renderChartJs('chart-rightpath-trend', {
    type: 'line',
    data: {
      labels: trend.map(row => row.date),
      datasets: [{
        label: 'Assessments recorded',
        data: trend.map(row => row.count),
        borderColor: BRAND_COLORS.deep,
        backgroundColor: 'rgba(18, 48, 71, 0.12)',
        fill: true,
        tension: 0.25,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

function renderRightPathImpactMethodology(data) {
  const container = $('#rightpath-impact-methodology');
  if (!container) return;
  const baseline = data.costOpportunityMethodology?.baseline || 'CMS population average ED claim cost';
  container.innerHTML = `
    <div class="rationale-box">
      <strong>Confirmed non-ED navigation actions &times; CMS average ED claim cost = Estimated potential cost opportunity</strong>
      <p class="muted" style="margin: 6px 0 0 0;">Baseline: ${escapeHtml(baseline)}.</p>
    </div>
    <p class="muted caption-note">Illustrative population-level estimate. A navigation action is not proof that an ED visit was prevented, and this does not represent confirmed savings.</p>
  `;
}

/**
 * RISK x ANOMALY PRIORITY MATRIX (Command Center)
 * Real 2x2 cross-tab from GET /api/analytics (analytics_service.py), built
 * from the exact same per-member risk/anomaly signals already shown on the
 * Priority Members table -- never fabricated. Clicking a quadrant jumps to
 * the Priority Members table pre-filtered to that exact population.
 */
function renderPriorityMatrix(matrix) {
  const container = $('#priority-matrix');
  if (!container) return;
  if (!matrix) {
    container.innerHTML = '<p class="muted">Priority matrix data is unavailable.</p>';
    return;
  }
  const total = matrix.highRiskHighAnomaly + matrix.highRiskLowAnomaly + matrix.lowerRiskHighAnomaly + matrix.lowerRiskLowAnomaly;
  const pct = count => (total > 0 ? `${((count / total) * 100).toFixed(1)}%` : 'N/A');

  const quadrants = [
    {
      key: 'highRiskHighAnomaly', label: 'High Risk + High Anomaly', tone: 'priority-critical',
      detail: 'PRIORITY REVIEW', risk: 'HIGH', anomaly: 'ANOMALOUS',
    },
    {
      key: 'highRiskLowAnomaly', label: 'High Risk + Normal Utilization', tone: 'priority-watch',
      detail: 'MONITOR', risk: 'HIGH', anomaly: 'NORMAL',
    },
    {
      key: 'lowerRiskHighAnomaly', label: 'Lower Risk + High Anomaly', tone: 'priority-review',
      detail: 'REVIEW', risk: 'LOWER', anomaly: 'ANOMALOUS',
    },
    {
      key: 'lowerRiskLowAnomaly', label: 'Lower Risk + Normal Utilization', tone: 'priority-stable',
      detail: 'STABLE', risk: 'LOWER', anomaly: 'NORMAL',
    },
  ];

  container.innerHTML = quadrants.map(q => `
    <button type="button" class="priority-quadrant interactive ${q.tone}" data-matrix-quadrant data-risk="${q.risk}" data-anomaly="${q.anomaly}">
      <span class="quadrant-label">${escapeHtml(q.detail)} &bull; ${escapeHtml(q.label)}</span>
      <span class="quadrant-count">${formatNumber(matrix[q.key])}</span>
      <span class="quadrant-detail">${pct(matrix[q.key])} of ${formatNumber(total)} members with a computed risk/anomaly result</span>
    </button>
  `).join('');

  $$('[data-matrix-quadrant]', container).forEach(btn => {
    btn.addEventListener('click', () => {
      state.cohortFilters.risk = btn.dataset.risk === 'HIGH' ? 'HIGH' : '';
      state.cohortFilters.anomaly = btn.dataset.anomaly === 'ANOMALOUS' ? 'ANOMALOUS' : 'NORMAL';
      // "Lower risk" spans MODERATE+LOW, which the existing single risk
      // filter can't express as one value, so it's left at "All Priorities"
      // (empty) for the two lower-risk quadrants -- the anomaly filter
      // alone already narrows to the intended quadrant's population.
      route('cohort');
      syncCohortFilterControls();
    });
  });
}

/**
 * VIEW 6: MEMBER COHORT / PRIORITY MEMBER TABLE
 * Operational, authorized (PAYER-only) table with search, risk/utilization
 * -band/anomaly filters, and TRUE server-side pagination: each page/filter
 * change requests only the ~20 rows actually displayed from
 * GET /api/patients?page=&pageSize=&risk=&band=&anomaly=&search= rather
 * than fetching the entire ~8,671-member (~7MB) population up front. The
 * full unpaginated fetch was observed (via real browser testing) to
 * unreliably reset the connection on the local Windows dev server.
 */
const COHORT_PAGE_SIZE = 20;

function syncCohortFilterControls() {
  if ($('#member-search')) $('#member-search').value = state.cohortFilters.search;
  if ($('#risk-filter')) $('#risk-filter').value = state.cohortFilters.risk;
  if ($('#utilization-band-filter')) $('#utilization-band-filter').value = state.cohortFilters.band;
  if ($('#anomaly-filter')) $('#anomaly-filter').value = state.cohortFilters.anomaly;
}

function applyCohortFilters() {
  state.cohortPage = 1;
  renderCohort();
}

function filterCohort() {
  state.cohortFilters = {
    search: $('#member-search')?.value || '',
    risk: $('#risk-filter')?.value || '',
    band: $('#utilization-band-filter')?.value || '',
    anomaly: $('#anomaly-filter')?.value || '',
  };
  applyCohortFilters();
}

function resetCohortFilters() {
  state.cohortFilters = { search: '', risk: '', band: '', anomaly: '' };
  syncCohortFilterControls();
  applyCohortFilters();
}

async function renderCohort() {
  const container = $('#member-table');
  const paginationContainer = $('#member-table-pagination');
  if (!container) return;
  container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Loading member population...</p></div>';
  if (paginationContainer) paginationContainer.innerHTML = '';

  const { search, risk, band, anomaly } = state.cohortFilters;
  const params = new URLSearchParams({ page: String(state.cohortPage || 1), pageSize: String(COHORT_PAGE_SIZE) });
  if (search) params.set('search', search);
  if (risk) params.set('risk', risk);
  if (band) params.set('band', band);
  if (anomaly) params.set('anomaly', anomaly);

  let page;
  try {
    page = await request(`/api/patients?${params.toString()}`);
  } catch (error) {
    container.innerHTML = `<div class="notice emergency">Unable to load the member population: ${escapeHtml(error.message)}</div>`;
    return;
  }

  const patients = page.items || [];
  if (patients.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <h3>No Members Found</h3>
        <p>No beneficiaries match the current filter criteria.</p>
      </div>
    `;
    return;
  }

  state.cohortPage = page.page;
  const totalPages = page.totalPages || 1;
  const startIndex = (page.page - 1) * page.pageSize;

  container.innerHTML = `
    <table class="data-table">
      <thead>
        <tr>
          <th>Beneficiary Member</th>
          <th>Priority Level</th>
          <th>Anomaly Status</th>
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
            <td>${p.isAnomalous ? '<span class="badge high">Anomalous</span>' : '<span class="badge low">Normal</span>'}</td>
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

  if (paginationContainer) {
    paginationContainer.innerHTML = `
      <span>Showing ${startIndex + 1}-${Math.min(startIndex + page.pageSize, page.total)} of ${formatNumber(page.total)} members</span>
      <div class="pagination-controls">
        <button type="button" class="button ghost" id="cohort-prev-page" ${page.page <= 1 ? 'disabled' : ''}>Previous</button>
        <span>Page ${page.page} of ${totalPages}</span>
        <button type="button" class="button ghost" id="cohort-next-page" ${page.page >= totalPages ? 'disabled' : ''}>Next</button>
      </div>
    `;
    $('#cohort-prev-page')?.addEventListener('click', () => {
      state.cohortPage = page.page - 1;
      renderCohort();
    });
    $('#cohort-next-page')?.addEventListener('click', () => {
      state.cohortPage = page.page + 1;
      renderCohort();
    });
  }
}

/**
 * VIEW 7: UTILIZATION INSIGHTS (Payer Intelligence)
 * Reuses GET /api/ml/anomalies/summary as-is -- no new backend endpoint.
 */
async function renderUtilizationInsights() {
  const container = $('#utilization-insights-content');
  if (!container) return;
  const a = state.analytics;

  if (!state.mlAnomalySummaryLoaded) {
    container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div></div>';
    try {
      state.mlAnomalySummary = await request('/api/ml/anomalies/summary');
      state.mlAnomalySummaryLoaded = true;
    } catch (error) {
      container.innerHTML = `<div class="card"><div class="empty-state"><h3>Unable to load utilization insights</h3><p>${escapeHtml(error.message)}</p></div></div>`;
      return;
    }
  }

  const summary = state.mlAnomalySummary;
  if (!summary || !a) {
    container.innerHTML = '<div class="card"><div class="empty-state"><h3>No utilization-insight data available</h3><p>Run the anomaly-detection pipeline to generate this analysis.</p></div></div>';
    return;
  }

  const overlap = summary.xgboost_post_hoc_overlap_counts || {};
  const segments = [
    ['High Utilization + Anomaly', overlap.high_utilization_and_anomaly],
    ['High Utilization, No Anomaly', overlap.high_utilization_no_anomaly],
    ['Anomaly, No High Utilization', overlap.low_utilization_and_anomaly],
    ['Neither Signal', overlap.low_utilization_no_anomaly],
  ].filter(([, count]) => Number.isFinite(count));
  const maxCount = Math.max(...segments.map(([, count]) => count), 1);

  container.innerHTML = `
    <div class="stat-grid-container">
      <div class="stat-card">
        <div class="stat-label">Total Members Analyzed</div>
        <div class="stat-value">${formatNumber(summary.total_members)}</div>
        <div class="stat-detail">Isolation Forest utilization-anomaly model</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Flagged Anomalies</div>
        <div class="stat-value">${formatNumber(summary.number_of_anomalies)}</div>
        <div class="stat-detail">${Number.isFinite(summary.anomaly_percentage) ? summary.anomaly_percentage.toFixed(2) : '—'}% of the population</div>
      </div>
    </div>

    <div class="chart-grid">
      <div class="card">
        <div class="card-header">
          <p class="eyebrow">Population Distribution</p>
          <h2>ED Utilization Bands</h2>
          <p class="muted">How concentrated ED utilization is across the member population.</p>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart-utilization-distribution"></canvas></div>
      </div>
      <div class="card">
        <div class="card-header">
          <p class="eyebrow">Model-Based Utilization Risk</p>
          <h2>Risk Distribution</h2>
          <p class="muted">Categorical risk segmentation only -- this is a model-based utilization-risk signal, not a medical diagnosis.</p>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart-risk-distribution"></canvas></div>
      </div>
    </div>

    <div class="chart-grid single-column">
      <div class="card">
        <div class="card-header">
          <p class="eyebrow">Isolation Forest</p>
          <h2>Anomaly Analysis</h2>
          <p class="muted">Average anomaly score by ED-visit count, aggregated (never per-member) into anomalous vs. normal groups. Anomaly = unusual utilization pattern, not a clinical diagnosis.</p>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart-anomaly-scatter"></canvas></div>
      </div>
    </div>

    <div class="card">
      <div class="card-header">
        <p class="eyebrow">Combined Utilization Signals</p>
        <h2>High-Utilization Prediction &times; Anomaly Detection Overlap</h2>
        <p class="muted">Each member is classified by two independent models: XGBoost's historical high-utilization prediction and Isolation Forest's unsupervised anomaly detection. Combined utilization signals, not a medical diagnosis.</p>
      </div>
      <div class="bar-chart-container">
        ${segments.map(([label, count]) => `
          <div class="bar-row">
            <span>${escapeHtml(label)}</span>
            <div class="bar-track"><div class="bar-fill" style="width: ${(count / maxCount) * 100}%"></div></div>
            <strong>${formatNumber(count)}</strong>
          </div>
        `).join('')}
      </div>
      <p class="muted caption-note">An anomaly indicates an unusual utilization pattern. It is not evidence of medical necessity, clinical deterioration, inappropriate ED use, or ED avoidability.</p>
    </div>
  `;

  renderUtilizationDistributionChart(a.utilizationDistribution);
  renderRiskDistributionChart(a.riskDistribution);
  renderAnomalyScatterChart(a.anomalyScatterBins);
}

function renderUtilizationDistributionChart(distribution) {
  if (!distribution || distribution.length === 0) return;
  renderChartJs('chart-utilization-distribution', {
    type: 'bar',
    data: {
      labels: distribution.map(row => `${row.band} visits`),
      datasets: [{
        label: 'Members',
        data: distribution.map(row => row.memberCount),
        backgroundColor: BRAND_COLORS.teal,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { callbacks: {
        label: ctx => `${formatNumber(ctx.parsed.y)} members`,
      } } },
      scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

function renderRiskDistributionChart(distribution) {
  if (!distribution) return;
  const total = (distribution.high || 0) + (distribution.moderate || 0) + (distribution.low || 0);
  renderChartJs('chart-risk-distribution', {
    type: 'doughnut',
    data: {
      labels: ['High', 'Moderate', 'Low'],
      datasets: [{
        data: [distribution.high || 0, distribution.moderate || 0, distribution.low || 0],
        backgroundColor: [BRAND_COLORS.coral, BRAND_COLORS.amber, BRAND_COLORS.green],
        borderWidth: 2,
        borderColor: '#ffffff',
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom' },
        tooltip: { callbacks: {
          label: ctx => {
            const pct = total > 0 ? ((ctx.parsed / total) * 100).toFixed(1) : '0.0';
            return `${ctx.label}: ${formatNumber(ctx.parsed)} members (${pct}%)`;
          },
        } },
      },
    },
  });
}

function renderAnomalyScatterChart(bins) {
  if (!bins || bins.length === 0) {
    const el = document.getElementById('chart-anomaly-scatter');
    if (el) el.closest('.chart-canvas-wrap').innerHTML = '<p class="muted">Anomaly-detection data is unavailable.</p>';
    return;
  }
  const anomalousPoints = bins
    .filter(b => b.isAnomalous)
    .map(b => ({ x: b.edVisitCount, y: b.averageAnomalyScore, count: b.memberCount }));
  const normalPoints = bins
    .filter(b => !b.isAnomalous)
    .map(b => ({ x: b.edVisitCount, y: b.averageAnomalyScore, count: b.memberCount }));

  renderChartJs('chart-anomaly-scatter', {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Anomalous',
          data: anomalousPoints,
          backgroundColor: BRAND_COLORS.coral,
          pointRadius: ctx => Math.min(4 + Math.sqrt(ctx.raw?.count || 1), 16),
        },
        {
          label: 'Normal',
          data: normalPoints,
          backgroundColor: BRAND_COLORS.teal,
          pointRadius: ctx => Math.min(4 + Math.sqrt(ctx.raw?.count || 1), 16),
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'bottom' },
        tooltip: { callbacks: {
          label: ctx => `${ctx.dataset.label}: ${ctx.raw.count} member(s) at ${ctx.raw.x} ED visits, avg anomaly score ${ctx.raw.y.toFixed(3)}`,
        } },
      },
      scales: {
        x: { title: { display: true, text: 'ED Visit Count' } },
        y: { title: { display: true, text: 'Average Anomaly Score' } },
      },
    },
  });
}

/**
 * VIEW 8: INTERVENTIONS (Payer Intelligence)
 * Reuses the already-loaded population cohort (GET /api/patients) plus
 * GET /api/navigation/members/<id>/recommendations per prioritized member --
 * no new backend endpoint. Recommended actions are exactly what the backend
 * returns; nothing here is a fabricated campaign name.
 */
function careManagementOpportunityChartHtml(opportunities) {
  if (!opportunities || opportunities.length === 0) {
    return `
      <div class="card">
        <div class="card-header">
          <p class="eyebrow">Care-Management Opportunity</p>
          <h2>Recorded Care-Navigation Pathways</h2>
        </div>
        <div class="empty-state" style="padding: 28px 12px;">
          <p>Intervention data will appear as care-management actions are recorded.</p>
        </div>
      </div>
    `;
  }
  return `
    <div class="card">
      <div class="card-header">
        <p class="eyebrow">Care-Management Opportunity</p>
        <h2>Recorded Care-Navigation Pathways</h2>
        <p class="muted">Real counts of navigation actions actually recorded through RightPath (patient- and payer-initiated), by the pathway selected.</p>
      </div>
      <div class="chart-canvas-wrap tall"><canvas id="chart-care-management"></canvas></div>
    </div>
  `;
}

function renderCareManagementOpportunityChart(opportunities) {
  if (!opportunities || opportunities.length === 0) return;
  renderChartJs('chart-care-management', {
    type: 'bar',
    data: {
      labels: opportunities.map(row => row.pathway.replace('_', ' ')),
      datasets: [{
        label: 'Recorded actions',
        data: opportunities.map(row => row.actionCount),
        backgroundColor: BRAND_COLORS.teal,
        borderRadius: 4,
      }],
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { beginAtZero: true, ticks: { precision: 0 } } },
    },
  });
}

async function renderInterventions() {
  const container = $('#interventions-content');
  if (!container) return;

  if (!state.interventionsLoaded) {
    container.innerHTML = '<div class="loading-spinner-wrap"><div class="spinner"></div><p>Generating recommended actions...</p></div>';
    // A small, dedicated server-paginated request (top 10 High priority
    // members) -- see renderCohort()'s module note on why the full
    // ~8,671-member cohort is never fetched in one request.
    const highRiskPage = await request('/api/patients?risk=HIGH&page=1&pageSize=10').catch(() => ({ items: [] }));
    const highRisk = highRiskPage.items || [];
    const results = await Promise.all(highRisk.map(async patient => {
      try {
        const data = await request(`/api/navigation/members/${patient.id}/recommendations`);
        return { patient, data };
      } catch (error) {
        return { patient, data: null };
      }
    }));
    state.interventions = results;
    state.interventionsLoaded = true;
  }

  const opportunityChartHtml = careManagementOpportunityChartHtml(state.analytics?.careManagementOpportunities);

  if (state.interventions.length === 0) {
    container.innerHTML = `
      ${opportunityChartHtml}
      <div class="card">
        <div class="empty-state">
          <h3>No high-priority members found</h3>
          <p>Recommended actions are generated for members currently flagged as high utilization risk.</p>
        </div>
      </div>
    `;
    renderCareManagementOpportunityChart(state.analytics?.careManagementOpportunities);
    return;
  }

  container.innerHTML = `
    ${opportunityChartHtml}
    <div class="card">
      <div class="card-header">
        <p class="eyebrow">Recommended Actions</p>
        <h2>High-Priority Member Interventions</h2>
        <p class="muted">Care-management recommendations informed by historical utilization and ML risk signals -- the workflow, not the model, determines the intervention.</p>
      </div>
      <div class="member-queue-list">
        ${state.interventions.map(({ patient, data }) => {
          const recs = data?.recommendations || [];
          const topRec = recs[0];
          const categories = [...new Set(recs.map(r => r.category))];
          return `
            <div class="member-row-item">
              <div class="member-avatar">${patient.name.split(' ').map(n => n[0]).slice(0, 2).join('')}</div>
              <div class="member-info">
                <strong>${escapeHtml(patient.name)}</strong>
                <p>${escapeHtml(patient.beneficiaryId)} &bull; ${topRec ? escapeHtml(topRec.recommendation) : 'No recommendation available'}</p>
                ${topRec ? `<p class="muted" style="font-size: 11.5px; margin-top: 2px;">${escapeHtml(topRec.reason)}</p>` : ''}
                ${categories.length ? `<div style="margin-top: 6px; display: flex; gap: 6px; flex-wrap: wrap;">${categories.map(c => `<span class="badge low">${escapeHtml(c)}</span>`).join('')}</div>` : ''}
              </div>
              ${riskBadge(patient.riskLevel)}
              <button class="button secondary btn-open-member" data-member-id="${patient.id}">View Profile</button>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;

  renderCareManagementOpportunityChart(state.analytics?.careManagementOpportunities);

  $$('.btn-open-member', container).forEach(btn => {
    btn.addEventListener('click', () => openMember(btn.dataset.memberId));
  });
}

/**
 * VIEW 9: COST REDUCTION & PROGRAM OUTCOMES (Payer Intelligence)
 * Reuses the already-loaded GET /api/analytics -- no new backend endpoint.
 */
function renderCostReduction() {
  const container = $('#cost-reduction-content');
  if (!container) return;
  const a = state.analytics;
  if (!a) {
    container.innerHTML = '<div class="card"><div class="empty-state"><h3>Analytics unavailable</h3><p>Population analytics could not be loaded.</p></div></div>';
    return;
  }

  container.innerHTML = `
    <div class="stat-grid-container">
      <div class="stat-card">
        <div class="stat-label">Total Members</div>
        <div class="stat-value">${formatNumber(a.totalPatients)}</div>
        <div class="stat-detail">PostgreSQL Member Population</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Total ED Visits</div>
        <div class="stat-value">${formatNumber(a.totalEdVisits)}</div>
        <div class="stat-detail">12-Month Claims Ingestion</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Total ED Spend</div>
        <div class="stat-value">${formatMoney(a.totalEdSpend)}</div>
        <div class="stat-detail">12-Month Emergency Department Spend</div>
      </div>
    </div>

    <div class="chart-grid">
      <div class="card">
        <div class="card-header">
          <p class="eyebrow">Cost Analysis</p>
          <h2>ED Cost by Utilization Band</h2>
          <p class="muted">How ED spend is distributed across utilization levels.</p>
          <div class="filter-row" style="margin-top: 10px;">
            <label style="font-size: 12.5px; display: flex; align-items: center; gap: 6px;">
              <input type="radio" name="cost-band-metric" value="total" checked /> Total spend
            </label>
            <label style="font-size: 12.5px; display: flex; align-items: center; gap: 6px;">
              <input type="radio" name="cost-band-metric" value="average" /> Average spend per member
            </label>
          </div>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart-cost-by-band"></canvas></div>
      </div>
      <div class="card">
        <div class="card-header">
          <p class="eyebrow">Relationship Exploration</p>
          <h2>Utilization vs. ED Cost</h2>
          <p class="muted">Association between utilization band and average ED cost (not a claim of causation).</p>
        </div>
        <div class="chart-canvas-wrap"><canvas id="chart-utilization-vs-cost"></canvas></div>
      </div>
    </div>
  `;

  renderCostByBandChart(a.costByUtilizationBand, 'total');
  renderUtilizationVsCostChart(a.costByUtilizationBand);

  $$('input[name="cost-band-metric"]').forEach(radio => {
    radio.addEventListener('change', event => {
      renderCostByBandChart(a.costByUtilizationBand, event.target.value);
    });
  });
}

function renderCostByBandChart(costByBand, metric) {
  if (!costByBand || costByBand.length === 0) return;
  const isTotal = metric === 'total';
  renderChartJs('chart-cost-by-band', {
    type: 'bar',
    data: {
      labels: costByBand.map(row => `${row.band} visits`),
      datasets: [{
        label: isTotal ? 'Total ED Spend' : 'Average ED Spend per Member',
        data: costByBand.map(row => isTotal ? row.totalEdSpend : row.averageEdSpend),
        backgroundColor: BRAND_COLORS.deep,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const row = costByBand[ctx.dataIndex];
            return [`${formatMoney(ctx.parsed.y)}`, `${formatNumber(row.memberCount)} members in this band`];
          },
        } },
      },
      scales: { y: { beginAtZero: true, ticks: { callback: v => formatMoney(v) } } },
    },
  });
}

/**
 * Categorical bar chart, not a continuous scatter/line: "0", "1", "2-3",
 * "4-5", "6+" are utilization BANDS, not numeric x-values, and "6+" has no
 * true numeric midpoint. Plotting it as a point on a continuous axis (the
 * previous implementation used x=7 for "6+") misrepresents an open-ended
 * category as a specific number. A categorical x-axis avoids that entirely.
 */
function renderUtilizationVsCostChart(costByBand) {
  if (!costByBand || costByBand.length === 0) return;
  renderChartJs('chart-utilization-vs-cost', {
    type: 'bar',
    data: {
      labels: costByBand.map(row => row.band),
      datasets: [{
        label: 'Average ED spend',
        data: costByBand.map(row => row.averageEdSpend),
        backgroundColor: BRAND_COLORS.teal,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: {
          label: ctx => {
            const row = costByBand[ctx.dataIndex];
            return `${row.band} ED visits: ${formatMoney(ctx.parsed.y)} average (${formatNumber(row.memberCount)} members)`;
          },
        } },
      },
      scales: {
        x: { title: { display: true, text: 'ED Visit Count (band)' } },
        y: { title: { display: true, text: 'Average ED Spend' }, beginAtZero: true, ticks: { callback: v => formatMoney(v) } },
      },
    },
  });
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
    `;
  } catch (error) {
    detail.innerHTML = `<div class="notice emergency">Failed to load member profile: ${escapeHtml(error.message)}</div>`;
  }
}

function closeMember() {
  $('#member-panel').classList.remove('open');
  $('#panel-backdrop').classList.remove('open');
}

/**
 * PATIENT SELF-ENTRY PROFILE: load (GET /api/profile), populate, and save
 * (PUT /api/profile). Genuinely account-persistent in PostgreSQL, scoped to
 * the authenticated caller's own user_id -- never frontend/localStorage
 * state, and never a hardcoded demo identity.
 */
function populateProfileForm(profile) {
  if ($('#self-patient-name')) $('#self-patient-name').value = profile.fullName || '';
  if ($('#self-patient-age')) $('#self-patient-age').value = profile.age ?? '';
  if ($('#self-patient-zip')) $('#self-patient-zip').value = profile.zipCode || '';
  if ($('#self-patient-contact')) $('#self-patient-contact').value = profile.contactInfo || '';
  if ($('#self-patient-insurance')) $('#self-patient-insurance').value = profile.insuranceStatus || 'Self-Pay / Uninsured';
  if ($('#self-patient-pref-setting')) $('#self-patient-pref-setting').value = profile.preferredCareSetting || 'Virtual Telehealth First';
  if ($('#self-patient-comm')) $('#self-patient-comm').value = profile.communicationPreference || 'Email Updates';
}

async function loadPatientProfile() {
  const status = $('#profile-status-banner');
  if (status) status.innerHTML = '<p class="muted">Loading your profile…</p>';
  try {
    const profile = await request('/api/profile');
    populateProfileForm(profile);
    if (status) {
      status.innerHTML = profile.exists
        ? ''
        : '<p class="muted">Add your personal details below to complete your health profile.</p>';
    }
  } catch (error) {
    if (status) status.innerHTML = `<div class="notice emergency">Unable to load your profile: ${escapeHtml(error.message)}</div>`;
  }
}

async function saveSelfProfile(event) {
  event.preventDefault();
  const status = $('#profile-status-banner');
  const submitBtn = event.target.querySelector('button[type="submit"]');
  if (submitBtn) submitBtn.disabled = true;

  const ageRaw = $('#self-patient-age').value;
  const payload = {
    fullName: $('#self-patient-name').value.trim(),
    age: ageRaw ? parseInt(ageRaw, 10) : null,
    zipCode: $('#self-patient-zip').value.trim(),
    contactInfo: $('#self-patient-contact').value.trim(),
    insuranceStatus: $('#self-patient-insurance').value,
    preferredCareSetting: $('#self-patient-pref-setting').value,
    communicationPreference: $('#self-patient-comm').value,
  };

  try {
    await request('/api/profile', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    // Reload from PostgreSQL (not the just-submitted local payload) so the
    // displayed values are proven to have actually persisted.
    await loadPatientProfile();
    if (status) status.innerHTML = '<span class="badge low">Saved successfully</span>';
  } catch (error) {
    if (status) status.innerHTML = `<div class="notice emergency">Failed to save your profile: ${escapeHtml(error.message)}</div>`;
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

/**
 * CARE COPILOT CHAT DRAWER -- RightPath AI Patient Assistant
 * Reuses the existing #chat-panel scaffold as-is; wires it to POST
 * /api/patient/assistant. Only ever reachable from the "Ask RightPath AI"
 * CTA on a NON-EMERGENCY triage result (see renderNonEmergencyResult) --
 * the emergency result never renders that CTA. The backend independently
 * re-verifies the encounter's persisted is_emergency value regardless of
 * what the frontend sends, and returns {emergency: true} if it ever
 * disagrees; that response is handled below rather than trusted away.
 */
function openChat() {
  $('#chat-panel')?.classList.add('open');
  $('#panel-backdrop')?.classList.add('open');
}

function closeChat() {
  $('#chat-panel')?.classList.remove('open');
  $('#panel-backdrop')?.classList.remove('open');
}

function _prefersReducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;
}

/** Smoothly scroll a chat's own scrollable message list -- never the page. */
function _scrollChatToBottom(container) {
  if (!container) return;
  container.scrollTo({ top: container.scrollHeight, behavior: _prefersReducedMotion() ? 'auto' : 'smooth' });
}

function addTypingIndicator(messagesContainer, id) {
  messagesContainer.insertAdjacentHTML('beforeend', `
    <div class="chat-message assistant typing" id="${id}">
      <div class="typing-indicator" aria-hidden="true"><span></span><span></span><span></span></div>
      <p class="typing-label">Preparing your care guidance&hellip;</p>
    </div>
  `);
  _scrollChatToBottom(messagesContainer);
}

/** Long-wait state: swap the loader's label without touching the dots, so a
 * slow Gemini response never looks like a frozen/broken interface. */
function _updateTypingLabel(id, text) {
  const label = document.getElementById(id)?.querySelector('.typing-label');
  if (label) label.textContent = text;
}

function removeTypingIndicator(id) {
  document.getElementById(id)?.remove();
}

/**
 * Safe inline formatting for one line of assistant text: escapes the raw
 * text first (via the existing escapeHtml), then recognizes only Gemini's
 * own "**bold**" convention, converting it to a real <strong> tag. Because
 * the substitution runs on already-escaped text and only ever inserts a
 * fixed, literal tag, this can never introduce attacker-controlled markup --
 * it is not a markdown parser, just one narrow, safe pattern.
 */
function _formatAssistantInlineText(text) {
  // Order matters: consume "**bold**" pairs first so the single-asterisk
  // "*italic*" pass below only ever matches genuinely remaining single
  // pairs, not the inner half of an already-converted bold pair.
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>');
}

/**
 * Small, safe formatter for RightPath Payer AI replies (structured
 * dashboard-style analytics answers and Gemini prose alike). This is not a
 * markdown library: it only recognizes the plain-text conventions the
 * backend/Gemini already produce -- blank-line-separated sections, "•"/"*"/
 * "-" bullet lines, short title-style heading lines, and a trailing
 * "Note: ..." line -- and otherwise falls back to plain paragraphs with
 * preserved line breaks, so ordinary prose still renders correctly. All text
 * content is escaped before it ever reaches innerHTML (see
 * _formatAssistantInlineText above).
 */
function _isBulletLine(line) {
  return /^[•*-]\s+/.test(line);
}

/**
 * Strip two lightweight Gemini markdown conventions observed in practice
 * ("### Heading" ATX headings, and a whole line wrapped in "*italic*") down
 * to plain text before classification -- not a markdown parser, just two
 * narrow, safe textual normalizations so those literal marker characters
 * never show up in the rendered UI.
 */
function _stripLightweightMarkdown(line) {
  const withoutHeadingMarker = line.replace(/^#{1,6}\s+/, '');
  if (_isBulletLine(withoutHeadingMarker)) return withoutHeadingMarker;
  const wholeLineWrap = withoutHeadingMarker.match(/^\*{1,2}(.+)\*{1,2}$/);
  return wholeLineWrap ? wholeLineWrap[1].trim() : withoutHeadingMarker;
}

function _bulletListHtml(lines) {
  const items = lines
    .map(line => `<li>${_formatAssistantInlineText(line.replace(/^[•*-]\s+/, ''))}</li>`)
    .join('');
  return `<ul class="ai-reply-list">${items}</ul>`;
}

function _renderAssistantBlock(block) {
  const lines = block.split('\n').map(line => _stripLightweightMarkdown(line.trim())).filter(Boolean);
  if (lines.length === 0) return '';

  if (lines.every(_isBulletLine)) {
    return _bulletListHtml(lines);
  }

  // The backend emits sub-sections as a heading line immediately followed by
  // its bullets with NO blank line in between (e.g. "Assessments" then its
  // three "•" lines) -- so a block can be a heading + list pair, not just
  // one or the other.
  if (lines.length > 1 && !_isBulletLine(lines[0]) && lines.slice(1).every(_isBulletLine)) {
    const heading = `<h4 class="ai-reply-heading">${_formatAssistantInlineText(lines[0])}</h4>`;
    return heading + _bulletListHtml(lines.slice(1));
  }

  if (lines.length === 1) {
    const line = lines[0];
    if (/^note:/i.test(line)) {
      return `<p class="ai-reply-note">${_formatAssistantInlineText(line)}</p>`;
    }
    const looksLikeHeading = line.length <= 60 && !/[.!?]$/.test(line) && !/:\s*\S/.test(line);
    if (looksLikeHeading) {
      return `<h4 class="ai-reply-heading">${_formatAssistantInlineText(line)}</h4>`;
    }
    return `<p>${_formatAssistantInlineText(line)}</p>`;
  }

  // Ordinary multi-line prose paragraph -- preserve line breaks.
  return `<p>${lines.map(_formatAssistantInlineText).join('<br>')}</p>`;
}

function formatAssistantReply(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return '<p>Assistant response received.</p>';

  return raw
    .split(/\n{2,}/)
    .map(block => block.trim())
    .filter(Boolean)
    .map(_renderAssistantBlock)
    .join('');
}

/**
 * Core send path, shared by the form submit handler and the "Try again"
 * retry action below, so both follow the exact same request/render logic.
 * Renders the patient's message immediately (never waits on the network),
 * shows a compact loading bubble while Gemini/the deterministic router
 * responds, and disables the input/button for the duration to prevent
 * duplicate in-flight requests.
 */
async function sendChatMessage(messageText) {
  const input = $('#chat-input');
  const submitBtn = $('#chat-form button[type="submit"]');
  const messagesContainer = $('#chat-messages');
  if (!messageText || input?.disabled) return; // guard against duplicate/overlapping sends

  if (!state.currentEncounter?.encounterId) {
    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message assistant">
        <p>Please complete a symptom assessment first so RightPath AI has context for your question.</p>
      </div>
    `);
    _scrollChatToBottom(messagesContainer);
    return;
  }

  messagesContainer.insertAdjacentHTML('beforeend', `
    <div class="chat-message user">
      <p>${escapeHtml(messageText)}</p>
    </div>
  `);
  _scrollChatToBottom(messagesContainer);

  if (input) input.disabled = true;
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = 'Thinking…';
  }

  const typingId = `chat-typing-${Date.now()}`;
  addTypingIndicator(messagesContainer, typingId);
  // If a response hasn't arrived after a few seconds, reassure the patient
  // the interface is still working rather than leaving a static loader --
  // never naming the underlying AI provider/implementation.
  const longWaitTimer = setTimeout(
    () => _updateTypingLabel(typingId, 'Still preparing your guidance…'),
    4000,
  );

  try {
    const response = await request('/api/patient/assistant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: messageText,
        encounterId: state.currentEncounter.encounterId,
        triageContext: {
          recommendedAcuity: state.currentEncounter.recommendedAcuity,
          recommendedSettingName: state.currentEncounter.recommendedSettingName,
          clinicalRationale: state.currentEncounter.clinicalRationale,
          isEmergencyRedFlag: state.currentEncounter.isEmergencyRedFlag,
        },
      }),
    });

    clearTimeout(longWaitTimer);
    removeTypingIndicator(typingId);
    const replyText = response.emergency
      ? "RightPath AI can't help with emergency symptoms. Call 911 or go to the nearest Emergency Department right away."
      : (response.reply || 'Assistant response received.');
    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message assistant">${formatAssistantReply(replyText)}</div>
    `);
  } catch (error) {
    // Never surface a raw backend exception, and never invent a replacement
    // medical recommendation -- point back to the recommendation already on
    // screen and offer to retry the exact same question.
    clearTimeout(longWaitTimer);
    removeTypingIndicator(typingId);
    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message error">
        <p>Sorry, I couldn't reach the assistant right now.</p>
        <p>Your existing RightPath care recommendation is still available above.</p>
        <button type="button" class="text-button chat-retry-btn" data-retry-chat
          data-retry-question="${escapeHtml(messageText)}">Try again</button>
      </div>
    `);
  } finally {
    if (input) input.disabled = false;
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Send';
    }
    _scrollChatToBottom(messagesContainer);
  }
}

async function submitChat(event) {
  event.preventDefault();
  const input = $('#chat-input');
  const messageText = input.value.trim();
  if (!messageText || input.disabled) return; // guard against duplicate submissions
  input.value = '';
  await sendChatMessage(messageText);
}

/** "Try again" action on a failed request -- resends the exact same question. */
async function retryChatMessage(messageText) {
  const input = $('#chat-input');
  if (input?.disabled) return; // a request is already in flight
  await sendChatMessage(messageText);
}

/**
 * PAYER INTELLIGENCE ASSISTANT (page-embedded, not a drawer)
 * POST /api/payer/assistant -- grounded only in aggregate CMS/RightPath
 * analytics and optional approved RAG knowledge (see backend/routes/
 * assistant.py's _payer_context whitelist). There is no raw patient
 * complaint/conversation text available client-side to send, by design.
 */
async function submitPayerAssistantQuestion(questionText) {
  const messagesContainer = $('#payer-assistant-messages');
  const submitBtn = $('#payer-assistant-submit');
  if (!messagesContainer || !questionText || !questionText.trim()) return;
  if (submitBtn?.disabled) return; // guard against duplicate/overlapping submissions

  messagesContainer.insertAdjacentHTML('beforeend', `
    <div class="chat-message user">
      <p>${escapeHtml(questionText)}</p>
    </div>
  `);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  const input = $('#payer-assistant-input');
  if (input) input.disabled = true;
  if (submitBtn) submitBtn.disabled = true;
  $$('.prompt-chip').forEach(chip => { chip.disabled = true; });
  const typingId = `payer-assistant-typing-${Date.now()}`;
  addTypingIndicator(messagesContainer, typingId);

  try {
    const response = await request('/api/payer/assistant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: questionText }),
    });

    removeTypingIndicator(typingId);
    const sources = response.sources || [];
    const sourcesHtml = sources.length
      ? `<p class="muted" style="margin: 10px 0 4px 0; font-size: 12.5px; font-weight: 700;">Knowledge sources</p>
         <div class="chat-sources">
           ${sources.map(s => `<span class="badge low">${escapeHtml(s.title || s.source || 'Source')}${s.category ? ` &bull; ${escapeHtml(s.category)}` : ''}</span>`).join('')}
         </div>`
      : '';
    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message assistant">
        ${formatAssistantReply(response.reply)}
        ${sourcesHtml}
      </div>
    `);
  } catch (error) {
    removeTypingIndicator(typingId);
    messagesContainer.insertAdjacentHTML('beforeend', `
      <div class="chat-message assistant">
        <p>RightPath AI is temporarily unavailable. Please try again shortly.</p>
      </div>
    `);
  } finally {
    if (input) input.disabled = false;
    if (submitBtn) submitBtn.disabled = false;
    $$('.prompt-chip').forEach(chip => { chip.disabled = false; });
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }
}

async function submitPayerAssistantForm(event) {
  event.preventDefault();
  const input = $('#payer-assistant-input');
  const questionText = input.value.trim();
  if (!questionText) return;
  input.value = '';
  await submitPayerAssistantQuestion(questionText);
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
  $('#back-to-portal-select')?.addEventListener('click', () => {
    state.selectedPortal = null;
    showPortalSelect();
  });

  // Navigation event delegation
  document.addEventListener('click', event => {
    const portalSelectBtn = event.target.closest('[data-portal-select]');
    if (portalSelectBtn) {
      event.preventDefault();
      selectPortal(portalSelectBtn.dataset.portalSelect);
    }

    const routeBtn = event.target.closest('[data-route]');
    if (routeBtn) {
      event.preventDefault();
      route(routeBtn.dataset.route);
    }

    const urgentCareAction = event.target.closest('[data-urgent-care-action]');
    if (urgentCareAction) {
      event.preventDefault();
      if (urgentCareAction.dataset.urgentCareAction === 'open-map') openUrgentCareMap();
      // "Find Emergency Department" reuses the exact same live urgent-care
      // map/route flow (which already surfaces hospitals, not only urgent
      // care) -- no second map implementation.
      if (urgentCareAction.dataset.urgentCareAction === 'find-emergency') { route('urgent-care-map'); requestBrowserLocation(); }
      if (urgentCareAction.dataset.urgentCareAction === 'retry-location') requestBrowserLocation();
      if (urgentCareAction.dataset.urgentCareAction === 'select-facility') selectUrgentCareFacility(urgentCareAction.dataset.facilityId);
    }

    const pathwayAction = event.target.closest('[data-pathway-action]');
    if (pathwayAction) {
      event.preventDefault();
      const action = pathwayAction.dataset.pathwayAction;
      if (action === 'start-telehealth') startSimulatedTelehealth();
      if (action === 'show-primary-care') showPrimaryCareOptions(pathwayAction);
      if (action === 'return-to-recommendation') returnToCareRecommendation();
    }

    const selectProviderBtn = event.target.closest('[data-select-provider-id]');
    if (selectProviderBtn) {
      event.preventDefault();
      selectRecommendedProvider(selectProviderBtn);
    }

    const openChatBtn = event.target.closest('[data-open-chat]');
    if (openChatBtn) {
      event.preventDefault();
      openChat();
    }

    const assistantPromptChip = event.target.closest('[data-assistant-prompt]');
    if (assistantPromptChip && !assistantPromptChip.disabled) {
      event.preventDefault();
      submitPayerAssistantQuestion(assistantPromptChip.dataset.assistantPrompt);
    }

    if (event.target.closest('[data-close-panel]') || event.target === $('#panel-backdrop')) {
      closeMember();
      closeChat();
      $('#sidebar-nav')?.classList.remove('open');
    }

    if (event.target.closest('[data-close-chat]')) {
      closeChat();
    }

    const retryChatBtn = event.target.closest('[data-retry-chat]');
    if (retryChatBtn) {
      event.preventDefault();
      const question = retryChatBtn.dataset.retryQuestion || '';
      retryChatBtn.closest('.chat-message')?.remove();
      retryChatMessage(question);
    }
  });

  // RightPath AI chat drawer (patient) and Payer Intelligence Assistant form
  $('#chat-form')?.addEventListener('submit', submitChat);
  $('#payer-assistant-form')?.addEventListener('submit', submitPayerAssistantForm);

  // Mobile sidebar toggle
  $('#sidebar-toggle')?.addEventListener('click', () => {
    $('#sidebar-nav')?.classList.toggle('open');
    $('#panel-backdrop')?.classList.toggle('open');
  });

  // Filters & Form listeners
  $('#member-search')?.addEventListener('input', filterCohort);
  $('#risk-filter')?.addEventListener('change', filterCohort);
  $('#utilization-band-filter')?.addEventListener('change', filterCohort);
  $('#anomaly-filter')?.addEventListener('change', filterCohort);
  $('#reset-cohort-filters')?.addEventListener('click', resetCohortFilters);

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
    // The Personal Information / Care Preferences form is populated from
    // GET /api/profile only once the "patient-profile" route is actually
    // opened by an authenticated caller (see route() and
    // loadPatientProfile()) -- never from a frontend default here.
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
