import csv
import pandas as pd
import os
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify

# =============================================================================
# CSV LOAD
# =============================================================================

def load_stops_from_csv(filename="490Stops.csv"):
    station_stops = []
    try:
        with open(filename, newline="", encoding="cp1252", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    lng  = float(row["Longitude"])
                    lat  = float(row["Latitude"])
                    mode = row.get("Mode", "").strip().lower()
                    name = row.get("Name", "").strip()
                    if lng != 0 and lat != 0:
                        station_stops.append({
                            "longitude": lng,
                            "latitude":  lat,
                            "mode":      mode,
                            "name":      name,
                        })
                except (ValueError, KeyError, TypeError):
                    continue
    except Exception as e:
        print("CSV LOAD ERROR:", e)
    return station_stops


# =============================================================================
# MODE FILTER
# =============================================================================

def filter_stops_by_mode(station_stops, mode=None):
    filtered_stations = station_stops
    if mode:
        modes = [mode.strip().lower()] if isinstance(mode, str) else [m.strip().lower() for m in mode]
        filtered_stations = [s for s in filtered_stations if s["mode"] in modes]
    return filtered_stations


# =============================================================================
# TfL JOURNEY PLANNER API
# =============================================================================

def load_api_key(filename):
    with open(filename) as f:
        return f.read().strip()

TFL_API_KEY = load_api_key("TFL_API_KEY.txt")
ORS_API_KEY = load_api_key("OPS_API_KEY")


def get_tfl_journey_minutes(origin_lat, origin_lng, dest_lat, dest_lng, mode):
    """Returns journey time in minutes from TfL Journey Planner API."""
    MODE_MAP = {
        "bus":  "bus",
        "tube": "tube",
        "rail": "national-rail",
    }

    tfl_mode = MODE_MAP.get(mode, mode)
    url = (
        f"https://api.tfl.gov.uk/Journey/JourneyResults/"
        f"{origin_lat},{origin_lng}/to/{dest_lat},{dest_lng}"
    )
    params = {
        "mode":    tfl_mode,
        "app_key": TFL_API_KEY,
    }
    retries = 3
    backoff  = 2    # doubles each retry: 2s, 4s, 8s

    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=15)

            if response.status_code == 401:
                raise ValueError("Invalid API key — check TFL_API_KEY.txt")
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                wait = backoff * (2 ** attempt)
                print(f"Rate limited — waiting {wait}s before retry {attempt + 1}/{retries}")
                time.sleep(wait)
                continue

            response.raise_for_status()
            journeys = response.json().get("journeys", [])
            if journeys:
                return journeys[0]["duration"]

        except requests.exceptions.Timeout:
            print(f"Timeout for ({dest_lat}, {dest_lng}) — skipping")
            return None
        except requests.RequestException as e:
            print(f"TfL API error for ({dest_lat}, {dest_lng}): {e}")
            return None

    return None


def filter_stops_by_time(user_time_specific_stops, user_lat, user_lng, max_user_commute_minutes):
    user_commute_time_results = []

    def check_stops(commute_stops):
        time.sleep(0.15)    # 500 req/min limit — 0.15s gives safety buffer
        user_mins = get_tfl_journey_minutes(
            user_lat, user_lng,
            commute_stops["latitude"], commute_stops["longitude"],
            commute_stops["mode"]
        )
        if user_mins is not None and user_mins <= max_user_commute_minutes:
            return {**commute_stops, "journey_minutes": user_mins}
        return None

    print(f"Querying TfL API for {len(user_time_specific_stops)} stops concurrently...")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(check_stops, stop): stop for stop in user_time_specific_stops}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            print(f"Progress: {completed}/{len(user_time_specific_stops)}", end="\r")
            result = future.result()
            if result:
                user_commute_time_results.append(result)

    print()
    user_commute_time_results.sort(key=lambda s: s["journey_minutes"])
    return user_commute_time_results


# =============================================================================
# ORS ISOCHRONE
# Note: ORS does not support public transit — driving-car is used as a visual
# approximation of the reachable area. Accurate journey times come from TfL API.
# =============================================================================

def get_ors_isochrone(lat, lng, max_minutes):
    """Fetch isochrone polygon from ORS for the reachable area shape."""
    url = "https://api.openrouteservice.org/v2/isochrones/driving-car"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type":  "application/json",
    }
    body = {
        "locations":  [[lng, lat]],         # ORS uses [lng, lat] order
        "range":      [max_minutes * 60],   # ORS takes seconds not minutes
        "range_type": "time",
        "smoothing":  0.5,
    }

    try:
        response = requests.post(url, json=body, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"ORS isochrone error: {e}")
        return None


# =============================================================================
# FLASK
# =============================================================================

app = Flask(__name__)

# load CSV once at startup
tfl_stations = load_stops_from_csv()
print(f"Loaded {len(tfl_stations)} stops")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/run", methods=["POST"])
def run():
    data        = request.json
    user_lat    = float(data["lat"])
    user_lng    = float(data["lng"])
    max_minutes = float(data["maxMinutes"])
    modes       = [m.strip().lower() for m in data["modes"].split(",") if m.strip()]

    mode_filtered = filter_stops_by_mode(tfl_stations, mode=modes)
    reachable     = filter_stops_by_time(mode_filtered, user_lat, user_lng, max_minutes)
    isochrone     = get_ors_isochrone(user_lat, user_lng, max_minutes)

    return jsonify({
        "origin":     {"lat": user_lat, "lng": user_lng},
        "maxMinutes": max_minutes,
        "isochrone":  isochrone,
        "stops": [
            {
                "name":            s["name"],
                "lat":             s["latitude"],
                "lng":             s["longitude"],
                "mode":            s["mode"],
                "journey_minutes": s["journey_minutes"],
            }
            for s in reachable
        ]
    })


# =============================================================================
# RUN
# =============================================================================

if __name__ == "__main__":
    app.run(debug=True)