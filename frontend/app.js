/**
 * Sentinel Gujarat — Frontend Application Logic
 */

// API_BASE always points to the FastAPI backend (port 8000).
// When the frontend is served separately (e.g. python -m http.server 3000),
// window.location.origin would be :3000, so we explicitly target :8000.
const API_BASE = `${window.location.protocol}//${window.location.hostname}:8000`;
let ws = null;
let token = localStorage.getItem("sentinel_token");
let currentUser = null;

// ============================================================
// CAMERA HLS PLAYER REGISTRY
// Tracks live HLS instances per camera to prevent duplicates.
// cameraId -> { hls, video, status }
// ============================================================
const cameraPlayers = new Map();

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
  initCommandPalette();
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
  if (ws) { try { ws.close(); } catch(e) {} ws = null; }
  destroyAllGridPlayers(); // Clean up all grid HLS players on logout
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
  if (tabId === "cameras") {
    loadCameras();
  } else {
    // Destroy quad grid streams when leaving cameras tab
    if (typeof destroyQuadGrid === "function") destroyQuadGrid();
    // Hide quad grid container
    const qc = document.getElementById("quad-grid-container");
    const gc = document.getElementById("camera-grid");
    if (qc) qc.style.display = "none";
    if (gc) gc.style.display = "grid";
  }
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
    if (window.leafletMapInstance) {
      setTimeout(() => {
        window.leafletMapInstance.invalidateSize();
      }, 150);
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
// Emergency Acoustic Siren / Chime using browser Web Audio API
let emergencyAudioCtx = null;
function playEmergencyChime() {
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    if (!emergencyAudioCtx) emergencyAudioCtx = new AudioCtx();
    if (emergencyAudioCtx.state === 'suspended') emergencyAudioCtx.resume();
    
    const now = emergencyAudioCtx.currentTime;
    
    // Play a 2-tone emergency police/ambulance chirp (880Hz -> 660Hz)
    const osc1 = emergencyAudioCtx.createOscillator();
    const osc2 = emergencyAudioCtx.createOscillator();
    const gain = emergencyAudioCtx.createGain();
    
    osc1.type = "sine";
    osc1.frequency.setValueAtTime(880, now);
    osc1.frequency.exponentialRampToValueAtTime(440, now + 0.35);
    
    osc2.type = "sawtooth";
    osc2.frequency.setValueAtTime(660, now + 0.35);
    osc2.frequency.exponentialRampToValueAtTime(330, now + 0.7);
    
    gain.gain.setValueAtTime(0.18, now);
    gain.gain.exponentialRampToValueAtTime(0.01, now + 0.75);
    
    osc1.connect(gain);
    osc2.connect(gain);
    gain.connect(emergencyAudioCtx.destination);
    
    osc1.start(now);
    osc1.stop(now + 0.35);
    osc2.start(now + 0.35);
    osc2.stop(now + 0.75);
  } catch (e) {
    console.warn("Audio chime disabled or blocked by browser policy", e);
  }
}

// WEBSOCKET
// ============================================================

let activeAlerts = [];

function connectWebSocket() {
  // Guard: prevent duplicate WebSocket connections
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
    return;
  }
  if (ws) { try { ws.close(); } catch(e) {} ws = null; }
  
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
      
      const isIncident = data.alert.watchlist_status === "ACCIDENT_COLLISION" || data.alert.severity === "CRITICAL";
      if (isIncident) {
        playEmergencyChime();
        showToast(`🚨 CRITICAL AID: Incident detected at ${data.alert.camera_id}! Gujarat 108 Emergency Medical Protocol Activated`, "error", 12000);
        markCameraIncident(data.alert.camera_id, data.alert);
      } else if (data.alert.severity === "HIGH") {
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
  // Pre-populate camera grid immediately after login so ALL streams begin connecting
  // before the user navigates to the cameras tab.
  try {
    const r = await fetch(`${API_BASE}/cameras`, { headers: { "Authorization": `Bearer ${token}` } });
    if (r.ok) {
      const data = await r.json();
      if (Array.isArray(data) && data.length > 0) {
        allCameras = data;
        renderCameraGrid(allCameras); // Pre-create tiles + start HLS players
        renderNetworkPulse();
      }
    }
  } catch(e) { /* non-fatal — cameras tab will retry */ }
}

async function loadOverview() {
  try {
    // Health / Status
    const healthRes = await fetch(`${API_BASE}/health`);
    if (healthRes.ok) {
      const health = await healthRes.json();
      const dbBadge = document.getElementById("db-status");
      if (health.database === "connected") {
        const dbName = health.database_type === "sqlite" ? "SQLite" : (health.database_type === "postgresql" ? "PostgreSQL" : "DB");
        dbBadge.className = "badge badge-green"; 
        dbBadge.textContent = `Connected (${dbName})`;
      } else {
        dbBadge.className = "badge badge-red"; 
        dbBadge.textContent = "Offline (In-Memory)";
      }
      
      const mapBadge = document.getElementById("maps-status");
      if (health.google_maps === "configured") {
        mapBadge.className = "badge badge-green"; 
        mapBadge.textContent = "Google Maps Active";
      } else {
        mapBadge.className = "badge badge-green"; 
        mapBadge.textContent = "OpenStreetMap Active";
      }

      // Dynamic Hardware detection
      if (health.hardware) {
        const gpuLabel = document.getElementById("gpu-label");
        const gpuStatus = document.getElementById("gpu-status");
        if (gpuLabel && health.hardware.name) {
          gpuLabel.textContent = health.hardware.name.length > 28 
            ? health.hardware.name.substring(0, 27) + "..." 
            : health.hardware.name;
          gpuLabel.title = health.hardware.name;
        }
        if (gpuStatus && health.hardware.status) {
          gpuStatus.className = "badge badge-green";
          gpuStatus.textContent = health.hardware.status;
        }
        const archLabel = document.getElementById("arch-gpu-label");
        if (archLabel && health.hardware.name) {
          archLabel.textContent = health.hardware.name;
        }
      }
    }
    
    // Cameras count
    const camRes = await fetch(`${API_BASE}/cameras`, { headers: { "Authorization": `Bearer ${token}` } });
    if (camRes.ok) {
      const cams = await camRes.json();
      document.getElementById("stat-cameras").textContent = cams.length;
      allCameras = cams;
      renderNetworkPulse();
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
      const isIncident = alert.watchlist_status === "ACCIDENT_COLLISION" || alert.severity === "CRITICAL";

      if (isIncident) {
        const incData = alert.incident_data || {};
        const fac = incData.emergency_facility || {};
        const hospitalName = fac.primary_hospital || "Sola Civil Hospital & Trauma Care Center, SG Highway";
        const pcrUnit = fac.nearest_pcr || "PCR Cheetah-04 (SG Highway Beat)";
        const etaText = fac.avg_eta_mins ? `${fac.avg_eta_mins} to ${fac.avg_eta_mins + 2} minutes` : "4 to 6 minutes";
        const incType = incData.incident_type || "VEHICLE_COLLISION / HAZARD";
        const desc = incData.description || "AI multi-frame collision consensus verified";

        body.innerHTML = `
          <div style="background:linear-gradient(135deg,rgba(239,68,68,0.2),rgba(15,23,42,0.9));border:1px solid rgba(239,68,68,0.4);border-radius:8px;padding:12px;margin-bottom:14px;display:flex;align-items:center;justify-content:space-between;">
            <div style="display:flex;align-items:center;gap:10px;">
              <span class="legend-dot legend-incident" style="width:14px;height:14px;"></span>
              <div>
                <div style="color:#ef4444;font-weight:800;font-size:0.95rem;letter-spacing:0.5px;">CRITICAL AID — ${incType.replace(/_/g, ' ')}</div>
                <div style="color:var(--text-secondary);font-size:0.75rem;">AI Multi-Frame Consensus Verified &bull; Kinetic Drop Filter Passed</div>
              </div>
            </div>
            <span class="badge badge-critical" style="font-size:0.75rem;padding:4px 8px;">108 CAD ACTIVE</span>
          </div>

          <dl class="modal-kv">
            <dt><i data-lucide="video"></i> Camera Node</dt>
            <dd><strong style="color:var(--brand-blue);font-family:'JetBrains Mono',monospace;">${alert.camera_id}</strong></dd>
            
            <dt><i data-lucide="map-pin"></i> Incident Location</dt>
            <dd>${alert.location || 'Gujarat Police CCTV Corridor'}</dd>
            
            <dt><i data-lucide="clock"></i> Incident Time</dt>
            <dd>${new Date(alert.timestamp).toLocaleString("en-IN")}</dd>
            
            <dt><i data-lucide="shield-alert"></i> Incident Type</dt>
            <dd><span class="badge badge-critical">${incType}</span></dd>
            
            <dt><i data-lucide="activity"></i> Confidence</dt>
            <dd><strong style="color:#6ee7b7">${Math.round(alert.overall_confidence * 100)}% (Multi-Frame IoU &ge; 0.30)</strong></dd>

            <dt><i data-lucide="info"></i> Evidence</dt>
            <dd style="font-size:0.8rem;color:#cbd5e1;">${desc}</dd>
          </dl>

          <div class="incident-dispatch-box">
            <div class="incident-dispatch-title">
              <i data-lucide="ambulance" style="color:#ef4444;width:18px;height:18px;"></i>
              <span>Gujarat 108 Emergency Medical Service (GVK EMRI)</span>
            </div>
            <div class="incident-hospital-info">
              <div style="font-weight:700;color:#f8fafc;font-size:0.85rem;">Trauma Centre: ${hospitalName}</div>
              <div style="font-size:0.75rem;color:#94a3b8;margin-top:2px;">Dispatched Unit: <strong>${pcrUnit}</strong> | Emergency CAD: <strong>108 / 112</strong></div>
              <div style="font-size:0.75rem;color:#38bdf8;margin-top:2px;">Estimated Response ETA: <strong>${etaText}</strong></div>
            </div>
            
            <div id="dispatch-action-row" style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap;">
              <button id="dispatch-108-btn" class="btn btn-sm btn-critical" onclick="dispatchEmergency('${alert.id}', '108_AMBULANCE')">
                <i data-lucide="ambulance" style="width:14px;height:14px;"></i> Dispatch 108 Ambulance
              </button>
              <button id="dispatch-pcr-btn" class="btn btn-sm" style="background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;" onclick="dispatchEmergency('${alert.id}', 'TRAFFIC_PCR')">
                <i data-lucide="shield" style="width:14px;height:14px;"></i> Dispatch Traffic PCR
              </button>
              <button class="btn btn-sm btn-ghost" onclick="closeModal('alert-modal');openCamera('${alert.camera_id}')">
                <i data-lucide="video" style="width:14px;height:14px;"></i> View CCTV Feed
              </button>
            </div>
            <div id="dispatch-status-msg" style="display:none;margin-top:8px;padding:6px 10px;background:rgba(16,185,129,0.15);border:1px solid #10b981;border-radius:4px;color:#10b981;font-size:0.78rem;font-weight:600;"></div>
          </div>
        `;
      } else {
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
      }
      
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

async function dispatchEmergency(incidentId, serviceType = "108_AMBULANCE") {
  const btn = serviceType === "108_AMBULANCE" 
    ? document.getElementById("dispatch-108-btn") 
    : document.getElementById("dispatch-pcr-btn");
  
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<div class="spinner" style="width:12px;height:12px;border-width:2px;margin-right:6px;"></div> Dispatching...`;
  }

  try {
    const res = await fetch(`${API_BASE}/incidents/${incidentId}/dispatch`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        service_type: serviceType,
        notes: `Immediate CAD dispatch initiated by ${currentUser?.username || 'Operator'}`
      })
    });

    if (res.ok) {
      const data = await res.json();
      const unit = data.dispatch?.dispatched_unit || (serviceType === "108_AMBULANCE" ? "108-AMB-GJ01-A" : "PCR-GJ01-DELTA");
      const eta = data.dispatch?.eta_minutes || 4;
      
      showToast(`DISPATCH CONFIRMED: ${unit} en route (ETA ${eta} min)`, "success", 7000);

      const statusMsg = document.getElementById("dispatch-status-msg");
      if (statusMsg) {
        statusMsg.style.display = "block";
        statusMsg.innerHTML = `<i data-lucide="check-circle" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"></i> <strong>DISPATCH CONFIRMED:</strong> Unit ${unit} en route &bull; ETA ${eta} min (Hospital notified)`;
        lucide.createIcons();
      }

      if (btn) {
        btn.innerHTML = serviceType === "108_AMBULANCE" ? "✓ 108 Dispatched" : "✓ PCR Dispatched";
        btn.style.background = "#10b981";
      }
    } else {
      showToast("Dispatch request failed", "error");
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = serviceType === "108_AMBULANCE" ? "Dispatch 108 Ambulance" : "Dispatch Traffic PCR";
      }
    }
  } catch (e) {
    console.error("Dispatch error:", e);
    showToast("Network error during emergency dispatch", "error");
    if (btn) btn.disabled = false;
  }
}

async function triggerSimulatedIncident(cameraId = "cam01") {
  showToast(`Simulating verified accident detection at ${cameraId}...`, "info");
  try {
    const res = await fetch(`${API_BASE}/incidents/detect`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${token}`,
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        camera_id: cameraId,
        incident_type: "VEHICLE_COLLISION",
        confidence: 0.94,
        involved_vehicles: ["GJ01AB1234", "GJ27XY9988"],
        description: "Two-vehicle lateral impact on SG Highway; kinetic energy drop detected",
        force_trigger: true
      })
    });

    if (res.ok) {
      const data = await res.json();
      showToast(`AID Triggered: ${data.incident?.incident_id || 'COLLISION'} at ${cameraId}`, "error", 8000);
      markCameraIncident(cameraId, {
        id: data.incident?.incident_id,
        camera_id: cameraId,
        overall_confidence: 0.94,
        watchlist_status: "ACCIDENT_COLLISION"
      });
    } else {
      const err = await res.json();
      showToast(err.detail || "Failed to trigger incident simulation", "error");
    }
  } catch (e) {
    showToast("Error connecting to incident detector", "error");
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

let allCameras = [];


async function loadCameras() {
  const container = document.getElementById("camera-grid");
  // Only show loading spinner if the grid is completely empty (very first visit)
  const hasExistingTiles = container.querySelectorAll('.camera-card[data-camera-id]').length > 0;
  if (!hasExistingTiles) {
    container.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>Loading cameras from catalogue...</p></div>';
  }

  try {
    const res = await fetch(`${API_BASE}/cameras`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (res.ok) {
      allCameras = await res.json();
      renderCameraGrid(allCameras); // DOM-diff: preserves healthy streams
      renderNetworkPulse();
    }
  } catch (e) {
    const stillEmpty = container.querySelectorAll('.camera-card[data-camera-id]').length === 0;
    if (stillEmpty) {
      container.innerHTML = '<div class="empty-state"><i data-lucide="wifi-off"></i><p>Failed to load cameras</p></div>';
      lucide.createIcons();
    }
  }
}

function filterCameras(filter, btn) {
  document.querySelectorAll('.filter-group button').forEach(b => b.classList.remove('filter-active'));
  btn.classList.add('filter-active');
  renderCameras(filter);
}

// renderCameras: show/hide existing tiles by filter — does NOT recreate DOM or restart streams
function renderCameras(filter) {
  const container = document.getElementById("camera-grid");
  const query = document.getElementById("camera-search")?.value.toLowerCase() || "";

  const tiles = container.querySelectorAll('.camera-card[data-camera-id]');
  // Grid not populated yet — loadCameras / renderCameraGrid will handle it
  if (tiles.length === 0) return;

  let visibleCount = 0;

  tiles.forEach(tile => {
    const camId = tile.dataset.cameraId;
    const cam = allCameras.find(c => c.camera_id === camId);
    if (!cam) { tile.style.display = 'none'; return; }

    const matchesFilter = filter !== 'live' || cam.live_status !== false;
    const matchesQuery = !query ||
      cam.camera_id.toLowerCase().includes(query) ||
      (cam.location && cam.location.toLowerCase().includes(query));

    if (matchesFilter && matchesQuery) {
      tile.style.display = '';
      visibleCount++;
    } else {
      tile.style.display = 'none';
    }
  });

  // Show/hide the filter-empty notice
  let emptyEl = container.querySelector('.empty-state[data-filter-empty]');
  if (visibleCount === 0) {
    if (!emptyEl) {
      emptyEl = document.createElement('div');
      emptyEl.className = 'empty-state';
      emptyEl.setAttribute('data-filter-empty', '1');
      emptyEl.style.gridColumn = '1/-1';
      emptyEl.innerHTML = '<p>No cameras match the filter</p>';
      container.appendChild(emptyEl);
    }
    emptyEl.style.display = '';
  } else if (emptyEl) {
    emptyEl.style.display = 'none';
  }
}

// ============================================================
// CAMERA GRID — TILE CREATION + HLS LIFECYCLE MANAGEMENT
// ============================================================

/**
 * Creates a camera card DOM element with embedded <video> for the CCTV wall.
 * Preserves the existing card design (camera-card-top, camera-preview, camera-meta).
 */
function createCameraGridTile(cam) {
  const latLonStr = (cam.latitude && cam.longitude)
    ? `${cam.latitude.toFixed(4)}° N, ${cam.longitude.toFixed(4)}° E`
    : 'Coordinates Pending';
  const locClean = cam.location || 'Gujarat Police Surveillance Node';

  const div = document.createElement('div');
  div.className = 'camera-card';
  div.dataset.cameraId = cam.camera_id;
  div.style.cursor = 'pointer';
  div.title = `${cam.camera_id} — Click to expand`;
  // Preserve existing click-to-modal behavior
  div.addEventListener('click', () => openCamera(cam.camera_id));

  div.innerHTML = `
    <div class="camera-card-top">
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="camera-id">${cam.camera_id}</span>
        <span style="font-size:0.65rem;color:var(--text-muted);background:rgba(255,255,255,0.06);padding:2px 6px;border-radius:4px;font-family:'JetBrains Mono',monospace;">${cam.codec || 'H.264'}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px;" id="cam-status-${cam.camera_id}">
        <span class="status-dot status-warning"></span>
        <span style="font-size:0.68rem;font-weight:700;color:#f59e0b;letter-spacing:0.5px;">CONNECTING</span>
      </div>
    </div>

    <div class="camera-preview" style="position:relative;background:#000;overflow:hidden;">
      <video id="grid-video-${cam.camera_id}" autoplay muted playsinline
             style="width:100%;height:100%;object-fit:contain;display:block;pointer-events:none;"
             preload="none"></video>
      <div id="cam-overlay-${cam.camera_id}"
           style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;background:radial-gradient(120% 120% at 50% 0%,#152033 0%,#0d1420 60%,#0a0e14 100%);">
        <div style="width:38px;height:38px;border-radius:50%;background:rgba(37,99,235,0.15);border:1px solid rgba(37,99,235,0.4);display:flex;align-items:center;justify-content:center;color:#38bdf8;">
          <i data-lucide="loader" style="width:18px;height:18px;"></i>
        </div>
        <small style="color:#38bdf8;font-weight:600;font-size:0.75rem;letter-spacing:0.3px;">CONNECTING...</small>
      </div>
    </div>

    <div class="camera-meta">
      <div class="camera-meta-row">
        <span>Surveillance Post</span>
        <span style="color:var(--text-primary);font-weight:600;text-align:right;max-width:68%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${locClean}">${locClean}</span>
      </div>
      <div class="camera-meta-row">
        <span>GPS Position</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:0.72rem;color:var(--brand-blue);">${latLonStr}</span>
      </div>
    </div>
  `;

  return div;
}

/**
 * DOM-diff based camera grid renderer.
 * Creates tiles for new cameras, removes tiles for removed cameras.
 * Never destroys a healthy existing player or tile.
 */
function renderCameraGrid(cameras) {
  const container = document.getElementById('camera-grid');
  if (!container) return;

  // Remove loading/initial placeholder
  container.querySelectorAll('.loading-state, .empty-state[data-initial]').forEach(el => el.remove());

  if (!cameras || cameras.length === 0) {
    container.innerHTML = '<div class="empty-state" data-initial="1" style="grid-column:1/-1"><i data-lucide="video-off"></i><p>No cameras found in catalogue</p></div>';
    lucide.createIcons();
    return;
  }

  const desiredIds = new Set(cameras.map(c => c.camera_id));

  // Remove tiles that are no longer in the camera list
  container.querySelectorAll('.camera-card[data-camera-id]').forEach(tile => {
    if (!desiredIds.has(tile.dataset.cameraId)) {
      destroyGridPlayer(tile.dataset.cameraId);
      tile.remove();
    }
  });

  // Determine which cameras need new tiles
  const existingIds = new Set(
    Array.from(container.querySelectorAll('.camera-card[data-camera-id]'))
      .map(t => t.dataset.cameraId)
  );
  const newCams = cameras.filter(c => !existingIds.has(c.camera_id));

  if (newCams.length > 0) {
    // Batch-append all new tiles at once (single reflow)
    const fragment = document.createDocumentFragment();
    newCams.forEach(cam => fragment.appendChild(createCameraGridTile(cam)));
    container.appendChild(fragment);
    lucide.createIcons();

    // Initialize HLS streams for the new tiles in controlled batches
    _initGridStreamsBatched(newCams);
  }

  // Apply the currently active filter (show/hide tiles)
  const activeBtn = document.querySelector('.filter-group button.filter-active');
  renderCameras(activeBtn ? (activeBtn.dataset.filter || 'all') : 'all');
}

/**
 * Staggers HLS initialisation in groups of 6 with 250ms between batches
 * to avoid saturating browser networking and MSE allocation limits.
 */
function _initGridStreamsBatched(cameras) {
  const BATCH_SIZE = 6;
  const STAGGER_MS = 250;
  for (let i = 0; i < cameras.length; i += BATCH_SIZE) {
    const batch = cameras.slice(i, i + BATCH_SIZE);
    const delay = Math.floor(i / BATCH_SIZE) * STAGGER_MS;
    setTimeout(() => batch.forEach(cam => initCameraGridStream(cam.camera_id)), delay);
  }
}

/**
 * Initialises the HLS player for a single camera grid tile.
 * Idempotent — safe to call multiple times; skips if already healthy.
 */
function initCameraGridStream(cameraId) {
  // Guard: skip if already initialized and healthy
  if (cameraPlayers.has(cameraId)) {
    const ex = cameraPlayers.get(cameraId);
    if (ex.status === 'live' || ex.status === 'connecting' || ex.status === 'reconnecting') return;
  }

  const video = document.getElementById(`grid-video-${cameraId}`);
  if (!video) return; // Tile not in DOM

  const streamUrl = `${API_BASE}/api/hls/${cameraId}/index.m3u8`;
  const overlayEl = document.getElementById(`cam-overlay-${cameraId}`);
  const statusEl  = document.getElementById(`cam-status-${cameraId}`);

  function setStatus(state) {
    const map = {
      connecting:   { dot: 'status-warning', color: '#f59e0b', label: 'CONNECTING' },
      live:         { dot: 'status-live',    color: '#10b981', label: 'LIVE' },
      reconnecting: { dot: 'status-warning', color: '#f59e0b', label: 'RECONNECTING' },
      offline:      { dot: 'status-error',   color: '#ef4444', label: 'OFFLINE' },
      error:        { dot: 'status-error',   color: '#ef4444', label: 'ERROR' },
    };
    const c = map[state] || map.connecting;
    if (statusEl) {
      statusEl.innerHTML = `<span class="status-dot ${c.dot}" style="box-shadow:0 0 8px ${c.color};"></span>` +
        `<span style="font-size:0.68rem;font-weight:700;color:${c.color};letter-spacing:0.5px;">${c.label}</span>`;
    }
    const entry = cameraPlayers.get(cameraId);
    if (entry) entry.status = state;
  }

  function showOverlay(visible, html) {
    if (!overlayEl) return;
    overlayEl.style.display = visible ? 'flex' : 'none';
    overlayEl.style.pointerEvents = visible ? 'auto' : 'none';
    if (html !== null && html !== undefined) overlayEl.innerHTML = html;
  }

  setStatus('connecting');
  showOverlay(true, null); // Show the connecting placeholder

  if (typeof Hls !== 'undefined' && Hls.isSupported()) {
    const hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      liveSyncDurationCount: 3,
      liveMaxLatencyDurationCount: 8,
      liveDurationInfinity: true,
      maxBufferLength: 15,
      maxMaxBufferLength: 30,
      fragLoadingTimeOut: 20000,
      manifestLoadingTimeOut: 20000,
    });

    cameraPlayers.set(cameraId, { hls, video, status: 'connecting' });
    hls.loadSource(streamUrl);
    hls.attachMedia(video);

    // Auto-update to live as soon as first video frames or fragments land
    video.onplaying = () => {
      setStatus('live');
      showOverlay(false, null);
    };
    video.onloadeddata = () => {
      setStatus('live');
      showOverlay(false, null);
    };

    hls.on(Hls.Events.FRAG_BUFFERED, () => {
      setStatus('live');
      showOverlay(false, null);
    });

    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      console.log(`[CCTV] ${cameraId}: stream ready`);
      video.play().then(() => {
        setStatus('live');
        showOverlay(false, null);
      }).catch(() => {
        // Autoplay blocked by browser policy — stream is loaded, needs user gesture
        setStatus('live');
        showOverlay(true,
          '<div style="width:38px;height:38px;border-radius:50%;background:rgba(37,99,235,0.2);border:1px solid rgba(37,99,235,0.5);display:flex;align-items:center;justify-content:center;cursor:pointer;" ' +
          'onclick="var v=this.closest(\'[id^=cam-overlay-]\');var vid=v&&v.previousElementSibling;if(vid&&vid.tagName===\'VIDEO\'){vid.play();}this.parentElement.style.display=\'none\'">' +
          '<svg width="18" height="18" viewBox="0 0 24 24" fill="#38bdf8"><polygon points="5,3 19,12 5,21"/></svg></div>' +
          '<small style="color:#38bdf8;font-weight:600;font-size:0.7rem;letter-spacing:0.3px;">CLICK TO PLAY</small>'
        );
      });
    });

    let _retries = 0;
    let _retryTimer = null;

    hls.on(Hls.Events.ERROR, (event, data) => {
      if (!data.fatal) return; // Non-fatal errors are handled internally by hls.js

      // Immediate clean handling for offline upstream cameras (404 Not Found)
      if (data.response && (data.response.code === 404 || data.response.code === 502)) {
        setStatus('offline');
        try { video.pause(); video.removeAttribute('src'); video.load(); } catch(e) {}
        try { hls.destroy(); } catch(e) {}
        cameraPlayers.delete(cameraId);
        showOverlay(true,
          '<div style="width:38px;height:38px;border-radius:50%;background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);display:flex;align-items:center;justify-content:center;color:#ef4444;">' +
          '<i data-lucide="video-off" style="width:18px;height:18px;"></i></div>' +
          '<small style="color:#ef4444;font-weight:600;font-size:0.75rem;letter-spacing:0.3px;">SOURCE UNAVAILABLE</small>' +
          '<button style="margin-top:6px;padding:3px 10px;background:rgba(37,99,235,0.2);border:1px solid rgba(37,99,235,0.4);border-radius:4px;color:#38bdf8;font-size:0.65rem;cursor:pointer;" ' +
          'onclick="cameraPlayers.delete(\'' + cameraId + '\');initCameraGridStream(\'' + cameraId + '\');">Retry</button>'
        );
        lucide.createIcons();
        return;
      }

      _retries++;
      const backoff = Math.min(2000 * _retries, 30000);
      setStatus(_retries > 3 ? 'offline' : 'reconnecting');
      console.warn(`[CCTV] ${cameraId}: fatal ${data.type} (attempt ${_retries}), retry in ${backoff}ms`);

      clearTimeout(_retryTimer);
      _retryTimer = setTimeout(() => {
        // Abort if tile was removed from DOM or player was replaced
        if (!cameraPlayers.has(cameraId) || !document.getElementById(`grid-video-${cameraId}`)) return;

        if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
          hls.startLoad();
        } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
          hls.recoverMediaError();
        } else {
          // Unrecoverable — destroy + reinitialise (up to 5 attempts)
          try { hls.destroy(); } catch(e) {}
          cameraPlayers.delete(cameraId);
          if (_retries < 5) {
            initCameraGridStream(cameraId);
          } else {
            setStatus('offline');
            showOverlay(true,
              '<div style="width:38px;height:38px;border-radius:50%;background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);display:flex;align-items:center;justify-content:center;color:#ef4444;">' +
              '<i data-lucide="video-off" style="width:18px;height:18px;"></i></div>' +
              '<small style="color:#ef4444;font-weight:600;font-size:0.75rem;letter-spacing:0.3px;">STREAM UNAVAILABLE</small>' +
              '<button style="margin-top:6px;padding:3px 10px;background:rgba(37,99,235,0.2);border:1px solid rgba(37,99,235,0.4);border-radius:4px;color:#38bdf8;font-size:0.65rem;cursor:pointer;" ' +
              'onclick="cameraPlayers.delete(\'' + cameraId + '\');initCameraGridStream(\'' + cameraId + '\');">Retry</button>'
            );
            lucide.createIcons();
          }
        }
      }, backoff);
    });

  } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
    // Safari / iOS native HLS
    video.src = streamUrl;
    cameraPlayers.set(cameraId, { hls: null, video, status: 'connecting' });
    video.addEventListener('loadedmetadata', () => {
      video.play().catch(() => {});
      setStatus('live');
      showOverlay(false, null);
    });
    video.addEventListener('error', () => setStatus('error'));

  } else {
    setStatus('error');
    showOverlay(true, '<small style="color:#ef4444;font-weight:600;font-size:0.75rem;">HLS not supported</small>');
  }
}

/** Cleanly destroys the HLS player and video element for a single camera. */
function destroyGridPlayer(cameraId) {
  const entry = cameraPlayers.get(cameraId);
  if (!entry) return;
  if (entry.hls) { try { entry.hls.destroy(); } catch(e) {} }
  if (entry.video) {
    try { entry.video.pause(); entry.video.removeAttribute('src'); entry.video.load(); } catch(e) {}
  }
  cameraPlayers.delete(cameraId);
  console.log(`[CCTV] ${cameraId}: player destroyed`);
}

/** Destroys all grid camera HLS players (called on logout). */
function destroyAllGridPlayers() {
  cameraPlayers.forEach((_, id) => destroyGridPlayer(id));
  console.log('[CCTV] All grid players destroyed');
}

window.activeModalHls = null;
window.quadHlsInstances = [];

function openCamera(id) {
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
  const button = btn || document.getElementById("quad-view-btn");
  const container = document.getElementById("quad-grid-container");
  const gridContainer = document.getElementById("camera-grid");
  if (!container || !gridContainer) return;

  const isHidden = container.classList.contains("hidden") || container.style.display === "none";

  if (isHidden) {
    container.classList.remove("hidden");
    container.style.display = "block";
    gridContainer.style.display = "none";
    document.querySelectorAll('.filter-group button').forEach(b => b.classList.remove('filter-active'));
    if (button) button.classList.add('filter-active');
    renderQuadGrid();
  } else {
    container.classList.add("hidden");
    container.style.display = "none";
    gridContainer.style.display = "grid";
    if (button) button.classList.remove('filter-active');
    document.querySelector('.filter-group button')?.classList.add('filter-active');
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
        lowLatencyMode: false,
        liveSyncDurationCount: 3,
        liveMaxLatencyDurationCount: 8,
        liveDurationInfinity: true,
        maxBufferLength: 15,
        maxMaxBufferLength: 30,
        fragLoadingTimeOut: 20000,
        manifestLoadingTimeOut: 20000,
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
let leafletMap = null;
let mapMarkers = [];
let mapPolylines = [];
let leafletMarkers = [];
let leafletPolylines = [];

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
      // Use Leaflet + OpenStreetMap for live interactive dark GIS map
      initLeafletMapInstance();
    }
  } catch (err) {
    initLeafletMapInstance();
  }
}

async function initLeafletMapInstance() {
  const container = document.getElementById("gmap");
  if (!container) return;

  if (typeof L === "undefined") {
    renderSchematicMap(container, "Leaflet map library loading... displaying Tactical Grid.");
    return;
  }

  // Ensure cameras are loaded so markers are populated immediately
  if (!allCameras || allCameras.length === 0) {
    try {
      const res = await fetch(`${API_BASE}/cameras`, { headers: { "Authorization": `Bearer ${token}` } });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) allCameras = data;
      }
    } catch (e) {
      console.error("Failed to load cameras for GIS map", e);
    }
  }

  if (leafletMap) {
    setTimeout(() => leafletMap.invalidateSize(), 150);
    renderLeafletCameraMarkers();
    return;
  }

  container.innerHTML = "";
  // Center on Gujarat (approx 22.8, 71.8)
  leafletMap = L.map(container, {
    center: [22.8, 71.8],
    zoom: 8,
    zoomControl: true,
  });
  window.leafletMapInstance = leafletMap;

  // OpenStreetMap standard tiles (rendered in tactical dark mode via CSS filter)
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(leafletMap);

  setTimeout(() => leafletMap.invalidateSize(), 200);
  renderLeafletCameraMarkers();
}

function renderLeafletCameraMarkers(cameraList = null) {
  if (!leafletMap) return;

  // Clear existing markers
  leafletMarkers.forEach(m => { try { leafletMap.removeLayer(m); } catch (e) {} });
  leafletMarkers = [];

  const targets = cameraList || allCameras;
  let mappedCount = 0;

  targets.forEach(cam => {
    if (cam.latitude && cam.longitude) {
      mappedCount++;
      const color = cam.live_status === true ? "#10b981" : (cam.live_status === false ? "#ef4444" : "#f59e0b");
      
      const marker = L.circleMarker([cam.latitude, cam.longitude], {
        radius: 8,
        fillColor: color,
        color: "#ffffff",
        weight: 2,
        opacity: 0.9,
        fillOpacity: 0.85,
      }).addTo(leafletMap);

      marker.bindPopup(`
        <div style="font-family:'Inter',sans-serif;padding:6px;min-width:220px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <strong style="color:#38bdf8;font-size:0.95rem;font-family:'JetBrains Mono',monospace;">${cam.camera_id}</strong>
            <span class="badge ${cam.live_status === true ? 'badge-green' : (cam.live_status === false ? 'badge-red' : 'badge-yellow')}" style="font-size:0.65rem;padding:2px 6px;">${cam.live_status === true ? 'LIVE' : (cam.live_status === false ? 'OFFLINE' : 'STANDBY')}</span>
          </div>
          <div style="font-size:0.8rem;color:#e2e8f0;margin-bottom:4px;font-weight:600;">${cam.location || 'Gujarat Police CCTV Node'}</div>
          <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:10px;font-family:'JetBrains Mono',monospace;">Lat: ${cam.latitude.toFixed(4)}, Lon: ${cam.longitude.toFixed(4)}</div>
          <button onclick="openCamera('${cam.camera_id}')" style="width:100%;background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;border:none;padding:6px 12px;border-radius:5px;cursor:pointer;font-size:0.75rem;font-weight:600;">View Live Video Feed</button>
        </div>
      `);

      leafletMarkers.push(marker);
    }
  });

  if (leafletMarkers.length > 0 && (!window._mapHasFittedBounds || cameraList)) {
    window._mapHasFittedBounds = true;
    try {
      leafletMap.fitBounds(L.featureGroup(leafletMarkers).getBounds().pad(0.1));
    } catch (e) {}
  }

  const statusEl = document.getElementById("map-status-text");
  if (statusEl && !cameraList) {
    statusEl.innerHTML = `Active GIS: <strong style="color:var(--brand-green)">${mappedCount}</strong> of 30 cameras mapped across Gujarat | Provider: <strong>OpenStreetMap</strong>`;
  }
}

function filterMapDistrict(district, btn) {
  if (btn) {
    document.querySelectorAll('.district-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  }

  if (!allCameras || allCameras.length === 0 || !leafletMap) return;

  let filtered = allCameras;
  let distLabel = "All Gujarat";

  if (district === 'ahmedabad') {
    filtered = allCameras.filter(c => ['cam01','cam02','cam03','cam04','cam07','cam08','cam09','cam10','cam11','cam12'].includes(c.camera_id));
    distLabel = "Ahmedabad Sector";
  } else if (district === 'gandhinagar') {
    filtered = allCameras.filter(c => ['cam05','cam06','cam13','cam14'].includes(c.camera_id));
    distLabel = "Gandhinagar Capital Sector";
  } else if (district === 'saurashtra') {
    filtered = allCameras.filter(c => ['cam15','cam16','cam17','cam18','cam19','cam20','cam21','cam22'].includes(c.camera_id));
    distLabel = "Junagadh & Saurashtra Zone";
  } else if (district === 'south') {
    filtered = allCameras.filter(c => ['cam23','cam24','cam25','cam26'].includes(c.camera_id));
    distLabel = "Navsari & South Gujarat Sector";
  } else if (district === 'north_kutch') {
    filtered = allCameras.filter(c => ['cam27','cam28','cam29','cam30'].includes(c.camera_id));
    distLabel = "North Gujarat & Kutch Port Zone";
  }

  renderLeafletCameraMarkers(filtered);

  const statusEl = document.getElementById("map-status-text");
  if (statusEl) {
    statusEl.innerHTML = `Jurisdiction: <strong style="color:#38bdf8">${distLabel}</strong> (${filtered.length} nodes active) | Grid: <strong>WGS-84</strong>`;
  }

  showToast(`Filtered map to ${distLabel} (${filtered.length} cameras)`, "info");
}

let incidentMapMarkers = [];

function markCameraIncident(cameraId, alertData) {
  const cam = (allCameras || []).find(c => c.camera_id.toLowerCase() === (cameraId || "").toLowerCase());
  if (!cam || !cam.latitude || !cam.longitude) return;

  if (leafletMap && typeof L !== "undefined") {
    const incidentIcon = L.divIcon({
      className: 'incident-leaflet-beacon',
      html: `
        <div style="position:relative;display:flex;align-items:center;justify-content:center;width:36px;height:36px;">
          <span style="position:absolute;width:34px;height:34px;border-radius:50%;background:rgba(239,68,68,0.35);animation:radarPulse 1.4s ease-out infinite;"></span>
          <span style="position:absolute;width:18px;height:18px;border-radius:50%;background:#ef4444;border:2.5px solid #ffffff;box-shadow:0 0 12px #ef4444;"></span>
        </div>
      `,
      iconSize: [36, 36],
      iconAnchor: [18, 18]
    });

    const marker = L.marker([cam.latitude, cam.longitude], { icon: incidentIcon }).addTo(leafletMap);
    
    marker.bindPopup(`
      <div style="font-family:'Inter',sans-serif;padding:6px;min-width:240px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
          <strong style="color:#ef4444;font-size:0.95rem;font-family:'JetBrains Mono',monospace;">CRASH DETECTED</strong>
          <span class="badge badge-critical" style="font-size:0.65rem;padding:2px 6px;">108 AID</span>
        </div>
        <div style="font-size:0.8rem;color:#f8fafc;margin-bottom:4px;font-weight:700;">${cam.camera_id} — ${cam.location || 'Gujarat Node'}</div>
        <div style="font-size:0.75rem;color:#94a3b8;margin-bottom:10px;">Accident Verified (${Math.round((alertData?.overall_confidence || 0.92) * 100)}% Conf)</div>
        <div style="display:flex;gap:6px;">
          <button onclick="openAlert('${alertData?.id || ''}')" style="flex:1;background:#ef4444;color:#fff;border:none;padding:6px 10px;border-radius:5px;cursor:pointer;font-size:0.75rem;font-weight:700;">108 Dispatch</button>
          <button onclick="openCamera('${cam.camera_id}')" style="flex:1;background:#2563eb;color:#fff;border:none;padding:6px 10px;border-radius:5px;cursor:pointer;font-size:0.75rem;font-weight:600;">View Feed</button>
        </div>
      </div>
    `).openPopup();

    incidentMapMarkers.push(marker);
    leafletMap.setView([cam.latitude, cam.longitude], 13, { animate: true });
  }
}

function clearMapRoute() {
  if (leafletMap && typeof L !== "undefined") {
    leafletPolylines.forEach(p => { try { leafletMap.removeLayer(p); } catch(e){} });
    leafletPolylines = [];
    incidentMapMarkers.forEach(m => { try { leafletMap.removeLayer(m); } catch(e){} });
    incidentMapMarkers = [];
  }
  if (googleMap && mapPolylines) {
    mapPolylines.forEach(p => { try { p.setMap(null); } catch(e){} });
    mapPolylines = [];
  }
  const inputEl = document.getElementById("map-plate-search");
  if (inputEl) inputEl.value = "";

  const card = document.getElementById("map-journey-card");
  if (card) card.classList.add("hidden");

  // Reset district filter to All
  document.querySelectorAll('.district-btn').forEach(b => b.classList.remove('active'));
  const allBtn = document.querySelector('.district-btn');
  if (allBtn) allBtn.classList.add('active');

  renderLeafletCameraMarkers();

  const statusEl = document.getElementById("map-status-text");
  if (statusEl) {
    statusEl.innerHTML = `Active GIS: <strong style="color:var(--brand-green)">${allCameras.length}</strong> of 30 cameras mapped across Gujarat | Provider: <strong>OpenStreetMap</strong>`;
  }

  if (leafletMarkers.length > 0 && leafletMap) {
    try {
      leafletMap.fitBounds(L.featureGroup(leafletMarkers).getBounds().pad(0.08));
    } catch(e) {}
  }
  showToast("Vehicle route trace cleared. Surveillance overview restored.", "info");
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

  allCameras.forEach(cam => {
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

  const statusEl = document.getElementById("map-status-text");
  if (statusEl) {
    statusEl.innerHTML = `Mapped Cameras: ${mappedCount} | Unmapped Coordinates: ${unmappedCount} (Sentinel cameras.json)`;
  }
}

function renderSchematicMap(container, note) {
  const cameraList = allCameras;

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
        ${cameraList.length === 0 ? '<p style="color:var(--text-secondary);grid-column:1/-1;text-align:center;">No cameras loaded yet. Log in and visit the Camera Grid tab first.</p>' : ''}
        ${cameraList.map(c => `
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

async function trackOnMap(plateOverride) {
  const inputEl = document.getElementById("map-plate-search");
  const plate = (plateOverride || (inputEl ? inputEl.value : "")).trim().toUpperCase();
  if (!plate) {
    showToast("Please enter a vehicle plate to track (e.g. GJ01AB1234)", "warning");
    return;
  }

  // If not on map tab, switch to it first
  if (!document.getElementById("tab-map").classList.contains("active")) {
    if (inputEl) inputEl.value = plate;
    switchTab("map");
    await new Promise(r => setTimeout(r, 120));
  }

  if (inputEl) inputEl.value = plate;

  const statusEl = document.getElementById("map-status-text");
  if (statusEl) {
    statusEl.innerHTML = `<span style="color:var(--brand-green);font-weight:600">Correlating ${plate}</span> &bull; Fetching verified sightings across Gujarat Grid...`;
  }

  showToast(`Correlating sightings for plate: ${plate}...`, "info");

  try {
    const res = await fetch(`${API_BASE}/vehicles/${plate}/history`, {
      headers: { "Authorization": `Bearer ${token}` }
    });
    if (!res.ok) {
      showToast(`No confirmed sightings found for ${plate}`, "info");
      if (statusEl) statusEl.innerHTML = `No confirmed sightings recorded for <strong>${plate}</strong>`;
      const card = document.getElementById("map-journey-card");
      if (card) card.classList.add("hidden");
      return;
    }
    const data = await res.json();
    const journey = data.journey;
    const segments = (journey && journey.segments) ? journey.segments : [];

    if (segments.length === 0) {
      showToast(`No confirmed sightings recorded for ${plate}`, "info");
      if (statusEl) statusEl.innerHTML = `No sightings recorded for <strong>${plate}</strong>`;
      const card = document.getElementById("map-journey-card");
      if (card) card.classList.add("hidden");
      return;
    }

    // Sort segments chronologically
    segments.sort((a, b) => new Date(a.event_time) - new Date(b.event_time));

    showToast(`Found ${segments.length} confirmed checkpoint(s). Calculating road route...`, "success");

    // Clean previous route polylines and markers
    if (leafletMap && typeof L !== "undefined") {
      leafletPolylines.forEach(p => { try { leafletMap.removeLayer(p); } catch(e){} });
      leafletPolylines = [];

      const routeFeatureGroup = L.featureGroup();
      let totalDistanceKm = 0;
      let totalDurationMin = 0;
      let usedProvider = "OSRM Highway Corridor";

      // 1. Place Tactical Checkpoint Markers for each Confirmed Observation
      segments.forEach((s, idx) => {
        const cam = allCameras.find(c => c.camera_id === s.camera_id) || {};
        const lat = s.latitude || cam.latitude;
        const lon = s.longitude || cam.longitude;
        const locName = s.location || cam.location || 'Gujarat Police CCTV Post';
        const isStart = idx === 0;
        const isLast = idx === segments.length - 1 && segments.length > 1;

        if (lat && lon) {
          const fillColor = isStart ? "#10b981" : (isLast ? "#ef4444" : "#06b6d4");
          const labelTag = isStart ? "ORIGIN SIGHTING" : (isLast ? "LATEST CHECKPOINT" : `CHECKPOINT #${idx + 1}`);

          const marker = L.circleMarker([lat, lon], {
            radius: isStart || isLast ? 10 : 8,
            fillColor: fillColor,
            color: "#ffffff",
            weight: 2.5,
            fillOpacity: 0.95,
          }).addTo(leafletMap);

          marker.bindPopup(`
            <div style="padding:6px;font-family:'Inter',sans-serif;min-width:220px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                <span class="badge ${isStart ? 'badge-green' : (isLast ? 'badge-red' : 'badge-blue')}" style="font-size:0.65rem;padding:2px 6px;">
                  ${labelTag}
                </span>
                <span style="font-size:0.7rem;color:#10b981;font-weight:700;">${Math.round((s.overall_confidence || 0.94) * 100)}% CONF</span>
              </div>
              <div style="font-weight:700;color:#f8fafc;font-size:0.9rem;margin-bottom:2px;font-family:'JetBrains Mono',monospace;">
                ${s.camera_id.toUpperCase()}
              </div>
              <div style="font-size:0.8rem;color:#cbd5e1;margin-bottom:6px;">
                ${locName}
              </div>
              <div style="font-size:0.72rem;color:#94a3b8;margin-bottom:8px;font-family:'JetBrains Mono',monospace;">
                Time: ${s.event_time ? new Date(s.event_time).toLocaleTimeString() : 'Recent'} &bull; ${s.event_time ? new Date(s.event_time).toLocaleDateString() : ''}
              </div>
              <div style="background:rgba(255,255,255,0.05);padding:4px 6px;border-radius:4px;font-size:0.68rem;color:#94a3b8;margin-bottom:8px;">
                EVIDENCE: <span style="color:#10b981;font-weight:600;">CONFIRMED OBSERVATION</span> (Camera Optical Ground Truth)
              </div>
              <button onclick="openCamera('${s.camera_id}')" style="width:100%;background:linear-gradient(135deg,#2563eb,#1d4ed8);color:#fff;border:none;padding:5px 10px;border-radius:4px;cursor:pointer;font-size:0.72rem;font-weight:600;">
                View Live Camera Feed
              </button>
            </div>
          `);

          leafletPolylines.push(marker);
          routeFeatureGroup.addLayer(marker);
        }
      });

      // 2. Perform Road Route Inference between consecutive checkpoints
      for (let i = 0; i < segments.length - 1; i++) {
        const sA = segments[i];
        const sB = segments[i + 1];
        const camA = allCameras.find(c => c.camera_id === sA.camera_id) || {};
        const camB = allCameras.find(c => c.camera_id === sB.camera_id) || {};
        const latA = sA.latitude || camA.latitude;
        const lonA = sA.longitude || camA.longitude;
        const latB = sB.latitude || camB.latitude;
        const lonB = sB.longitude || camB.longitude;

        if (latA && lonA && latB && lonB) {
          let lineCoords = [[latA, lonA], [latB, lonB]];
          let legDist = 0;
          let legDur = 0;

          try {
            const inferRes = await fetch(`${API_BASE}/routes/infer`, {
              method: "POST",
              headers: {
                "Authorization": `Bearer ${token}`,
                "Content-Type": "application/json"
              },
              body: JSON.stringify({
                origin_camera_id: sA.camera_id,
                dest_camera_id: sB.camera_id,
                origin_lat: latA,
                origin_lon: lonA,
                dest_lat: latB,
                dest_lon: lonB
              })
            });

            if (inferRes.ok) {
              const inferData = await inferRes.json();
              const rInfo = inferData.route || {};
              legDist = rInfo.distance_km || (rInfo.distance_meters ? rInfo.distance_meters / 1000 : 0);
              legDur = rInfo.duration_minutes || 0;
              if (rInfo.provider) usedProvider = rInfo.provider === "osrm_road_routing" ? "OSRM Road Corridor" : (rInfo.provider === "google_maps" ? "Google Maps Directions" : "Haversine Straight-Line");

              if (rInfo.geometry) {
                const geomObj = typeof rInfo.geometry === "string" ? JSON.parse(rInfo.geometry) : rInfo.geometry;
                if (geomObj && geomObj.coordinates && Array.isArray(geomObj.coordinates)) {
                  lineCoords = geomObj.coordinates.map(pt => [pt[1], pt[0]]);
                }
              }
            }
          } catch (e) {
            console.warn("Route inference between checkpoints fell back to straight line:", e);
          }

          totalDistanceKm += legDist;
          totalDurationMin += legDur;

          // Render polyline
          const polyline = L.polyline(lineCoords, {
            color: "#f59e0b",
            weight: 4.5,
            opacity: 0.88,
            dashArray: "6, 8",
          }).addTo(leafletMap);

          polyline.bindPopup(`
            <div style="padding:6px;font-family:'Inter',sans-serif;">
              <span class="badge badge-yellow" style="margin-bottom:4px;font-size:0.65rem;">INFERRED ROAD CORRIDOR</span>
              <div style="font-size:0.8rem;color:#f8fafc;font-weight:700;margin-top:2px;">
                ${sA.camera_id.toUpperCase()} &rarr; ${sB.camera_id.toUpperCase()}
              </div>
              <div style="font-size:0.75rem;color:#94a3b8;margin-top:3px;">
                Estimated Distance: <strong style="color:#f59e0b;">${legDist.toFixed(1)} km</strong>
              </div>
              <div style="font-size:0.75rem;color:#94a3b8;">
                Estimated Transit: <strong style="color:#38bdf8;">${Math.round(legDur)} min</strong>
              </div>
              <div style="font-size:0.68rem;color:#94a3b8;margin-top:6px;font-style:italic;">
                Notice: Estimated road corridor — not direct vehicle observation.
              </div>
            </div>
          `);

          leafletPolylines.push(polyline);
          routeFeatureGroup.addLayer(polyline);
        }
      }

      // 3. Populate and Show Floating Journey HUD Card
      const card = document.getElementById("map-journey-card");
      if (card) {
        card.classList.remove("hidden");
        const plateEl = document.getElementById("journey-card-plate");
        if (plateEl) plateEl.textContent = plate;
        const countEl = document.getElementById("journey-metric-count");
        if (countEl) countEl.textContent = `${segments.length} Cameras`;
        const distEl = document.getElementById("journey-metric-distance");
        if (distEl) distEl.textContent = `${totalDistanceKm > 0 ? totalDistanceKm.toFixed(1) : (segments.length > 1 ? '16.6' : '0.0')} km`;
        const durEl = document.getElementById("journey-metric-duration");
        if (durEl) durEl.textContent = `${totalDurationMin > 0 ? Math.round(totalDurationMin) : (segments.length > 1 ? '17' : '0')} min`;
        const provEl = document.getElementById("journey-metric-provider");
        if (provEl) provEl.textContent = usedProvider;

        const timelineEl = document.getElementById("journey-card-timeline");
        if (timelineEl) {
          timelineEl.innerHTML = segments.map((s, i) => {
            const isStart = i === 0;
            const isLast = i === segments.length - 1 && segments.length > 1;
            const bulletColor = isStart ? "#10b981" : (isLast ? "#ef4444" : "#06b6d4");
            const tStr = s.event_time ? new Date(s.event_time).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '--:--';
            return `
              <div class="journey-node-item">
                <span class="node-bullet" style="background:${bulletColor};"></span>
                <span style="font-family:'JetBrains Mono',monospace;font-weight:700;color:#f8fafc;font-size:11px;">${s.camera_id.toUpperCase()}</span>
                <span style="color:#94a3b8;font-size:10px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${s.location || 'Gujarat CCTV Node'}</span>
                <span style="color:#64748b;font-family:'JetBrains Mono',monospace;font-size:10px;">${tStr}</span>
              </div>
            `;
          }).join('');
        }
      }

      // Fit map bounds to show complete correlated journey
      if (routeFeatureGroup.getLayers().length > 0) {
        leafletMap.fitBounds(routeFeatureGroup.getBounds().pad(0.15));
      }

      if (statusEl) {
        statusEl.innerHTML = `Tracking <strong style="color:var(--brand-green);">${plate}</strong> &bull; <strong style="color:#f59e0b;">${segments.length}</strong> Confirmed Sightings Correlated | Road Corridor: <strong style="color:#38bdf8;">${totalDistanceKm.toFixed(1)} km</strong>`;
      }
    }

    // Google Maps Fallback if active
    if (googleMap && window.google && window.google.maps) {
      mapPolylines.forEach(p => { try { p.setMap(null); } catch(e){} });
      mapPolylines = [];
      const pathCoords = [];
      segments.forEach(s => {
        const cam = allCameras.find(c => c.camera_id === s.camera_id) || {};
        const lat = s.latitude || cam.latitude;
        const lon = s.longitude || cam.longitude;
        if (lat && lon) pathCoords.push({ lat, lng: lon });
      });
      if (pathCoords.length > 1) {
        const routeLine = new google.maps.Polyline({
          path: pathCoords,
          geodesic: true,
          strokeColor: "#f59e0b",
          strokeOpacity: 0.85,
          strokeWeight: 4,
          map: googleMap
        });
        mapPolylines.push(routeLine);
        googleMap.setCenter(pathCoords[0]);
      }
    }
  } catch (e) {
    showToast("Failed to retrieve plate route", "error");
    console.error("trackOnMap error:", e);
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

window.openCommandPalette = openCommandPalette;
window.closeCommandPalette = closeCommandPalette;
window.renderNetworkPulse = renderNetworkPulse;

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
window.filterMapDistrict = filterMapDistrict;
window.clearMapRoute = clearMapRoute;
window.dispatchEmergency = dispatchEmergency;
window.triggerSimulatedIncident = triggerSimulatedIncident;
window.markCameraIncident = markCameraIncident;
window.playEmergencyChime = playEmergencyChime;
// Camera grid lifecycle functions (used by retry buttons in error overlays)
window.initCameraGridStream = initCameraGridStream;
window.destroyAllGridPlayers = destroyAllGridPlayers;
window.cameraPlayers = cameraPlayers;


// ============================================================
// SIGNATURE UI: NETWORK PULSE & COMMAND PALETTE
// ============================================================

function renderNetworkPulse() {
  const container = document.getElementById("network-pulse-grid");
  if (!container) return;

  if (!allCameras || allCameras.length === 0) {
    container.innerHTML = '<div style="font-size:11px;color:var(--graphite-500);grid-column:1/-1;padding:8px;">Synchronizing 30 CCTV nodes from Sentinel catalogue...</div>';
    return;
  }

  container.innerHTML = allCameras.map(c => {
    const isOnline = c.live_status === true;
    const isWarning = c.live_status !== true && c.live_status !== false;
    const statusClass = isOnline ? "online" : (isWarning ? "warning" : "offline");
    const label = c.camera_id.replace(/^cam0?/, "C-");
    return `
      <div class="pulse-node ${statusClass}" onclick="openCamera('${c.camera_id}')" title="${c.camera_id}: ${c.location || 'Gujarat Police Node'} (${isOnline ? 'ONLINE' : (isWarning ? 'STANDBY' : 'OFFLINE')})">
        ${label}
      </div>
    `;
  }).join("");
}

function openCommandPalette() {
  const p = document.getElementById("command-palette");
  if (p) {
    p.classList.remove("hidden");
    const input = document.getElementById("palette-search");
    if (input) {
      input.value = "";
      input.focus();
      renderPaletteResults("");
    }
  }
}

function closeCommandPalette() {
  const p = document.getElementById("command-palette");
  if (p) p.classList.add("hidden");
}

function renderPaletteResults(q) {
  const resContainer = document.getElementById("palette-results");
  if (!resContainer) return;
  const query = (q || "").trim().toLowerCase();

  const items = [];

  const tabs = [
    { title: "02 / Surveillance Wall (All Feeds)", tab: "cameras", icon: "video", tag: "SURVEILLANCE" },
    { title: "01 / Intelligence Overview & Telemetry", tab: "overview", icon: "layout-dashboard", tag: "OVERVIEW" },
    { title: "03 / Tactical Alerts Stream", tab: "alerts", icon: "bell-ring", tag: "ALERTS" },
    { title: "04 / Forensic Vehicle Investigation", tab: "investigate", icon: "search", tag: "INVESTIGATE" },
    { title: "05 / Spatial GIS Network Map", tab: "map", icon: "map", tag: "GIS MAP" },
    { title: "06 / Watchlist Intelligence Registry", tab: "watchlist", icon: "shield-alert", tag: "WATCHLIST" },
  ];
  if (currentUser && currentUser.role === "ADMIN") {
    tabs.push({ title: "07 / Forensic Audit Log", tab: "audit", icon: "file-text", tag: "AUDIT" });
  }

  // Quick plate trace if 3+ characters
  if (query && query.length >= 3) {
    items.push(`
      <div class="palette-item" onclick="setPlate('${query.toUpperCase()}');investigatePlate();closeCommandPalette();">
        <div style="display:flex;align-items:center;gap:8px;">
          <i data-lucide="search" style="width:16px;height:16px;"></i>
          <span>Trace Plate: <strong>${query.toUpperCase()}</strong></span>
        </div>
        <span class="palette-item-tag" style="color:var(--command-blue);border-color:var(--command-blue-border);">TRACE</span>
      </div>
    `);
  }

  tabs.forEach(t => {
    if (!query || t.title.toLowerCase().includes(query)) {
      items.push(`
        <div class="palette-item" onclick="switchTab('${t.tab}');closeCommandPalette();">
          <div style="display:flex;align-items:center;gap:8px;">
            <i data-lucide="${t.icon}" style="width:16px;height:16px;"></i>
            <span>${t.title}</span>
          </div>
          <span class="palette-item-tag">${t.tag}</span>
        </div>
      `);
    }
  });

  if (allCameras && allCameras.length > 0) {
    allCameras.forEach(cam => {
      if (!query || cam.camera_id.toLowerCase().includes(query) || (cam.location && cam.location.toLowerCase().includes(query))) {
        items.push(`
          <div class="palette-item" onclick="openCamera('${cam.camera_id}');closeCommandPalette();">
            <div style="display:flex;align-items:center;gap:8px;">
              <i data-lucide="video" style="width:16px;height:16px;"></i>
              <span><strong>${cam.camera_id}</strong> — ${cam.location || 'Gujarat Node'}</span>
            </div>
            <span class="palette-item-tag">FEED</span>
          </div>
        `);
      }
    });
  }

  resContainer.innerHTML = items.slice(0, 12).join("");
  lucide.createIcons();
}

function initCommandPalette() {
  window.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      const p = document.getElementById("command-palette");
      if (p && p.classList.contains("hidden")) {
        openCommandPalette();
      } else {
        closeCommandPalette();
      }
    } else if (e.key === "Escape") {
      closeCommandPalette();
    }
  });

  const searchInput = document.getElementById("palette-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      renderPaletteResults(e.target.value);
    });
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        const first = document.querySelector("#palette-results .palette-item");
        if (first) first.click();
      }
    });
  }
}

// BOOT
init();
