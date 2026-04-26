let map = L.map('map').setView([51.5074, -0.1278], 12);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap'
}).addTo(map);

let points = [];
let routeLine = null;
let markers = [];
let isoLayer = null;
let heatLayer = null;

function toggleTransportFilters() {
    const mode = document.getElementById("mode").value;
    document.getElementById("transportFilters").style.display =
        mode === "public-transport" ? "block" : "none";
}

function toggleFilterInputs() {
    const filterType = document.getElementById("filterType").value;
    document.getElementById("timeInput").style.display =
        filterType === "time" ? "inline" : "none";
    document.getElementById("distanceInput").style.display =
        filterType === "distance" ? "inline" : "none";
}

function setNow() {
    const now = new Date();
    const dateStr = now.toISOString().split("T")[0];
    const timeStr = now.toTimeString().slice(0, 5);
    document.getElementById("departureDate").value = dateStr;
    document.getElementById("departureTime").value = timeStr;
}

function getDeparture() {
    const date = document.getElementById("departureDate").value;
    const time = document.getElementById("departureTime").value;
    return {
        departure_date: date || null,
        departure_time: time ? time.replace(":", "") : null
    };
}

function getSelectedTransportModes() {
    const checkboxes = document.querySelectorAll("#transportFilters input[type=checkbox]");
    const selected = [];
    checkboxes.forEach(cb => {
        if (cb.checked) selected.push(cb.value);
    });
    return selected.length > 0
        ? selected.join(",")
        : "tube,bus,national-rail,overground,elizabeth-line,tram";
}

document.getElementById("mapMode").addEventListener("change", function() {
    document.getElementById("isoOptions").style.display =
        this.value === "isochrone" ? "inline" : "none";
    clearAll();
});

map.on('click', function(e) {
    const lat = e.latlng.lat;
    const lng = e.latlng.lng;
    const mapMode = document.getElementById("mapMode").value;

    if (mapMode === "isochrone") {
        clearAll();
        markers.push(L.marker([lat, lng]).addTo(map));
        getIsochrone([lng, lat]);
        return;
    }

    points.push([lng, lat]);
    markers.push(L.marker([lat, lng]).addTo(map));

    if (points.length === 2) {
        getRoute();
    }
});

function clearMarkers() {
    markers.forEach(m => map.removeLayer(m));
    markers = [];
}

function clearAll() {
    clearMarkers();
    points = [];
    if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
    if (isoLayer) { map.removeLayer(isoLayer); isoLayer = null; }
    if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }
    document.getElementById("info").innerText = "";
}

function getHeatColor(ratio) {
    // 0.0 = green, 0.5 = yellow, 1.0 = red
    let r, g;
    if (ratio < 0.5) {
        r = Math.round(255 * (ratio * 2));
        g = 255;
    } else {
        r = 255;
        g = Math.round(255 * (1 - (ratio - 0.5) * 2));
    }
    return `rgb(${r},${g},0)`;
}

function formatJourneyBreakdown(legs, totalDuration) {
    if (!legs || legs.length === 0) {
        return `⏱ Estimated time: ${totalDuration} minutes`;
    }

    const modeEmojis = {
        "tube": "🚇",
        "bus": "🚌",
        "national-rail": "🚆",
        "overground": "🟠",
        "elizabeth-line": "🟣",
        "tram": "🚊",
        "walking": "🚶",
        "cycle": "🚴",
        "unknown": "🚍"
    };

    let lines = ["<strong>Journey breakdown:</strong>"];
    legs.forEach(leg => {
        const emoji = modeEmojis[leg.mode] || modeEmojis["unknown"];
        const lineName = leg.line ? ` (${leg.line})` : "";
        lines.push(`${emoji} ${leg.duration} mins ${leg.mode}${lineName}`);
    });
    lines.push(`<strong>⏱ Total: ${totalDuration} minutes</strong>`);
    return lines.join("<br>");
}

function getRoute() {
    const mode = document.getElementById("mode").value;
    const transportModes = mode === "public-transport" ? getSelectedTransportModes() : null;
    const departure = getDeparture();

    return fetch("/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            start: points[0],
            end: points[1],
            mode: mode,
            transport_modes: transportModes,
            departure_date: departure.departure_date,
            departure_time: departure.departure_time
        })
    })
    .then(async res => {
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    })
    .then(data => {
        if (!data.route) {
            document.getElementById("info").innerText = "Route failed.";
            return;
        }
        let coords = data.route.map(c => [c[1], c[0]]);
        if (routeLine) map.removeLayer(routeLine);
        routeLine = L.polyline(coords, {
            color: "blue",
            interactive: false
        }).addTo(map);
        map.fitBounds(routeLine.getBounds());
        document.getElementById("info").innerHTML =
            formatJourneyBreakdown(data.legs, data.duration);
    })
    .catch(err => {
        console.error(err);
        document.getElementById("info").innerText = "Error getting route.";
    })
    .finally(() => { clearMarkers(); points = []; });
}

function getIsochrone(coord) {
    const mode = document.getElementById("mode").value;
    const transportModes = mode === "public-transport" ? getSelectedTransportModes() : null;
    const filterType = document.getElementById("filterType") ?
        document.getElementById("filterType").value : "time";
    const minutes = filterType === "time" ?
        parseInt(document.getElementById("isoTime").value) : null;
    const distance = filterType === "distance" ?
        parseFloat(document.getElementById("isoDistance").value) : null;
    const departure = getDeparture();
    const showHeatmap = document.getElementById("heatmapToggle") ?
        document.getElementById("heatmapToggle").checked : false;

    document.getElementById("info").innerText = "⏳ Calculating commute zone...";

    fetch("/isochrone", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            coord: coord,
            mode: mode,
            minutes: minutes,
            distance_km: distance,
            filter_type: filterType,
            transport_modes: transportModes,
            departure_date: departure.departure_date,
            departure_time: departure.departure_time
        })
    })
    .then(async res => {
        if (!res.ok) throw new Error(await res.text());
        return res.json();
    })
    .then(data => {
        if (!data.polygon) {
            document.getElementById("info").innerText = "Commute zone failed.";
            return;
        }

        if (isoLayer) { map.removeLayer(isoLayer); isoLayer = null; }
        if (heatLayer) { map.removeLayer(heatLayer); heatLayer = null; }

        const polyCoords = data.polygon.map(c => [c[1], c[0]]);

        if (showHeatmap && data.heatmap_points && data.heatmap_points.length > 0) {
            // outline only polygon - no fill, passes clicks through
            isoLayer = L.polygon(polyCoords, {
                color: "blue",
                fillOpacity: 0,
                weight: 2,
                interactive: false
            }).addTo(map);

            // coloured circles per stop
            const maxMinutes = minutes || 60;
            const heatGroup = L.layerGroup();

            data.heatmap_points.forEach(point => {
                const lng = point[0];
                const lat = point[1];
                const duration = point[2];
                const ratio = Math.min(duration / maxMinutes, 1.0);
                const color = getHeatColor(ratio);

                L.circleMarker([lat, lng], {
                    radius: 8,
                    color: color,
                    fillColor: color,
                    fillOpacity: 0.7,
                    weight: 1,
                    interactive: true
                }).bindTooltip(`${Math.round(duration)} mins`).addTo(heatGroup);
            });

            heatLayer = heatGroup.addTo(map);
            map.fitBounds(isoLayer.getBounds());

        } else {
            // standard filled polygon
            isoLayer = L.polygon(polyCoords, {
                color: "blue",
                fillColor: "#0066ff",
                fillOpacity: 0.2,
                weight: 2,
                fill: true,
                interactive: false
            }).addTo(map);
            map.fitBounds(isoLayer.getBounds());
        }

        const label = filterType === "distance"
            ? `🗺️ Commute zone within ${distance} km`
            : `🗺️ Commute zone reachable within ${minutes} minutes`;
        document.getElementById("info").innerText = label;
    })
    .catch(err => {
        console.error(err);
        document.getElementById("info").innerText = "Error calculating commute zone.";
    });
}