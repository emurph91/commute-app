const MODE_COLOURS = {
    bus:   "#2266dd",
    tube:  "#cc2233",
    rail:  "#229944",
    ferry: "#ff9900",
};

let map             = null;
let polygonLayer    = null;
let originMarker    = null;
let abortController = null;
let currentSearchId = null;

const modeMarkers = { rail: [], tube: [], bus: [], ferry: [] };

window.addEventListener("DOMContentLoaded", () => {
    map = L.map("map", { zoomControl: false }).setView([51.505, -0.09], 11);

    L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "&copy; OpenStreetMap &copy; CartoDB",
        maxZoom: 19,
    }).addTo(map);

    L.control.zoom({ position: "bottomright" }).addTo(map);

    loadAllStops();

    // ── Click map to set origin ───────────────────────────────────────────────
    map.on("click", (e) => {
        const { lat, lng } = e.latlng;

        document.getElementById("lat").value = lat;
        document.getElementById("lng").value = lng;

        const display = document.getElementById("location-display");
        display.textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
        display.classList.add("set");

        const btn = document.getElementById("search-btn");
        btn.disabled    = false;
        btn.textContent = "SEARCH";

        if (originMarker) map.removeLayer(originMarker);
        originMarker = L.circleMarker([lat, lng], {
            radius: 10, color: "#333", fillColor: "#333",
            fillOpacity: 1, weight: 2,
        }).addTo(map).bindPopup("<b>Your location</b>");
    });

    // ── Time/distance toggle ──────────────────────────────────────────────────
    document.querySelectorAll("input[name='filter-type']").forEach(radio => {
        radio.addEventListener("change", (e) => {
            document.getElementById("time-field").style.display     = e.target.value === "time"     ? "flex" : "none";
            document.getElementById("distance-field").style.display = e.target.value === "distance" ? "flex" : "none";
            document.getElementById("depart-field").style.display   = e.target.value === "time"     ? "flex" : "none";
        });
    });

    // ── Search button ─────────────────────────────────────────────────────────
    document.getElementById("search-btn").addEventListener("click", () => {
        const lat        = parseFloat(document.getElementById("lat").value);
        const lng        = parseFloat(document.getElementById("lng").value);
        const filterType = document.querySelector("input[name='filter-type']:checked").value;
        const maxMinutes = parseFloat(document.getElementById("max-minutes").value);
        const maxKm      = parseFloat(document.getElementById("max-km").value);
        const departTime = document.getElementById("depart-time").value;   // "09:00"
        const selected   = ["rail", "tube", "bus", "ferry"].filter(
            m => document.getElementById(`mode-${m}`).checked
        );

        if (!selected.length) {
            alert("Please select at least one transport mode.");
            return;
        }

        document.getElementById("cancel-btn").disabled = filterType !== "time";
        runSearch(lat, lng, maxMinutes, maxKm, filterType, selected.join(","), departTime);
    });

    // ── Cancel button ─────────────────────────────────────────────────────────
    document.getElementById("cancel-btn").addEventListener("click", async () => {
        if (currentSearchId) {
            await fetch("/cancel", {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ searchId: currentSearchId }),
            });
        }
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        document.getElementById("status").textContent    = "Search cancelled.";
        document.getElementById("search-btn").disabled   = false;
        document.getElementById("search-btn").textContent = "SEARCH";
        document.getElementById("cancel-btn").disabled   = true;
        currentSearchId = null;
    });

    // ── Checkbox live filter ──────────────────────────────────────────────────
    ["rail", "tube", "bus", "ferry"].forEach(mode => {
        document.getElementById(`mode-${mode}`).addEventListener("change", (e) => {
            modeMarkers[mode].forEach(marker => {
                e.target.checked ? marker.addTo(map) : map.removeLayer(marker);
            });
        });
    });
});

// ── Fetch all stops on load ───────────────────────────────────────────────────
async function loadAllStops() {
    try {
        const response = await fetch("/stops");
        const stops    = await response.json();
        renderStops(stops, false);
    } catch (err) {
        console.error("Failed to load stops:", err);
    }
}

// ── Clear all mode markers and polygon ────────────────────────────────────────
function clearMap() {
    ["rail", "tube", "bus", "ferry"].forEach(mode => {
        modeMarkers[mode].forEach(m => map.removeLayer(m));
        modeMarkers[mode] = [];
    });
    if (polygonLayer) { map.removeLayer(polygonLayer); polygonLayer = null; }
}

// ── Render stops ──────────────────────────────────────────────────────────────
function renderStops(stops, fromSearch) {
    stops.forEach((stop, index) => {
        const mode      = stop.mode;
        const colour    = MODE_COLOURS[mode] || "#888";
        const checked   = document.getElementById(`mode-${mode}`)?.checked ?? true;
        const isNearest = fromSearch && index === 0;

        let detail = "";
        if (fromSearch) {
            detail = stop.journey_minutes !== undefined
                ? `Journey: ${stop.journey_minutes} mins`
                : `Distance: ${stop.distance_km} km`;
        }

        const popup = fromSearch
            ? `<b>${stop.name || "Unknown"}</b><br>${isNearest ? "⭐ Nearest<br>" : ""}Mode: ${stop.mode}<br>${detail}`
            : `<b>${stop.name || "Unknown"}</b><br>Mode: ${stop.mode}`;

        const marker = L.circleMarker([stop.lat, stop.lng], {
            radius:      isNearest ? 10 : 5,
            color:       colour,
            fillColor:   isNearest ? "#ffcc00" : colour,
            fillOpacity: fromSearch ? 0.9 : 0.45,
            weight:      isNearest ? 3 : 1,
        }).bindPopup(popup);

        if (checked || isNearest) marker.addTo(map);
        if (isNearest) marker.openPopup();

        if (modeMarkers[mode]) modeMarkers[mode].push(marker);
    });
}

// ── Render ORS isochrone ──────────────────────────────────────────────────────
function renderIsochrone(geojson, value) {
    if (!geojson?.features?.length) return;
    polygonLayer = L.geoJSON(geojson, {
        style: { color: "#00aa77", weight: 2, fillColor: "#00aa77", fillOpacity: 0.1 }
    }).addTo(map).bindPopup("Reachable area");
}

// ── Update legend count ───────────────────────────────────────────────────────
function updateLegend(stops, data) {
    const el = document.getElementById("stop-count");
    if (!el) return;
    const label = data.filterType === "distance"
        ? `${stops.length} stops within ${data.maxKm} km`
        : `${stops.length} stops within ${data.maxMinutes} mins`;
    el.textContent = label;
}

// ── Render full search results ────────────────────────────────────────────────
function renderMap(data) {
    clearMap();
    renderIsochrone(data.isochrone, data.maxMinutes || data.maxKm);
    renderStops(data.stops, true);
    updateLegend(data.stops, data);

    if (data.stops.length > 0) {
        map.panTo([data.stops[0].lat, data.stops[0].lng], { animate: true, duration: 1 });
    }
}

// ── Run search ────────────────────────────────────────────────────────────────
async function runSearch(lat, lng, maxMinutes, maxKm, filterType, modes, departTime) {
    const statusEl  = document.getElementById("status");
    const btn       = document.getElementById("search-btn");
    const cancelBtn = document.getElementById("cancel-btn");

    currentSearchId      = crypto.randomUUID();
    statusEl.textContent = filterType === "time" ? "Querying TfL API…" : "Calculating distances…";
    btn.disabled         = true;
    cancelBtn.disabled   = filterType !== "time";
    abortController      = new AbortController();

    try {
        const response = await fetch("/run", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
                lat,
                lng,
                maxMinutes,
                maxKm,
                filterType,
                modes,
                departTime,                 // ← departure time passed to Flask
                searchId: currentSearchId,
            }),
            signal: abortController.signal,
        });

        if (!response.ok) throw new Error(`Server error: ${response.status}`);
        const data = await response.json();

        if (data.cancelled) {
            statusEl.textContent = "Search cancelled.";
            return;
        }

        statusEl.textContent = "";
        renderMap(data);
    } catch (err) {
        if (err.name === "AbortError") return;
        console.error(err);
        statusEl.textContent = `Error: ${err.message}`;
    } finally {
        btn.disabled       = false;
        btn.textContent    = "SEARCH";
        cancelBtn.disabled = true;
        abortController    = null;
    }
}