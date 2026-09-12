/**
 * Sentinel Gujarat — Frontend Application Logic
 */

const API_BASE = window.location.protocol.startsWith("http") ? window.location.origin : "http://localhost:8000";
let ws = null;
let token = localStorage.getItem("sentinel_token");
let currentUser = null;

// DOM Elements
const els = {
  loginScreen: document.getElementById("login-screen"),
  loginForm: document.getElementById("login-form"),
  loginError: document.getElementById("login-error"),
  loginBtn: document.getElementById("login-btn"),
  
  app: document.getElementById("app"),
  timeDisplay: document.getElementById("time-display"),
  wsIndicator: document.getElementById("ws-indicator"),
  alertBadge: document.getElementById("alert-badge"),
  
  userName: document.getElementById("user-name"),
  userRole: document.getElementById("user-role"),
  userAvatar: document.getElementById("user-avatar"),
  logoutBtn: document.getElementById("logout-btn"),
  
  navItems: document.querySelectorAll(".nav-item"),
  tabs: document.querySelectorAll(".tab-content"),
  topbarTitle: document.getElementById("topbar-title"),
  
  toastContainer: document.getElementById("toast-container"),
  
  // Modals
  alertModal: document.getElementById("alert-modal"),
  alertModalBody: document.getElementById("alert-modal-body"),
  cameraModal: document.getElementById("camera-modal"),
  cameraModalBody: document.getElementById("camera-modal-body")
};

// Initialize Icons
lucide.createIcons();

// Time display
setInterval(() => {
  const now = new Date();
  els.timeDisplay.textContent = now.toLocaleString("en-IN", { 
    dateStyle: "medium", timeStyle: "medium", timeZone: "Asia/Kolkata" 
  });
}, 1000);

// ============================================================
// APP INIT
// ============================================================

async function init() {
  if (token) {
    // Validate token
    try {
      const res = await fetch(`${API_BASE}/auth/me`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        currentUser = await res.json();
        showApp();
      } else {
        logout();
      }
    } catch (e) {
      console.error("Init auth check failed", e);
      logout();
    }
  } else {
    showLogin();
  }
}

function showLogin() {
  els.loginScreen.classList.remove("hidden");
  els.app.classList.add("hidden");
}

function showApp() {
  els.loginScreen.classList.add("hidden");
  els.app.classList.remove("hidden");
  
  els.userName.textContent = currentUser.username;
  els.userRole.textContent = currentUser.role;
  els.userAvatar.textContent = currentUser.username.charAt(0).toUpperCase();
  
  // Apply role restrictions
  if (currentUser.role !== "ADMIN") {
    document.getElementById("nav-audit").style.display = "none";
  }
  
  connectWebSocket();
  loadData();
  switchTab("overview");
}

// ============================================================
// AUTHENTICATION
// ============================================================

els.loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  
  const user = document.getElementById("login-username").value;
  const pass = document.getElementById("login-password").value;
  
  if (!user || !pass) return;
  
  els.loginBtn.disabled = true;
  els.loginBtn.innerHTML = '<div class="spinner" style="width:16px;height:16px;border-width:2px;margin-right:8px;"></div> Signing In...';
  
  const formData = new URLSearchParams();
  formData.append("username", user);
  formData.append("password", pass);
  
  try {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formData
    });
    
    if (res.ok) {
      const data = await res.json();
      token = data.access_token;
      localStorage.setItem("sentinel_token", token);
      
      const meRes = await fetch(`${API_BASE}/auth/me`, {
        headers: { "Authorization": `Bearer ${token}` }
      });
      currentUser = await meRes.json();
      
      els.loginError.classList.add("hidden");
      showApp();
    } else {
      const err = await res.json();
      els.loginError.textContent = err.detail || "Authentication failed";
      els.loginError.classList.remove("hidden");
    }
  } catch (e) {
    els.loginError.textContent = "Network error. Is the backend running?";
    els.loginError.classList.remove("hidden");
  } finally {
    els.loginBtn.disabled = false;
    els.loginBtn.innerHTML = '<span>Sign In</span> <i data-lucide="arrow-right"></i>';
    lucide.createIcons();
  }
});

els.logoutBtn.addEventListener("click", logout);

function logout() {
  token = null;
  currentUser = null;
  localStorage.removeItem("sentinel_token");
  if (ws) { ws.close(); ws = null; }
  showLogin();
}

// ============================================================
// NAVIGATION
// ============================================================

els.navItems.forEach(item => {
  item.addEventListener("click", () => switchTab(item.dataset.tab));
});

function switchTab(tabId) {
  els.navItems.forEach(i => i.classList.remove("active"));
  els.tabs.forEach(t => t.classList.remove("active"));
  
  const activeNav = document.getElementById(`nav-${tabId}`);
  if (activeNav) activeNav.classList.add("active");
  document.getElementById(`tab-${tabId}`).classList.add("active");
  
  els.topbarTitle.textContent = activeNav ? activeNav.querySelector('span:not(.badge)').textContent : tabId;
  
  // Refresh specific tab data
  if (tabId === "cameras") loadCameras();
  else if (typeof destroyQuadGrid === "function") destroyQuadGrid();
  if (tabId === "alerts") loadAlerts();
  if (tabId === "watchlist") loadWatchlist();
  if (tabId === "audit" && currentUser.role === "ADMIN") loadAudit();
  if (tabId === "overview") loadOverview();
  
  if (tabId === "map") {
    loadMapTab();
    if (window.googleMapInstance && window.google && window.google.maps) {
      setTimeout(() => {
        google.maps.event.trigger(window.googleMapInstance, 'resize');
      }, 100);
    }
  }
}

document.getElementById("sync-btn").addEventListener("click", async () => {
  if (currentUser.role === "DEPARTMENT_USER") {
    showToast("Insufficient permissions to sync cameras", "error");
    return;
  }
  
  try {
    const res = await fetch(`${API_BASE}/cameras/sync`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.ok) {
      const data = await res.json();
      showToast(`Synced ${data.cameras_found} cameras from Sentinel`, "success");
      loadCameras();
      loadOverview();
    }
  } catch (e) {
    showToast("Failed to sync cameras", "error");
  }
});

// ============================================================
// WEBSOCKET
// ============================================================

let activeAlerts = [];

function connectWebSocket() {
  if (ws) ws.close();
  
  const wsUrl = API_BASE.replace("http://", "ws://").replace("https://", "wss://") + "/ws/alerts";
  ws = new WebSocket(wsUrl);
  
  ws.onopen = () => {
    els.wsIndicator.innerHTML = '<span class="status-dot status-live"></span><span>WS Connected</span>';
  };
  
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    if (data.type === "INIT") {
      activeAlerts = data.alerts;
      updateAlertUI();
    } else if (data.type === "NEW_ALERT") {
      activeAlerts.unshift(data.alert);
      if (activeAlerts.length > 100) activeAlerts.pop();
      updateAlertUI();
      
      // Notification
      if (data.alert.severity === "HIGH") {
        showToast(`URGENT: ${data.alert.registration_number} detected! (${data.alert.watchlist_status})`, "error", 8000);
      } else {
        showToast(`Alert: ${data.alert.registration_number} detected`, "warning");
      }
    }
  };
  
  ws.onclose = () => {
    els.wsIndicator.innerHTML = '<span class="status-dot status-error"></span><span>WS Disconnected</span>';
    // Try to reconnect in 5s
    setTimeout(() => { if (token) connectWebSocket(); }, 5000);
  };
}

function updateAlertUI() {
  const newAlertsCount = activeAlerts.filter(a => a.status === "NEW").length;
  
  if (newAlertsCount > 0) {
    els.alertBadge.textContent = newAlertsCount > 99 ? "99+" : newAlertsCount;
    els.alertBadge.style.display = "inline-flex";
    document.getElementById("stat-alerts").textContent = newAlertsCount;
  } else {
    els.alertBadge.style.display = "none";
    document.getElementById("stat-alerts").textContent = "0";
  }
  
  // Update overview list
  renderOverviewAlerts();
  
  // Update alerts tab if active
  if (document.getElementById("tab-alerts").classList.contains("active")) {
    renderAlertsList();
  }
}

// ============================================================
// DATA LOADING
// ============================================================

async function loadData() {
  loadOverview();
  // Map is lazy loaded
}

async function loadOverview() {
  try {
    // Health / Status
    const healthRes = await fetch(`${API_BASE}/health`);
    if (healthRes.ok) {
      const health = await healthRes.json();
      const dbBadge = document.getElementById("db-status");
      if (health.database === "connected") {
        dbBadge.className = "badge badge-green"; dbBadge.textContent = "Connected";
      } else {
        dbBadge.className = "badge badge-red"; dbBadge.textContent = "Offline (In-Memory)";
      }
      
      const mapBadge = document.getElementById("maps-status");
      if (health.google_maps === "configured") {
        mapBadge.className = "badge badge-green"; mapBadge.textContent = "Configured";
      } else {
        mapBadge.className = "badge badge-yellow"; mapBadge.textContent = "Fallback Active";
      }
    }
    
    // Cameras count
    const camRes = await fetch(`${API_BASE}/cameras`, { headers: { "Authorization": `Bearer ${token}` } });
    if (camRes.ok) {
      const cams = await camRes.json();
      document.getElementById("stat-cameras").textContent = cams.length;
    }
    
    // Watchlist count
    const wlRes = await fetch(`${API_BASE}/watchlist`, { headers: { "Authorization": `Bearer ${token}` } });
    if (wlRes.ok) {
      const wl = await wlRes.json();
      document.getElementById("stat-watchlist").textContent = wl.total;
    }
    
  } catch (e) {
    console.error("Failed to load overview data", e);
  }
}

function renderOverviewAlerts() {
  const container = document.getElementById("overview-alerts");
  
  if (activeAlerts.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <i data-lucide="check-circle"></i>
        <p>No active alerts</p>
        <small>System is monitoring live streams</small>
      </div>
    `;
    lucide.createIcons();
    return;
  }
  
  const recent = activeAlerts.slice(0, 4);
  container.innerHTML = recent.map(alert => `
    <div class="alert-item severity-${alert.severity}" onclick="openAlert('${alert.id}')">
      <div class="alert-severity-badge sev-${alert.severity}">
        ${alert.severity}
      </div>
      <div class="alert-info">
        <div class="alert-plate">${alert.registration_number}</div>
        <div class="alert-meta">
          <span class="alert-meta-item"><i data-lucide="clock"></i> ${formatTime(alert.timestamp)}</span>
          <span class="alert-meta-item"><i data-lucide="video"></i> ${alert.camera_id}</span>
          <span class="alert-meta-item"><i data-lucide="tag"></i> ${alert.watchlist_status}</span>
        </div>
      </div>
      <div class="alert-conf">
        <span class="conf-value" style="color: ${getConfColor(alert.overall_confidence)}">${Math.round(alert.overall_confidence * 100)}%</span>
        <span class="conf-label">CONFIDENCE</span>
      </div>
    </div>
  `).join("");
  lucide.createIcons();
}

// ============================================================
// ALERTS TAB
// ============================================================

let currentAlertFilter = 'all';

async function loadAlerts() {
  try {
    const res = await fetch(`${API_BASE}/alerts`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.ok) {
      const data = await res.json();
      activeAlerts = data.alerts;
      updateAlertUI();
    }
  } catch (e) {
    showToast("Failed to load alerts", "error");
  }
}

function filterAlerts(severity, btn) {
  currentAlertFilter = severity;
  
  // Update pills
  document.querySelectorAll('.alerts-toolbar .pill').forEach(p => p.classList.remove('pill-active', 'active'));
  btn.classList.add(severity === 'all' ? 'pill-active' : 'active');
  
  renderAlertsList();
}

function renderAlertsList() {
  const container = document.getElementById("alerts-container");
  
  const filtered = currentAlertFilter === 'all' 
    ? activeAlerts 
    : activeAlerts.filter(a => a.severity === currentAlertFilter);
    
  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <i data-lucide="check-circle"></i>
        <p>No alerts found</p>
        <small>No alerts match the current filter</small>
      </div>
    `;
    lucide.createIcons();
    return;
  }
  
  container.innerHTML = filtered.map(alert => `
    <div class="alert-item severity-${alert.severity}" onclick="openAlert('${alert.id}')">
      <div class="alert-severity-badge sev-${alert.severity}">
        ${alert.severity}
      </div>
      <div class="alert-info">
        <div class="alert-plate">${alert.registration_number}</div>
        <div class="alert-meta">
          <span class="alert-meta-item"><i data-lucide="clock"></i> ${formatTime(alert.timestamp)}</span>
          <span class="alert-meta-item"><i data-lucide="video"></i> ${alert.camera_id}</span>
          ${alert.location ? `<span class="alert-meta-item"><i data-lucide="map-pin"></i> ${alert.location}</span>` : ''}
          <span class="alert-meta-item"><i data-lucide="tag"></i> ${alert.watchlist_status}</span>
        </div>
      </div>
      <div class="alert-conf">
        <span class="conf-value" style="color: ${getConfColor(alert.overall_confidence)}">${Math.round(alert.overall_confidence * 100)}%</span>
        <span class="conf-label">CONFIDENCE</span>
      </div>
      <div class="alert-actions">
        ${alert.status === 'NEW' 
          ? `<span class="badge badge-red">NEW</span>` 
          : `<span class="badge badge-gray">ACKNOWLEDGED</span>`}
      </div>
    </div>
  `).join("");
  lucide.createIcons();
}

let currentAlertId = null;
let currentAlertPlate = null;

async function openAlert(id) {
  try {
    const res = await fetch(`${API_BASE}/alerts/${id}`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    
    if (res.ok) {
      const alert = await res.json();
      currentAlertId = alert.id;
      currentAlertPlate = alert.registration_number;
      
      const body = els.alertModalBody;
      body.innerHTML = `
        <div class="modal-section-title">Detection Info</div>
        <dl class="modal-kv">
          <dt><i data-lucide="hash"></i> Plate</dt>
          <dd class="alert-plate" style="margin:0">${alert.registration_number}</dd>
          
          <dt><i data-lucide="video"></i> Camera</dt>
          <dd>${alert.camera_id}</dd>
          
          <dt><i data-lucide="map-pin"></i> Location</dt>
          <dd>${alert.location || 'Unknown'}</dd>
          
          <dt><i data-lucide="clock"></i> Time</dt>
          <dd>${new Date(alert.timestamp).toLocaleString("en-IN")}</dd>
        </dl>
        
        <div style="height:1px;background:var(--border);margin:8px 0"></div>
        
        <div class="modal-section-title">Watchlist Details</div>
        <dl class="modal-kv">
          <dt><i data-lucide="tag"></i> Status</dt>
          <dd><span class="badge badge-${alert.severity === 'HIGH' ? 'red' : 'yellow'}">${alert.watchlist_status}</span></dd>
          
          <dt><i data-lucide="alert-circle"></i> Priority</dt>
          <dd>${alert.priority}</dd>
          
          <dt><i data-lucide="file-text"></i> Description</dt>
          <dd>${alert.watchlist_description || 'N/A'}</dd>
        </dl>
        
        <div style="height:1px;background:var(--border);margin:8px 0"></div>
        
        <div class="modal-section-title">Confidence Breakdown</div>
        <div style="display:flex;flex-direction:column;gap:12px;">
          
          <div>
            <div style="display:flex;justify-content:space-between;font-size:0.8rem">
              <span>Overall Confidence</span>
              <span style="color:${getConfColor(alert.overall_confidence)};font-weight:700">${Math.round(alert.overall_confidence * 100)}%</span>
            </div>
            <div class="confidence-bar-container">
              <div class="confidence-bar-bg">
                <div class="confidence-bar-fill" style="width:${alert.overall_confidence * 100}%;background:${getConfColor(alert.overall_confidence)}"></div>
              </div>
            </div>
          </div>
          
          <dl class="modal-kv" style="margin-top:4px;">
            <dt>Vehicle Detection (YOLO)</dt>
            <dd>${Math.round((alert.confidence_breakdown?.vehicle_detection_confidence || 0) * 100)}%</dd>
            
            <dt>Plate OCR (Paddle)</dt>
            <dd>${Math.round((alert.confidence_breakdown?.ocr_confidence || 0) * 100)}%</dd>
          </dl>
          
          ${alert.confidence_breakdown?.notes?.length ? `
            <div style="margin-top:8px;padding:8px;background:var(--bg-card);border:1px solid var(--border);border-radius:4px;font-size:0.75rem;color:var(--text-secondary)">
              <strong>Scoring Notes:</strong><br>
              ${alert.confidence_breakdown.notes.join('<br>')}
            </div>
          ` : ''}
        </div>
      `;
      
      const ackBtn = document.getElementById("ack-btn");
      if (alert.status === "NEW") {
        ackBtn.style.display = "inline-flex";
      } else {
        ackBtn.style.display = "none";
      }
      
      lucide.createIcons();
      els.alertModal.classList.remove("hidden");
    }
  } catch (e) {
    showToast("Failed to load alert details", "error");
  }
}

async function acknowledgeCurrentAlert() {
  if (!currentAlertId) return;
  
  if (currentUser.role === "DEPARTMENT_USER") {
    showToast("Only officers or admins can acknowledge alerts", "error");
    return;
  }
  
  try {
    const res = await fetch(`${API_BASE}/alerts/${currentAlertId}/acknowledge`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` }
    });
    
    if (res.ok) {
      showToast("Alert acknowledged", "success");
      // Update local state
      const a = activeAlerts.find(x => x.id === currentAlertId);
      if (a) a.status = "REVIEWING";
      
      updateAlertUI();
      closeModal("alert-modal");
    } else {
      showToast("Failed to acknowledge alert", "error");
    }
  } catch (e) {
    showToast("Network error", "error");
  }
}

function investigateFromAlert() {
  closeModal("alert-modal");
  if (currentAlertPlate) {
    setPlate(currentAlertPlate);
    investigatePlate();
  }
}

// ============================================================
// INVESTIGATE / TRACKING
// ============================================================

function setPlate(plate) {
  document.getElementById("plate-input").value = plate;
  switchTab("investigate");
}

async function investigatePlate() {
  const plate = document.getElementById("plate-input").value.replace(/\s+/g, '').toUpperCase();
  if (!plate) return;
  
  const btn = document.getElementById("investigate-btn");
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px;margin-right:6px;"></div> Loading...';
  
  try {
    const res = await fetch(`${API_BASE}/vehicles/${plate}/history`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    
    const data = await res.json();
    
    document.getElementById("investigate-empty").style.display = "none";
    document.getElementById("investigate-result").classList.remove("hidden");
    
    renderInvestigationResult(plate, data.journey);
    
  } catch (e) {
    showToast("Failed to fetch vehicle history", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i data-lucide="search"></i> Investigate';
    lucide.createIcons();
  }
}

function renderInvestigationResult(plate, journey) {
  const verdictCard = document.getElementById("verdict-card");
  const timeline = document.getElementById("journey-timeline");
  
  if (!journey || !journey.segments || journey.segments.length === 0) {
    verdictCard.innerHTML = `
      <div style="width:48px;height:48px;border-radius:50%;background:rgba(100,116,139,0.15);display:flex;align-items:center;justify-content:center;color:#94a3b8">
        <i data-lucide="search-X"></i>
      </div>
      <div>
        <h3 style="font-size:1.1rem;margin-bottom:4px;">No Sightings Found</h3>
        <p style="color:var(--text-secondary);font-size:0.85rem">Vehicle <span class="plate-chip" style="margin:0 4px">${plate}</span> has not been detected by the Sentinel network.</p>
      </div>
    `;
    timeline.innerHTML = '';
    lucide.createIcons();
    return;
  }
  
  // Verdict
  let vIcon = "check-circle";
  let vColor = "var(--brand-green)";
  let vBg = "rgba(16,185,129,0.15)";
  
  if (journey.watchlist_matched || journey.watchlist_status) {
    vIcon = "shield-alert";
    vColor = "var(--brand-red)";
    vBg = "rgba(239,68,68,0.15)";
  }
  
  verdictCard.innerHTML = `
    <div style="width:48px;height:48px;border-radius:50%;background:${vBg};display:flex;align-items:center;justify-content:center;color:${vColor}">
      <i data-lucide="${vIcon}"></i>
    </div>
    <div style="flex:1">
      <h3 style="font-size:1.1rem;margin-bottom:4px;display:flex;align-items:center;gap:12px;">
        <span style="font-family:'JetBrains Mono',monospace">${plate}</span>
        ${journey.watchlist_status ? `<span class="badge badge-red">${journey.watchlist_status}</span>` : ''}
      </h3>
      <p style="color:var(--text-secondary);font-size:0.85rem">
        Detected ${journey.sighting_count} times across ${journey.camera_count} cameras.
        First seen: ${formatTime(journey.first_seen)}. Last seen: ${formatTime(journey.last_seen)}.
      </p>
    </div>
    <button class="btn btn-primary" onclick="trackOnMap('${plate}')">
      <i data-lucide="map"></i> Map View
    </button>
  `;
  
  // Timeline
  let html = '';
  
  for (let i = 0; i < journey.segments.length; i++) {
    const seg = journey.segments[i];
    
    // The observation
    html += `
      <div class="journey-segment">
        <div class="segment-dot dot-confirmed"></div>
        <div class="segment-content">
          <div class="segment-type type-confirmed">CONFIRMED OBSERVATION</div>
          <div class="segment-camera">${seg.camera_id}</div>
          <div class="segment-details">
            <span class="segment-detail"><i data-lucide="clock"></i> ${formatTime(seg.event_time)}</span>
            ${seg.location ? `<span class="segment-detail"><i data-lucide="map-pin"></i> ${seg.location}</span>` : ''}
            ${seg.overall_confidence ? `<span class="segment-detail"><i data-lucide="check-square"></i> ${Math.round(seg.overall_confidence*100)}% confidence</span>` : ''}
          </div>
        </div>
      </div>
    `;
    
    // The inferred route (if there is a next segment)
    if (i < journey.segments.length - 1) {
      const next = journey.segments[i+1];
      html += `
        <div class="journey-segment">
          <div class="segment-line"></div>
          <div class="segment-dot dot-inferred"></div>
          <div class="segment-content" style="background:transparent;border-style:dashed;">
            <div class="segment-type type-inferred">INFERRED POSSIBLE ROUTE</div>
            <div class="segment-details" style="margin-top:6px;">
              <span class="segment-detail"><i data-lucide="arrow-right"></i> Predicted route from ${seg.camera_id} to ${next.camera_id}</span>
            </div>
          </div>
        </div>
      `;
    }
  }
  
  timeline.innerHTML = html;
  lucide.createIcons();
}

// ============================================================
// MAP
// ============================================================

function trackOnMap(plateOverride) {
  const plate = plateOverride || document.getElementById("map-plate-search").value || document.getElementById("plate-input").value;
  if (!plate) return;
  
  document.getElementById("map-plate-search").value = plate;
  switchTab("map");
  
  // In a real app, this would fetch route segments and draw them on Google Maps.
  // For the demo, we just show a mock message.
  document.getElementById("map-status-text").innerHTML = `
    <span style="color:var(--brand-green);font-weight:600">Tracking ${plate.toUpperCase()}</span><br>
    Fetching route data...
  `;
}

// ============================================================
// CAMERAS
// ============================================================

let allCameras = [];

async function loadCameras() {
  const container = document.getElementById("camera-grid");
  container.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Loading cameras from catalogue...</p></div>';
  
  try {
    const res = await fetch(`${API_BASE}/cameras`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.ok) {
      allCameras = await res.json();
      renderCameras('all');
    }
  } catch (e) {
    container.innerHTML = '<div class="empty-state"><i data-lucide="wifi-off"></i><p>Failed to load cameras</p></div>';
    lucide.createIcons();
  }
}

function filterCameras(filter, btn) {
  document.querySelectorAll('.filter-group button').forEach(b => b.classList.remove('filter-active'));
  btn.classList.add('filter-active');
  renderCameras(filter);
}

function renderCameras(filter) {
  const container = document.getElementById("camera-grid");
  const query = document.getElementById("camera-search")?.value.toLowerCase() || "";
  
  let filtered = allCameras.filter(c => {
    if (filter === 'live' && c.live_status === false) return false;
    if (query && !c.camera_id.toLowerCase().includes(query) && 
        !(c.location && c.location.toLowerCase().includes(query))) return false;
    return true;
  });
  
  if (filtered.length === 0) {
    container.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><p>No cameras match the filter</p></div>';
    return;
  }
  
  container.innerHTML = filtered.map(cam => `
    <div class="camera-card" onclick="openCamera('${cam.camera_id}')">
      <div class="camera-card-top">
        <span class="camera-id">${cam.camera_id}</span>
        ${cam.live_status === true ? '<span class="status-dot status-live"></span>' : (cam.live_status === false ? '<span class="status-dot status-error"></span>' : '<span class="status-dot status-warning" style="background:#f59e0b"></span>')}
      </div>
      
      <div class="camera-preview">
        <div class="camera-preview-overlay">
          <i data-lucide="video"></i>
          <small>${cam.live_status === true ? 'Live Feed Available' : (cam.live_status === false ? 'Offline' : 'Status Unknown')}</small>
        </div>
      </div>
      
      <div class="camera-meta">
        <div class="camera-meta-row">
          <span>Location</span>
          <span style="color:var(--text-primary)">${cam.location || 'Unknown'}</span>
        </div>
        <div class="camera-meta-row">
          <span>Codec</span>
          <span>${cam.codec || 'H.264'}</span>
        </div>
      </div>
    </div>
  `).join("");
  lucide.createIcons();
}

window.activeModalHls = null;
window.quadHlsInstances = [];function openCamera(id) {
  const cam = allCameras.find(c => c.camera_id === id);
  if (!cam) return;
  
  if (window.activeModalHls) {
    window.activeModalHls.destroy();
    window.activeModalHls = null;
  }
  
  const streamUrl = `${API_BASE}/api/hls/${cam.camera_id}/index.m3u8`;

  els.cameraModalBody.innerHTML = `
    <div style="background:#000;border-radius:10px;overflow:hidden;position:relative;aspect-ratio:16/9;margin-bottom:16px;box-shadow:0 8px 24px rgba(0,0,0,0.6);border:1px solid var(--border);cursor:pointer;" id="modal-video-container" onclick="document.getElementById('modal-hls-video').play()">
      <video id="modal-hls-video" autoplay muted playsinline style="width:100%;height:100%;object-fit:contain;background:#000;display:block;"></video>
      <div id="modal-play-overlay" style="display:none;position:absolute;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;cursor:pointer;z-index:5;" onclick="this.style.display='none';document.getElementById('modal-hls-video').play()">
        <div style="background:rgba(255,255,255,0.15);border-radius:50%;width:64px;height:64px;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px);border:2px solid rgba(255,255,255,0.3);">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="white"><polygon points="5,3 19,12 5,21"/></svg>
        </div>
      </div>
      
      <!-- Live CCTV Header Overlay -->
      <div id="modal-video-badge" style="position:absolute;top:12px;left:12px;display:flex;gap:8px;align-items:center;background:rgba(15,23,42,0.85);backdrop-filter:blur(6px);padding:5px 12px;border-radius:6px;border:1px solid rgba(255,255,255,0.12);z-index:10;">
        <span class="status-dot status-live" style="box-shadow:0 0 8px #10b981;animation:pulse 1.5s infinite;"></span>
        <span style="font-size:0.75rem;font-weight:800;color:#10b981;letter-spacing:1px;">LIVE</span>
        <span style="color:var(--text-muted);font-size:0.75rem;">|</span>
        <span style="color:#f8fafc;font-size:0.75rem;font-weight:700;font-family:'JetBrains Mono',monospace;">${cam.camera_id}</span>
      </div>

      <!-- Live Stream Controls Overlay (Pure CCTV: No seek, no duration) -->
      <div style="position:absolute;bottom:12px;left:12px;right:12px;display:flex;justify-content:space-between;align-items:center;z-index:10;">
        <div id="modal-video-status" style="font-size:0.72rem;background:rgba(15,23,42,0.8);backdrop-filter:blur(4px);color:#94a3b8;padding:4px 10px;border-radius:6px;border:1px solid rgba(255,255,255,0.08);display:flex;align-items:center;gap:6px;">
          <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#10b981;"></span>
          <span>Connecting Live Stream...</span>
        </div>
        <div style="display:flex;gap:8px;">
          <button id="modal-audio-btn" class="btn btn-ghost btn-sm" onclick="toggleModalAudio()" style="background:rgba(15,23,42,0.8);backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:4px 10px;font-size:0.75rem;">
            <i data-lucide="volume-x" style="width:14px;height:14px"></i> Muted
          </button>
          <button class="btn btn-ghost btn-sm" onclick="toggleModalFullscreen()" style="background:rgba(15,23,42,0.8);backdrop-filter:blur(4px);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:4px 10px;font-size:0.75rem;">
            <i data-lucide="maximize" style="width:14px;height:14px"></i> Fullscreen
          </button>
        </div>
      </div>
    </div>
    
    <div class="modal-section-title">Camera Metadata</div>
    <dl class="modal-kv">
      <dt>Camera ID</dt><dd><code>${cam.camera_id}</code></dd>
      <dt>Location</dt><dd><strong>${cam.location || 'N/A'}</strong></dd>
      <dt>Coordinates</dt><dd>${cam.latitude !== null && cam.latitude !== undefined && cam.longitude !== null && cam.longitude !== undefined ? `${cam.latitude}, ${cam.longitude}` : '<span style="color:var(--text-muted)">Not provided in catalogue</span>'}</dd>
      <dt>Stream Mode</dt><dd><span class="badge badge-green">CONTINUOUS LIVE CCTV</span></dd>
    </dl>
    
    <div style="height:1px;background:var(--border);margin:12px 0"></div>
    
    <div class="modal-section-title">Integration & Relay Endpoints</div>
    <div style="display:flex;flex-direction:column;gap:8px;font-family:'JetBrains Mono',monospace;font-size:0.75rem;">
      <div style="background:var(--bg-card);padding:10px;border-radius:6px;border:1px solid var(--border);">
        <div style="color:var(--text-secondary);margin-bottom:4px;font-family:'Inter',sans-serif;font-weight:600;font-size:0.7rem;">LIVE HLS PROXY (AUTHENTICATED)</div>
        <div style="color:var(--brand-green);word-break:break-all;">${streamUrl}</div>
      </div>
      <div style="background:var(--bg-card);padding:10px;border-radius:6px;border:1px solid var(--border);">
        <div style="color:var(--text-secondary);margin-bottom:4px;font-family:'Inter',sans-serif;font-weight:600;font-size:0.7rem;">AI INFERENCE (RTSP / TCP)</div>
        <div style="color:var(--brand-blue);word-break:break-all;">rtsp://103.250.160.189:8554/stream/${cam.camera_id}</div>
      </div>
    </div>
  `;
  
  lucide.createIcons();
  els.cameraModal.classList.remove("hidden");

  // Mount video via Hls.js with strict LIVE settings
  const video = document.getElementById("modal-hls-video");
  const statusEl = document.getElementById("modal-video-status");

  if (typeof Hls !== "undefined" && Hls.isSupported()) {
    const hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 10,
      liveDurationInfinity: true,
      maxBufferLength: 30,
      maxMaxBufferLength: 60,
      fragLoadingTimeOut: 20000,
      manifestLoadingTimeOut: 20000,
    });
    window.activeModalHls = hls;

    hls.loadSource(streamUrl);
    hls.attachMedia(video);

    hls.on(Hls.Events.MANIFEST_PARSED, function() {
      if (statusEl) statusEl.innerHTML = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#10b981;"></span><span>Live Feed Connected</span>';
      const overlay = document.getElementById('modal-play-overlay');
      video.play().then(() => {
        if (overlay) overlay.style.display = 'none';
      }).catch(() => {
        if (overlay) overlay.style.display = 'flex';
        if (statusEl) statusEl.innerHTML = '<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#f59e0b;"></span><span>Click video to start</span>';
      });
    });

    hls.on(Hls.Events.ERROR, function(event, data) {
      if (data.fatal) {
        if (statusEl) statusEl.innerHTML = `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#ef4444;"></span><span>Reconnecting (${data.type})...</span>`;
        switch (data.type) {
          case Hls.ErrorTypes.NETWORK_ERROR:
            hls.startLoad();
            break;
          case Hls.ErrorTypes.MEDIA_ERROR:
            hls.recoverMediaError();
            break;
          default:
            hls.destroy();
            break;
        }
      }
    });
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = streamUrl;
    video.addEventListener("loadedmetadata", () => {
      video.play();
    });
  }
}

function toggleModalAudio() {
  const video = document.getElementById("modal-hls-video");
  const btn = document.getElementById("modal-audio-btn");
  if (!video || !btn) return;
  video.muted = !video.muted;
  btn.innerHTML = video.muted 
    ? '<i data-lucide="volume-x" style="width:14px;height:14px"></i> Muted'
    : '<i data-lucide="volume-2" style="width:14px;height:14px"></i> Unmuted';
  lucide.createIcons();
}

function toggleModalFullscreen() {
  const container = document.getElementById("modal-video-container");
  if (!container) return;
  if (!document.fullscreenElement) {
    container.requestFullscreen?.().catch(() => {});
  } else {
    document.exitFullscreen?.().catch(() => {});
  }
}

function toggleQuadView(btn) {
  const container = document.getElementById("quad-grid-container");
  const gridContainer = document.getElementById("camera-grid");
  const isOpening = container.style.display === "none";

  if (isOpening) {
    container.style.display = "block";
    gridContainer.style.display = "none";
    document.querySelectorAll('.filter-group button').forEach(b => b.classList.remove('filter-active'));
    btn.classList.add('filter-active');
    renderQuadGrid();
  } else {
    container.style.display = "none";
    gridContainer.style.display = "grid";
    btn.classList.remove('filter-active');
    document.querySelector('[data-filter="all"]')?.classList.add('filter-active');
    destroyQuadGrid();
  }
}

function destroyQuadGrid() {
  if (window.quadHlsInstances) {
    window.quadHlsInstances.forEach(hls => {
      try { hls.destroy(); } catch (e) {}
    });
    window.quadHlsInstances = [];
  }
}

function renderQuadGrid() {
  destroyQuadGrid();
  const quadStreams = document.querySelector(".quad-streams");
  if (!quadStreams) return;

  const targetCams = ["cam01", "cam02", "cam03", "cam04"];
  const cams = targetCams.map(id => allCameras.find(c => c.camera_id === id) || { camera_id: id, location: id });

  quadStreams.innerHTML = cams.map(cam => `
    <div class="quad-card" style="background:var(--bg-card);border:1px solid var(--border);border-radius:10px;overflow:hidden;box-shadow:var(--shadow-md);">
      <div style="padding:8px 12px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);background:rgba(255,255,255,0.02);">
        <div style="display:flex;align-items:center;gap:8px;">
          <span class="status-dot status-live" style="width:6px;height:6px;box-shadow:0 0 6px #10b981;"></span>
          <span style="font-family:'JetBrains Mono',monospace;font-weight:700;color:#fff;font-size:0.8rem;">${cam.camera_id}</span>
          <span style="color:var(--text-secondary);font-size:0.75rem;">${cam.location || ''}</span>
        </div>
        <button class="btn btn-ghost btn-sm" onclick="openCamera('${cam.camera_id}')" style="padding:2px 8px;font-size:0.7rem;">
          <i data-lucide="maximize-2" style="width:12px;height:12px"></i> Expand
        </button>
      </div>
      <div style="aspect-ratio:16/9;background:#000;position:relative;">
        <video id="quad-video-${cam.camera_id}" autoplay muted playsinline style="width:100%;height:100%;object-fit:contain;display:block;pointer-events:none;"></video>
        <div style="position:absolute;top:8px;left:8px;font-size:0.65rem;background:rgba(15,23,42,0.85);backdrop-filter:blur(4px);color:#10b981;font-weight:800;padding:2px 8px;border-radius:4px;border:1px solid rgba(255,255,255,0.1);letter-spacing:0.5px;">
          ● LIVE
        </div>
      </div>
    </div>
  `).join("");

  lucide.createIcons();

  cams.forEach(cam => {
    const video = document.getElementById(`quad-video-${cam.camera_id}`);
    if (!video) return;
    const streamUrl = `${API_BASE}/api/hls/${cam.camera_id}/index.m3u8`;

    if (typeof Hls !== "undefined" && Hls.isSupported()) {
      const hls = new Hls({
        enableWorker: true,
        lowLatencyMode: true,
        liveSyncDurationCount: 3,
        liveMaxLatencyDurationCount: 5,
        liveDurationInfinity: true,
        maxBufferLength: 10,
        maxMaxBufferLength: 20,
      });
      window.quadHlsInstances.push(hls);
      hls.loadSource(streamUrl);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(e => console.log('Autoplay muted'));
      });
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = streamUrl;
      video.addEventListener("loadedmetadata", () => video.play());
    }
  });
  lucide.createIcons();
}

document.getElementById("camera-search")?.addEventListener("input", () => {
  const activeBtn = document.querySelector('.filter-group button.filter-active');
  renderCameras(activeBtn ? activeBtn.dataset.filter : 'all');
});

// ============================================================
// WATCHLIST
// ============================================================

async function loadWatchlist() {
  const table = document.getElementById("watchlist-table");
  
  try {
    const res = await fetch(`${API_BASE}/watchlist`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    
    if (res.ok) {
      const data = await res.json();
      
      if (data.entries.length === 0) {
        table.innerHTML = '<div class="empty-state"><p>Watchlist is empty</p></div>';
        return;
      }
      
      let html = `
        <table>
          <thead>
            <tr>
              <th>Plate</th>
              <th>Status</th>
              <th>Priority</th>
              <th>Description</th>
              <th>Source</th>
              ${currentUser.role === 'ADMIN' ? '<th>Action</th>' : ''}
            </tr>
          </thead>
          <tbody>
      `;
      
      data.entries.forEach(e => {
        let statusBadge = `badge-yellow`;
        if (e.status === 'STOLEN' || e.status === 'WANTED') statusBadge = `badge-red`;
        
        html += `
          <tr>
            <td><code style="font-size:0.9rem">${e.registration_number}</code></td>
            <td><span class="badge ${statusBadge}">${e.status}</span></td>
            <td>${e.priority}</td>
            <td>${e.description || '-'}</td>
            <td><span style="font-size:0.7rem;color:var(--text-muted)">${e.source}</span></td>
            ${currentUser.role === 'ADMIN' ? 
              `<td><button class="btn btn-ghost btn-sm btn-icon" onclick="removeWatchlist('${e.registration_number}')"><i data-lucide="trash-2"></i></button></td>` 
              : ''}
          </tr>
        `;
      });
      
      html += '</tbody></table>';
      table.innerHTML = html;
      lucide.createIcons();
    }
  } catch (e) {
    table.innerHTML = '<div class="empty-state"><p>Failed to load watchlist</p></div>';
  }
}

function showAddWatchlist() {
  if (currentUser.role === "DEPARTMENT_USER") {
    showToast("Insufficient permissions", "error");
    return;
  }
  document.getElementById("watchlist-add-form").classList.remove("hidden");
}

function hideAddWatchlist() {
  document.getElementById("watchlist-add-form").classList.add("hidden");
  document.getElementById("wl-plate").value = "";
  document.getElementById("wl-desc").value = "";
}

async function addWatchlistEntry() {
  const plate = document.getElementById("wl-plate").value;
  const status = document.getElementById("wl-status").value;
  const priority = document.getElementById("wl-priority").value;
  const desc = document.getElementById("wl-desc").value;
  
  if (!plate) {
    showToast("Registration number is required", "error");
    return;
  }
  
  try {
    const res = await fetch(`${API_BASE}/watchlist`, {
      method: "POST",
      headers: { 
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}` 
      },
      body: JSON.stringify({
        registration_number: plate,
        status: status,
        priority: priority,
        description: desc
      })
    });
    
    if (res.ok) {
      showToast(`Added ${plate} to watchlist`, "success");
      hideAddWatchlist();
      loadWatchlist();
      loadOverview();
    } else {
      showToast("Failed to add entry", "error");
    }
  } catch (e) {
    showToast("Network error", "error");
  }
}

async function removeWatchlist(plate) {
  if (!confirm(`Remove ${plate} from watchlist?`)) return;
  
  try {
    const res = await fetch(`${API_BASE}/watchlist/${plate}`, {
      method: "DELETE",
      headers: { "Authorization": `Bearer ${token}` }
    });
    
    if (res.ok) {
      showToast(`Removed ${plate}`, "success");
      loadWatchlist();
    }
  } catch (e) {
    showToast("Network error", "error");
  }
}

// ============================================================
// AUDIT
// ============================================================

async function loadAudit() {
  const table = document.getElementById("audit-table");
  
  try {
    const res = await fetch(`${API_BASE}/audit-logs?limit=50`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    
    if (res.ok) {
      const data = await res.json();
      
      let html = `
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>User</th>
              <th>Action</th>
              <th>Resource</th>
              <th>Outcome</th>
            </tr>
          </thead>
          <tbody>
      `;
      
      data.logs.forEach(l => {
        const time = new Date(l.timestamp).toLocaleTimeString("en-IN", {hour12: false}) + " " + new Date(l.timestamp).toLocaleDateString("en-IN");
        let outcomeBadge = 'badge-green';
        if (l.outcome === 'FAILURE') outcomeBadge = 'badge-red';
        if (l.outcome === 'DENIED') outcomeBadge = 'badge-yellow';
        
        let resourceStr = '-';
        if (l.resource_type) {
          resourceStr = `${l.resource_type}: <code style="font-size:0.75rem">${l.resource_id}</code>`;
        }
        
        html += `
          <tr>
            <td style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;color:var(--text-secondary)">${time}</td>
            <td style="font-weight:600">${l.username || 'system'}</td>
            <td><span style="font-size:0.8rem;background:rgba(255,255,255,0.05);padding:2px 6px;border-radius:4px">${l.action}</span></td>
            <td>${resourceStr}</td>
            <td><span class="badge ${outcomeBadge}">${l.outcome}</span></td>
          </tr>
        `;
      });
      
      html += '</tbody></table>';
      table.innerHTML = html;
    }
  } catch (e) {
    table.innerHTML = '<div class="empty-state"><p>Failed to load audit logs</p></div>';
  }
}

// ============================================================
// GIS MAP & ROUTE TRACKING (Nullable coords & INFERRED ROUTE)
// ============================================================

let googleMap = null;
let mapMarkers = [];
let mapPolylines = [];

async function loadMapTab() {
  const container = document.getElementById("gmap");
  if (!container) return;

  try {
    const res = await fetch(`${API_BASE}/api/config/maps`);
    const cfg = await res.json();

    if (cfg.has_google_maps && cfg.google_maps_api_key) {
      if (!window.google || !window.google.maps) {
        if (!document.getElementById("gmaps-sdk")) {
          window.onGoogleMapsLoaded = () => initGoogleMapInstance();
          const script = document.createElement("script");
          script.id = "gmaps-sdk";
          script.src = `https://maps.googleapis.com/maps/api/js?key=${cfg.google_maps_api_key}&callback=onGoogleMapsLoaded`;
          script.async = true;
          script.defer = true;
          document.head.appendChild(script);
        }
      } else {
        initGoogleMapInstance();
      }
    } else {
      renderSchematicMap(container, "Google Maps API Key not provided. Displaying Sentinel Tactical Grid.");
    }
  } catch (err) {
    renderSchematicMap(container, "Could not load map configuration. Displaying Tactical Grid.");
  }
}

function initGoogleMapInstance() {
  const container = document.getElementById("gmap");
  if (!container || !window.google || !window.google.maps) return;

  container.innerHTML = "";
  googleMap = new google.maps.Map(container, {
    center: { lat: 23.0225, lng: 72.5714 }, // Ahmedabad / Gujarat center
    zoom: 11,
    styles: [
      { elementType: "geometry", stylers: [{ color: "#242f3e" }] },
      { elementType: "labels.text.stroke", stylers: [{ color: "#242f3e" }] },
      { elementType: "labels.text.fill", stylers: [{ color: "#746855" }] },
      { featureType: "road", elementType: "geometry", stylers: [{ color: "#38414e" }] },
      { featureType: "water", elementType: "geometry", stylers: [{ color: "#17263c" }] }
    ]
  });
  window.googleMapInstance = googleMap;

  // Render camera markers
  mapMarkers = [];
  let mappedCount = 0;
  let unmappedCount = 0;

  cachedCameras.forEach(cam => {
    if (cam.latitude && cam.longitude) {
      mappedCount++;
      const marker = new google.maps.Marker({
        position: { lat: cam.latitude, lng: cam.longitude },
        map: googleMap,
        title: `${cam.camera_id}: ${cam.location || 'Sentinel Camera'}`,
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 7,
          fillColor: cam.live_status === true ? "#10b981" : (cam.live_status === false ? "#ef4444" : "#f59e0b"),
          fillOpacity: 0.9,
          strokeWeight: 2,
          strokeColor: "#ffffff"
        }
      });

      const info = new google.maps.InfoWindow({
        content: `
          <div style="color:#0f172a;font-family:sans-serif;padding:6px;max-width:220px;">
            <strong style="font-size:0.95rem;">${cam.camera_id}</strong>
            <p style="margin:4px 0;font-size:0.8rem;color:#475569;">${cam.location || 'Location unassigned'}</p>
            <div style="margin-top:8px;">
              <button onclick="openCamera('${cam.camera_id}')" style="background:#2563eb;color:#fff;border:none;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:0.75rem;">View Live Stream</button>
            </div>
          </div>
        `
      });

      marker.addListener("click", () => info.open(googleMap, marker));
      mapMarkers.push(marker);
    } else {
      unmappedCount++;
    }
  });

  // Display unmapped coordinates alert if any cameras have null lat/lon
  const statusEl = document.getElementById("map-status-text");
  if (statusEl) {
    statusEl.innerHTML = `Mapped Cameras: ${mappedCount} | Unmapped Coordinates: ${unmappedCount} (Sentinel cameras.json)`;
  }
}

function renderSchematicMap(container, note) {
  const mapped = cachedCameras.filter(c => c.latitude && c.longitude);
  const unmapped = cachedCameras.filter(c => !c.latitude || !c.longitude);

  container.innerHTML = `
    <div style="padding:24px;background:var(--bg-card);border-radius:var(--radius-md);height:100%;display:flex;flex-direction:column;gap:16px;">
      <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:12px;">
        <div>
          <h3 style="margin:0;font-size:1.1rem;display:flex;align-items:center;gap:8px;">
            <i data-lucide="map" style="color:var(--brand-blue)"></i> Sentinel Gujarat Tactical Schematic
          </h3>
          <p style="margin:4px 0 0;font-size:0.8rem;color:var(--text-secondary);">${note}</p>
        </div>
        <span class="badge badge-blue">Schematic Grid Mode</span>
      </div>

      <div style="background:var(--bg-dark);padding:16px;border-radius:var(--radius-md);border:1px solid var(--border);display:flex;gap:12px;align-items:center;">
        <span class="legend-item"><span class="legend-dot legend-camera"></span> Online Camera</span>
        <span class="legend-item"><span class="legend-dot" style="background:#ef4444"></span> Offline</span>
        <span class="legend-item"><span class="legend-line legend-inferred"></span> INFERRED ROUTE (Estimated interpolation)</span>
      </div>

      <div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(220px, 1fr));gap:12px;overflow-y:auto;flex:1;padding-right:8px;">
        ${cachedCameras.map(c => `
          <div style="background:rgba(255,255,255,0.03);border:1px solid var(--border);padding:12px;border-radius:var(--radius-sm);display:flex;flex-direction:column;justify-content:space-between;">
            <div>
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <strong style="font-family:'JetBrains Mono',monospace;color:var(--brand-blue);">${c.camera_id}</strong>
                <span class="badge ${c.live_status === true ? 'badge-green' : (c.live_status === false ? 'badge-red' : 'badge-yellow')}">${c.live_status === true ? 'LIVE' : (c.live_status === false ? 'OFFLINE' : 'UNKNOWN')}</span>
              </div>
              <div style="font-size:0.8rem;color:var(--text-secondary);line-height:1.3;margin-bottom:8px;">${c.location || 'Location unassigned'}</div>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;padding-top:8px;border-top:1px solid rgba(255,255,255,0.05);">
              <span style="font-size:0.7rem;color:var(--text-muted);">${c.latitude ? `${c.latitude.toFixed(2)}, ${c.longitude.toFixed(2)}` : 'Coords: NULL'}</span>
              <button class="btn btn-ghost btn-xs" onclick="openCamera('${c.camera_id}')">Feed</button>
            </div>
          </div>
        `).join('')}
      </div>
    </div>
  `;
  lucide.createIcons();
}

async function trackOnMap() {
  const input = document.getElementById("map-plate-search");
  const plate = (input ? input.value : "").trim().toUpperCase();
  if (!plate) {
    showToast("Please enter a plate to track", "warning");
    return;
  }

  showToast(`Tracking route for plate: ${plate}...`, "info");

  try {
    const res = await fetch(`${API_BASE}/investigate/${plate}`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (!res.ok) {
      showToast(`No confirmed sightings found for ${plate}`, "info");
      return;
    }
    const data = await res.json();
    const sightings = data.sightings || [];

    if (sightings.length === 0) {
      showToast(`No confirmed sightings recorded for ${plate}`, "info");
      return;
    }

    showToast(`Found ${sightings.length} confirmed observation(s). Rendering INFERRED ROUTE.`, "success");

    // Clear existing polylines
    mapPolylines.forEach(p => p.setMap(null));
    mapPolylines = [];

    if (googleMap && window.google && window.google.maps) {
      const pathCoords = [];
      sightings.forEach(s => {
        const cam = cachedCameras.find(c => c.camera_id === s.camera_id);
        if (cam && cam.latitude && cam.longitude) {
          pathCoords.push({ lat: cam.latitude, lng: cam.longitude });
        }
      });

      if (pathCoords.length > 1) {
        const routeLine = new google.maps.Polyline({
          path: pathCoords,
          geodesic: true,
          strokeColor: "#f59e0b",
          strokeOpacity: 0.8,
          strokeWeight: 4,
          icons: [{
            icon: { path: "M 0,-1 0,1", strokeOpacity: 1, scale: 3 },
            offset: "0",
            repeat: "16px"
          }],
          map: googleMap
        });
        mapPolylines.push(routeLine);
        googleMap.setCenter(pathCoords[0]);
      }
    }
  } catch (e) {
    showToast("Failed to retrieve plate route", "error");
  }
}

function closeModal(id) {
  if (id === "camera-modal") {
    if (window.activeModalHls) {
      try { window.activeModalHls.destroy(); } catch (e) {}
      window.activeModalHls = null;
    }
    const video = document.getElementById("modal-hls-video");
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
  }
  document.getElementById(id).classList.add("hidden");
}

function formatTime(isoStr) {
  if (!isoStr) return "";
  const d = new Date(isoStr);
  return d.toLocaleTimeString("en-IN", { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function getConfColor(conf) {
  if (conf >= 0.85) return "#6ee7b7"; // green
  if (conf >= 0.65) return "#fcd34d"; // yellow
  return "#fca5a5"; // red
}

function showToast(msg, type = "info", duration = 4000) {
  const toast = document.createElement("div");
  toast.className = `toast toast-${type}`;
  
  let icon = "info";
  if (type === "success") icon = "check-circle";
  if (type === "error") icon = "alert-circle";
  if (type === "warning") icon = "alert-triangle";
  
  toast.innerHTML = `
    <i data-lucide="${icon}" class="toast-icon"></i>
    <div class="toast-text">${msg}</div>
  `;
  
  els.toastContainer.appendChild(toast);
  lucide.createIcons();
  
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateX(20px)";
    toast.style.transition = "all 0.3s ease";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}

// Global scope expose for inline handlers
window.filterCameras = filterCameras;
window.filterAlerts = filterAlerts;
window.switchTab = switchTab;
window.loadAlerts = loadAlerts;
window.openAlert = openAlert;
window.closeModal = closeModal;
window.acknowledgeCurrentAlert = acknowledgeCurrentAlert;
window.investigateFromAlert = investigateFromAlert;
window.investigatePlate = investigatePlate;
window.setPlate = setPlate;
window.trackOnMap = trackOnMap;
window.openCamera = openCamera;
window.showAddWatchlist = showAddWatchlist;
window.hideAddWatchlist = hideAddWatchlist;
window.addWatchlistEntry = addWatchlistEntry;
window.removeWatchlist = removeWatchlist;

// BOOT
init();
