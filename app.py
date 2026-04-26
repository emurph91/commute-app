from flask import Flask, render_template, request, jsonify
import requests
import polyline
import os
import math
import ast
import time
import csv
import numpy as np
from scipy.spatial import ConvexHull
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)


# =========================
# CONFIG
# =========================
def normalize_ors_api_key(raw_value):
    if not raw_value:
        return None
    for line in raw_value.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        if "=" in cleaned:
            cleaned = cleaned.split("=", 1)[1].strip()
        if (cleaned.startswith('"') and cleaned.endswith('"')) or (
            cleaned.startswith("'") and cleaned.endswith("'")
        ):
            cleaned = cleaned[1:-1].strip()
        if cleaned:
            return cleaned
    return None


def load_ors_api_key():
    env_key = os.getenv("ORS_API_KEY")
    if env_key:
        return normalize_ors_api_key(env_key)
    for filename in ("ORS_API_KEY", "OPS_API_KEY"):
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as key_file:
                return normalize_ors_api_key(key_file.read())
    return None


API_KEY = load_ors_api_key()
DURATION_CACHE = {}


# =========================
# UTIL
# =========================
def haversine_km(lng1, lat1, lng2, lat2):
    dx = math.radians(lat2 - lat1)
    dy = math.radians(lng2 - lng1)
    a = (math.sin(dx/2)**2 +
         math.cos(math.radians(lat1)) *
         math.cos(math.radians(lat2)) *
         math.sin(dy/2)**2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371 * c


def estimate_time(dist_km):
    avg_speed = 25  # km/h mixed London transport
    return (dist_km / avg_speed) * 60  # minutes


def parse_retry_after(response):
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return max(0.0, float(retry_after))
    except ValueError:
        return None


def rate_limited_request(
    method,
    url,
    *,
    backoff_base_seconds=0.5,
    max_backoff_seconds=10.0,
    **kwargs,
):
    attempt = 0
    while True:
        response = requests.request(method, url, **kwargs)
        if response.status_code != 429:
            return response
        retry_delay = parse_retry_after(response)
        if retry_delay is None:
            retry_delay = min(backoff_base_seconds * (2 ** attempt), max_backoff_seconds)
        time.sleep(retry_delay)
        attempt += 1


def extract_tfl_error(data):
    if not isinstance(data, dict):
        return "Unexpected TfL response format."
    if data.get("message"):
        return data["message"]
    if data.get("httpStatusCode") and data.get("httpStatus"):
        return f"{data['httpStatusCode']} {data['httpStatus']}"
    if "journeys" not in data or not data["journeys"]:
        return "TfL returned no journeys for those points."
    return "TfL route parsing failed."


def extract_ors_error(data, status_code=None):
    if not API_KEY:
        return "OpenRouteService API key not found."
    if not isinstance(data, dict):
        return "Unexpected OpenRouteService response format."
    error = data.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("code")
        if message:
            return f"OpenRouteService error: {message}"
    if isinstance(error, str):
        return f"OpenRouteService error: {error}"
    if data.get("message"):
        return f"OpenRouteService error: {data['message']}"
    if status_code:
        return f"OpenRouteService request failed with status {status_code}."
    return "OpenRouteService route parsing failed."


# =========================
# CSV LOAD
# =========================

# NaPTAN StopType values mapped to TfL mode names
NAPTAN_MODE_MAP = {
    "tube": ["tube"],
    "national-rail": ["rail"],
    "overground": ["rail"],
    "elizabeth-line": ["rail", "tube"],
    "bus": ["bus"],
    "tram": ["bus"],
}

def get_allowed_stop_types(transport_modes):
    allowed = set()
    for mode in transport_modes.split(","):
        mode = mode.strip().lower()
        for stop_type in NAPTAN_MODE_MAP.get(mode, []):
            allowed.add(stop_type)
    return allowed

def load_stops_from_csv(filename="naptan_2026_london.csv"):
    stops = []
    try:
        with open(filename, newline="", encoding="cp1252", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    lng = float(row["Longitude"])
                    lat = float(row["Latitude"])
                    mode = row.get("Mode", "").strip().lower()  # ← read mode column
                    if lng != 0 and lat != 0:
                        stops.append([lng, lat, mode])              # ← store mode
                except (ValueError, KeyError, TypeError):
                    continue
    except Exception as e:
        print("CSV LOAD ERROR:", e)
    return stops


STOPS = load_stops_from_csv()
print(f"Loaded {len(STOPS)} stops from CSV")

# =========================
# NEARBY STOPS
# =========================
def get_nearby_stops(origin_lng, origin_lat, radius_m, transport_modes=None):
    allowed_types = get_allowed_stop_types(transport_modes) if transport_modes else None
    nearby = []
    for stop in STOPS:
        lng, lat, stop_type = stop[0], stop[1], stop[2]

        # filter by stop type if modes specified
        if allowed_types and stop_type not in allowed_types:
            continue

        if haversine_km(origin_lng, origin_lat, lng, lat) * 1000 <= radius_m:
            nearby.append([lng, lat])
    return nearby

# =========================
# ORS ROUTES
# =========================
def get_route(start, end, mode="driving-car"):
    url = f"https://api.openrouteservice.org/v2/directions/{mode}"
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}
    body = {"coordinates": [start, end]}

    response = rate_limited_request("POST", url, json=body, headers=headers, timeout=20)
    data = response.json()

    try:
        if "routes" not in data or not data["routes"]:
            error_message = extract_ors_error(data, response.status_code)
            print("ORS ERROR:", error_message)
            return {"error": error_message}

        route_data = data["routes"][0]
        duration = route_data["summary"]["duration"]
        coords = polyline.decode(route_data["geometry"])
        route = [[lng, lat] for lat, lng in coords]
        return {"route": route, "duration": round(duration / 60, 2), "legs": None}
    except Exception as e:
        error_message = f"OpenRouteService route parsing failed: {e}"
        print("ORS ERROR:", error_message)
        return {"error": error_message}


# =========================
# TfL ROUTE
# =========================
def get_tfl_route(
    start,
    end,
    transport_modes="tube,bus,national-rail,overground,elizabeth-line,tram",
    departure_date=None,
    departure_time=None,
):
    from_str = f"{start[1]},{start[0]}"
    to_str = f"{end[1]},{end[0]}"
    url = f"https://api.tfl.gov.uk/Journey/JourneyResults/{from_str}/to/{to_str}"

    full_modes = transport_modes + ",walking"
    params = {
        "nationalSearch": "true",
        "mode": full_modes,
        "maxWalkingMinutes": "30",
        "walkingSpeed": "Average"
    }

    if departure_date:
        params["date"] = departure_date.replace("-", "")
    if departure_time:
        params["time"] = departure_time

    response = rate_limited_request("GET", url, params=params, timeout=20)
    data = response.json()

    try:
        if "journeys" not in data or not data["journeys"]:
            error_message = extract_tfl_error(data)
            print("TFL ERROR:", error_message)
            return {"error": error_message}

        journey = data["journeys"][0]
        duration = journey["duration"]

        coords = []
        for leg in journey["legs"]:
            line_string = leg.get("path", {}).get("lineString")
            if not line_string:
                continue
            path_points = ast.literal_eval(line_string)
            for lat, lon in path_points:
                point = [lon, lat]
                if not coords or coords[-1] != point:
                    coords.append(point)

        if not coords:
            return {"error": "TfL returned a journey but no drawable path coordinates."}

        legs = []
        for leg in journey["legs"]:
            mode = leg.get("mode", {}).get("id", "unknown").lower()
            leg_duration = leg.get("duration", 0)
            line = None
            if leg.get("routeOptions"):
                for option in leg["routeOptions"]:
                    if option.get("lineIdentifier", {}).get("name"):
                        line = option["lineIdentifier"]["name"]
                        break
            legs.append({"mode": mode, "duration": leg_duration, "line": line})

        return {"route": coords, "duration": duration, "legs": legs}

    except Exception as e:
        error_message = f"TfL route parsing failed: {e}"
        print("TFL ERROR:", error_message)
        return {"error": error_message}


# =========================
# TfL DURATION
# =========================
def get_tfl_duration(
    origin_lng,
    origin_lat,
    dest_lng,
    dest_lat,
    mode="walking,tube,bus,elizabeth-line,overground,national-rail,tram",
    max_walking_minutes=30,
    departure_date=None,
    departure_time=None,
):
    from_str = f"{origin_lat},{origin_lng}"
    to_str = f"{dest_lat},{dest_lng}"
    url = f"https://api.tfl.gov.uk/Journey/JourneyResults/{from_str}/to/{to_str}"
    params = {
        "nationalSearch": "True",
        "mode": mode,
        "maxWalkingMinutes": str(max_walking_minutes),
        "walkingSpeed": "Average",
        "maxTransferMinutes": "30",
    }

    if departure_date:
        params["date"] = departure_date.replace("-", "")
    if departure_time:
        params["time"] = departure_time

    try:
        response = rate_limited_request("GET", url, params=params, timeout=10)
        data = response.json()
        if "journeys" not in data or not data["journeys"]:
            return {"error": extract_tfl_error(data)}
        return {"duration": data["journeys"][0]["duration"]}
    except Exception as e:
        return {"error": f"TfL duration lookup failed: {e}"}


# =========================
# TfL ISOCHRONE (HYBRID)
# =========================
def get_tfl_isochrone(
    coord,
    minutes=None,
    transport_modes="tube,bus,national-rail,overground,elizabeth-line,tram",
    distance_km=None,
    departure_date=None,
    departure_time=None,
):
    origin_lng, origin_lat = coord

    if distance_km is not None:
        radius_m = int(distance_km * 1000)
    else:
        radius_m = min(int(minutes * 700), 50000)

    # ← pass transport_modes so only relevant stop types are loaded
    stops = get_nearby_stops(origin_lng, origin_lat, radius_m, transport_modes)

    # deduplicate at ~100m grid
    stops = list({(round(s[0], 3), round(s[1], 3)) for s in stops})
    stops = [[s[0], s[1]] for s in stops]

    print(f"Total stops in radius: {len(stops)}")

    # sector sampling - low, mid, high per sector
    NUM_SECTORS = 24
    sectors = {}
    for stop in stops:
        dx = stop[0] - origin_lng
        dy = stop[1] - origin_lat
        bearing = math.degrees(math.atan2(dx, dy)) % 360
        sector = int(bearing // (360 / NUM_SECTORS))
        dist = math.sqrt(dx**2 + dy**2)
        sectors.setdefault(sector, []).append((dist, stop))

    sampled = []
    for sector_stops in sectors.values():
        sector_stops.sort(key=lambda x: x[0])
        n = len(sector_stops)
        if n == 0:
            continue
        elif n == 1:
            sampled.append(sector_stops[0][1])
        elif n == 2:
            sampled.append(sector_stops[0][1])
            sampled.append(sector_stops[-1][1])
        else:
            sampled.append(sector_stops[0][1])          # low
            sampled.append(sector_stops[n // 2][1])     # mid
            sampled.append(sector_stops[-1][1])         # high

    print(f"Checking {len(sampled)} sampled stops across {len(sectors)} sectors")

    full_modes = transport_modes + ",walking"

    def check_stop(stop):
        lng, lat = stop
        dist_km = haversine_km(origin_lng, origin_lat, lng, lat)

        # distance filter - verify stop is routable, no time check needed
        if distance_km is not None:
            result = get_tfl_duration(
                origin_lng, origin_lat, lng, lat,
                mode=full_modes,
                departure_date=departure_date,
                departure_time=departure_time
            )
            return (stop, None) if not result.get("error") else None

        # hybrid pre-filter to skip obvious cases without TfL API call
        est = estimate_time(dist_km)
        if est <= minutes * 0.6:
            return (stop, est)          # clearly reachable, no API call needed. return estimated duration time
        if est > minutes * 1.3:
            return None                 # clearly too far, no API call needed

        # uncertain band - check cache first
        cache_key = (
            round(origin_lng, 4), round(origin_lat, 4),
            round(lng, 4), round(lat, 4),
            departure_date, departure_time
        )
        if cache_key in DURATION_CACHE:
            dur = DURATION_CACHE[cache_key]
            return (stop, dur) if dur <= minutes else None

        # make TfL API call
        result = get_tfl_duration(
            origin_lng, origin_lat, lng, lat,
            mode=full_modes,
            departure_date=departure_date,
            departure_time=departure_time
        )

        if result.get("error"):
            return None

        DURATION_CACHE[cache_key] = result["duration"]
        return (stop, result["duration"]) if result["duration"] <= minutes else None

    with ThreadPoolExecutor(max_workers=24) as executor:
        results = list(executor.map(check_stop, sampled))

    reachable = [r for r in results if r is not None]

    print(f"Reachable stops: {len(reachable)}")

    if len(reachable) < 3:
        return {"error": "Not enough reachable stops to draw a zone"}

 # build polygon from stop coordinates
    coords = np.array([r[0] for r in reachable])
    hull = ConvexHull(coords)
    polygon = coords[hull.vertices].tolist()
    polygon.append(polygon[0])
    
  # build heatmap points [lng, lat, duration]
    heatmap_points = []
    for stop, duration in reachable:
        if duration is not None:
            heatmap_points.append([stop[0], stop[1], duration])

    return {"polygon": polygon, "heatmap_points": heatmap_points}

# =========================
# FLASK ROUTES
# =========================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/route", methods=["POST"], strict_slashes=False)
def get_route_data():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON received"}), 400

    start = data.get("start")
    end = data.get("end")
    mode = data.get("mode", "driving-car")
    transport_modes = data.get("transport_modes", "tube,bus,national-rail,overground,elizabeth-line,tram")
    departure_date = data.get("departure_date")
    departure_time = data.get("departure_time")

    if not start or not end:
        return jsonify({"error": "Missing start or end"}), 400

    if mode == "public-transport":
        result = get_tfl_route(start, end, transport_modes, departure_date, departure_time)
    else:
        result = get_route(start, end, mode)

    if result is None:
        return jsonify({"error": "Route failed"}), 500
    if result.get("error"):
        return jsonify({"error": result["error"]}), 500
    return jsonify(result)


@app.route("/isochrone", methods=["POST"], strict_slashes=False)
def get_isochrone():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON received"}), 400

    coord = data.get("coord")
    mode = data.get("mode", "driving-car")
    minutes = data.get("minutes")
    distance_km = data.get("distance_km")
    filter_type = data.get("filter_type", "time")
    transport_modes = data.get("transport_modes", "tube,bus,national-rail,overground,elizabeth-line,tram")
    departure_date = data.get("departure_date")
    departure_time = data.get("departure_time")

    if not coord:
        return jsonify({"error": "Missing coord"}), 400

    if mode == "public-transport":
        polygon_result = get_tfl_isochrone(
            coord,
            minutes=minutes if filter_type == "time" else None,
            transport_modes=transport_modes,
            distance_km=distance_km if filter_type == "distance" else None,
            departure_date=departure_date,
            departure_time=departure_time
        )
        if polygon_result.get("error"):
            return jsonify({"error": polygon_result["error"]}), 500
        return jsonify({"polygon": polygon_result["polygon"]})

    url = f"https://api.openrouteservice.org/v2/isochrones/{mode}"
    headers = {"Authorization": API_KEY, "Content-Type": "application/json"}

    if filter_type == "distance" and distance_km is not None:
        body = {
            "locations": [coord],
            "range": [distance_km * 1000],
            "range_type": "distance",
            "area_units": "km",
            "smoothing": 0
        }
    else:
        body = {
            "locations": [coord],
            "range": [minutes * 60],
            "range_type": "time",
            "area_units": "km",
            "smoothing": 0
        }

    try:
        response = rate_limited_request("POST", url, json=body, headers=headers, timeout=20)
        result = response.json()
        polygon = result["features"][0]["geometry"]["coordinates"][0]
        polygon.reverse()
        return jsonify({"polygon": polygon})
    except Exception as e:
        print("ISO ERROR:", e)
        return jsonify({"error": "Isochrone failed"}), 500


# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(debug=True)