/* ==========================================================================
   THE DAILY BUGLE - CLIENT CONTROLLER: THE WIRE & THE RADAR
   ========================================================================== */

let activeView = "wire";
let isJJJMode = false;
let leafletMap = null;
let mapMarkersGroup = null;
let currentIncidents = [];

const DEFAULT_CENTER = [20.2961, 85.8245];
const DEFAULT_ZOOM = 13;

function initRadarMap() {
  if (leafletMap) return;
  const mapElement = document.getElementById("map-viewport");
  if (!mapElement) return;

  leafletMap = L.map("map-viewport").setView(DEFAULT_CENTER, DEFAULT_ZOOM);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a>',
    subdomains: "abcd",
    maxZoom: 19
  }).addTo(leafletMap);

  mapMarkersGroup = L.layerGroup().addTo(leafletMap);
}

function renderRadarLayers(incidents) {
  if (!leafletMap || !mapMarkersGroup) return;
  mapMarkersGroup.clearLayers();

  incidents.forEach((inc) => {
    if (!inc.is_spatial || !inc.latitude || !inc.longitude) return;

    const isVerified = inc.status === "VERIFIED";
    const markerColor = isVerified ? "#22c48d" : "#f2a93c";

    const hazardRadius = L.circle([inc.latitude, inc.longitude], {
      color: markerColor, fillColor: markerColor, fillOpacity: isVerified ? 0.25 : 0.15, weight: 2, radius: 400
    });

    const pulseMarker = L.circleMarker([inc.latitude, inc.longitude], {
      radius: 7, color: "#ffffff", weight: 2, fillColor: markerColor, fillOpacity: 1
    });

    const popupContent = `
      <div style="font-family: system-ui, sans-serif; min-width: 200px;">
        <span style="display:inline-block; padding: 2px 6px; font-size: 10px; font-weight: 800; border-radius: 3px; background: ${markerColor}; color: #000; text-transform: uppercase;">
          ${isVerified ? "Verified Alert" : "Civic Hazard (Radar)"}
        </span>
        <h4 style="margin: 8px 0 4px 0; color: #111; font-size: 14px;">${escapeHtml(inc.title)}</h4>
        <p style="margin: 0 0 8px 0; color: #444; font-size: 12px;">${escapeHtml(inc.summary)}</p>
        <div style="display: flex; justify-content: space-between; font-size: 11px; font-weight: bold; color: #666; border-top: 1px solid #ddd; padding-top: 4px;">
          <span>Trust Index: ${inc.confidence_score}%</span>
          <span>Reports: ${inc.report_count}</span>
        </div>
      </div>
    `;
    pulseMarker.bindPopup(popupContent);
    hazardRadius.bindPopup(popupContent);
    mapMarkersGroup.addLayer(hazardRadius);
    mapMarkersGroup.addLayer(pulseMarker);
  });

  setTimeout(() => { if (leafletMap) leafletMap.invalidateSize(); }, 150);
}

function renderFeedCards(incidents) {
  const container = document.getElementById("feed-grid");
  if (!container) return;

  const searchBox = document.getElementById("feed-search");
  const query = (searchBox && searchBox.value.trim().toLowerCase()) || "";
  const filtered = query
    ? incidents.filter((inc) =>
        (inc.title || "").toLowerCase().includes(query) ||
        (inc.summary || "").toLowerCase().includes(query) ||
        (inc.category || "").toLowerCase().includes(query)
      )
    : incidents;

  container.innerHTML = "";

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>${query ? "No incidents match your search." : "No incident signals currently recorded in this view."}</p>
      </div>
    `;
    return;
  }

  filtered.forEach((inc) => {
    let reasons = [];
    try { reasons = JSON.parse(inc.explainability_json); } catch (e) { reasons = []; }

    let displayTitle = inc.title;
    if (isJJJMode) {
      if (inc.category === "Fire") {
        displayTitle = "SPIDER-MAN MENACE! WEB-HEAD SPOTTED FLEEING AS DISASTER STRIKES!";
      } else if (inc.category === "Obstruction") {
        displayTitle = "CHAOS IN THE STREETS! CITY IN PARALYSIS WHILE MASKED VIGILANTE HIDES!";
      } else {
        displayTitle = `EXCLUSIVE: ${inc.title.toUpperCase()} — THE DAILY BUGLE DEMANDS ANSWERS!`;
      }
    }

    let badgeClass = "badge-review";
    if (inc.status === "VERIFIED") badgeClass = "badge-verified";
    else if (inc.status === "COMMUNITY") badgeClass = "badge-community";
    else if (inc.status === "BUSTED") badgeClass = "badge-busted";

    const formattedTime = new Date(inc.updated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

    const reasonsHtml = reasons.slice(0, 3).map((r) => {
      const isPos = String(r.value).startsWith("+");
      return `<li><span>${escapeHtml(r.label)}</span><span class="${isPos ? "val-pos" : "val-neg"}">${escapeHtml(String(r.value))}</span></li>`;
    }).join("");

    const isBusted = inc.status === "BUSTED";
    const progressClass = isBusted ? "score-bar-fill danger" : "score-bar-fill";

    const card = document.createElement("article");
    card.className = "news-card";
    card.innerHTML = `
      <div>
        <div class="card-top">
          <span class="badge ${badgeClass}">${inc.status}</span>
          <span class="timestamp">${formattedTime}</span>
        </div>
        <h2 class="card-headline">
          <a href="/incident/${inc.id}" style="color: inherit; text-decoration: none;">${escapeHtml(displayTitle)}</a>
        </h2>
        <p class="card-summary">${escapeHtml(inc.summary)}</p>
      </div>
      <div class="trust-panel">
        <div class="trust-header">
          <span>Confidence Index</span>
          <span class="trust-metric-highlight">${inc.confidence_score}/100</span>
        </div>
        <div class="score-bar-track">
          <div class="${progressClass}" style="width: ${Math.max(8, inc.confidence_score)}%"></div>
        </div>
        <ul class="explainability-reasons">${reasonsHtml}</ul>
      </div>
    `;
    container.appendChild(card);
  });
}

async function fetchIncidentFeed() {
  const feedGrid = document.getElementById("feed-grid");
  const mapViewport = document.getElementById("map-viewport");

  try {
    const response = await fetch(`/api/incidents?view=${activeView}`);
    if (!response.ok) throw new Error("Feed request failed");
    const data = await response.json();
    currentIncidents = data;

    if (activeView === "radar") {
      feedGrid.style.display = "none";
      mapViewport.style.display = "block";
      initRadarMap();
      renderRadarLayers(data);
    } else {
      mapViewport.style.display = "none";
      feedGrid.style.display = "grid";
      renderFeedCards(data);
    }
  } catch (error) {
    console.error("Failed to load feed data:", error);
    showToast("Couldn't load the incident feed. Retrying shortly.", "error");
  }
}

function switchFeedView(viewName, targetElement) {
  activeView = viewName;
  document.querySelectorAll(".tab-btn").forEach((btn) => btn.classList.remove("active"));
  if (targetElement) targetElement.classList.add("active");
  fetchIncidentFeed();
}

function toggleJJJEditorial(checkbox) {
  isJJJMode = checkbox.checked;
  document.body.classList.toggle("jjj-active", isJJJMode);
  renderFeedCards(currentIncidents);
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str == null ? "" : String(str);
  return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", () => {
  fetchIncidentFeed();
});
