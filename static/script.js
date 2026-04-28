const MODE_COLOURS = {
    bus:  "#4488ff",
    tube: "#ff4455",
    rail: "#44ff88",
};

let map         = null;
let markersLayer = [];
let polygonLayer = null;
let originMarker = null;

// ── Initialise map on page load ───────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
    map = L.map("map").setView([51.505, -0.09], 11);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: "&copy; OpenStreetMap &copy; CartoDB",
        maxZoom: 19,
    }).addTo(map);

    // ── Click map to set origin ───────────────────────────────────────────────
    map.on("click", (e) => {
        const { lat, lng } = e.latlng;

        document.getElementById("lat").value = lat;
        document.getElementById("lng").value = lng;

        const display = document.getElementById("location-display");
        display.textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
        display.classList.add("set");

        const btn = document.getElementById("search-btn");
        btn.disabled = false;
        btn.textContent = "SEARCH";

        if (originMarker) map.removeLayer(originMarker);
        originMarker = L.circleMarker([lat, lng], {
            radius: 10, color: "#ffffff", fillColor: "#ffffff",
            fillOpacity: 1, weight: 2,
        }).addTo(map).bindPopup("<b>Your location</b>");
    });

    // ── Search button ─────────────────────────────────────────────────────────
    document.getElementById("search-btn").addEventListener("click", () => {
        const lat        = parseFloat(document.getElementById("lat").value);
        const lng        = parseFloat(document.getElementById("lng").value);
        const maxMinutes = parseFloat(document.getElementById("max-minutes").value);

        const selected = ["rail", "tube", "bus"].filter(
            m => document.getElementById(`mode-${m}`).checked
        );

        if (!selected.length) {
            alert("Please select at least one transport mode.");
            return;
        }

        loadMap(lat, lng, maxMinutes, selected.join(","));
    });
});

// ── Clear stop markers and polygon (keep origin marker) ───────────────────────
function clearMap() {
    markersLayer.forEach(m => map.removeLayer(m));
    markersLayer = [];
    if (polygonLayer) { map.removeLayer(polygonLayer); polygonLayer = null; }
}

// ── Render stop markers ───────────────────────────────────────────────────────
function renderStops(stops) {
    stops.forEach(stop => {
        const colour = MODE_COLOURS[stop.mode] || "#aaaaaa";
        const marker = L.circleMarker([stop.lat, stop.lng], {
            radius: 6, color: colour, fillColor: colour,
            fillOpacity: 0.85, weight: 1,
        })
        .addTo(map)
        .bindPopup(`<b>${stop.name}</b><br>Mode: ${stop.mode}<br>Journey: ${stop.journey_minutes} mins`);
        markersLayer.push(marker);
    });
}

// ── Render ORS isochrone polygon ──────────────────────────────────────────────
function renderIsochrone(geojson, maxMinutes) {
    if (!geojson?.features?.length) return;
    polygonLayer = L.geoJSON(geojson, {
        style: { color: "#00ffaa", weight: 2, fillColor: "#00ffaa", fillOpacity: 0.1 }
    }).addTo(map).bindPopup(`Reachable area within ${maxMinutes} mins`);
}

// ── Update legend ─────────────────────────────────────────────────────────────
function updateLegend(stops, maxMinutes) {
    const el = document.getElementById("stop-count");
    if (el) el.textContent = `${stops.length} stops within ${maxMinutes} mins`;
}

// ── Render map from Flask response ───────────────────────────────────────────
function renderMap(data) {
    clearMap();
    renderIsochrone(data.isochrone, data.maxMinutes);
    renderStops(data.stops);
    updateLegend(data.stops, data.maxMinutes);
}

// ── Fetch from Flask ──────────────────────────────────────────────────────────
async function loadMap(lat, lng, maxMinutes, modes) {
    const statusEl = document.getElementById("status");
    const btn      = document.getElementById("search-btn");
    statusEl.textContent = "Querying TfL API…";
    btn.disabled = true;

    try {
        const response = await fetch("/run", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ lat, lng, maxMinutes, modes }),
        });
        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const data = await response.json();
        statusEl.textContent = "";
        renderMap(data);
    } catch (err) {
        console.error(err);
        statusEl.textContent = `Error: ${err.message}`;
    } finally {
        btn.disabled = false;
        btn.textContent = "SEARCH";
    }
}