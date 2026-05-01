import csv
from re import search
import pandas as pd
import os
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, jsonify
import uuid
import threading
import math
import random
from datetime import datetime

# =============================================================================
# CSV LOAD
# =============================================================================

def load_stops_from_csv(filename="490Stops.csv"):
    station_stops = []
    unique_stations = set()  # track unique station names, removes multiple of one station due to multiple exits/entrances

    try:
        with open(filename, newline="", encoding="cp1252", errors="ignore") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    lng  = float(row["Longitude"])
                    lat  = float(row["Latitude"])
                    mode = row.get("Mode", "").strip().lower()
                    name = row.get("CommonName", "").strip()

                    if lng != 0 and lat != 0 and name and mode:
                        key =(name.lower(), mode) #normalise all the station names

                        if key not in unique_stations:
                            station_stops.append({
                                "longitude": lng,
                                "latitude":  lat,
                                "mode":      mode,
                                "name":      name,
                            })
                            unique_stations.add(key)  # mark as gathered

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
# DISTANCE BASED FILTER - JOURNEY PLANNER NO API
# =============================================================================

# ── haversine for distance filtering ─────────────────────────────────────────
def haversine_km(lat1, lng1, lat2, lng2):
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng/2)**2
    return R * 2 * math.asin(math.sqrt(a))

# ── distance-based filter (no API calls) ─────────────────────────────────────
def filter_stops_by_distance(dist_stops, user_lat, user_lng, user_max_km):
    distance_filtered_results = []
    for station in dist_stops:
        station_dist = haversine_km(user_lat, user_lng, station["latitude"], station["longitude"])
        if station_dist <= user_max_km:
            distance_filtered_results.append({**station, "distance_km": round(station_dist, 2)})
    distance_filtered_results.sort(key=lambda s: s["distance_km"])
    return distance_filtered_results


# =============================================================================
# TfL JOURNEY PLANNER API
# =============================================================================

def load_api_key(filename):
    with open(filename) as f:
        return f.read().strip()

TFL_API_KEY = load_api_key("TFL_API_KEY.txt")
ORS_API_KEY = load_api_key("OPS_API_KEY")

BUS_SAMPLE_SIZE = 300 #to reduce bus stop API calls, random set of 300 stops only to be used

def get_tfl_journey_minutes(origin_lat, origin_lng, dest_lat, dest_lng, mode, depart_time):
    """Returns journey time in minutes from TfL Journey Planner API."""
    MODE_MAP = {
        "bus":  "bus",
        "tube": "tube",
        "rail": "national-rail",
        "ferry": "river-bus",
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
            response = requests.get(url, params=params, timeout=30)

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
            journeys = response.json().get("journeys", []) #check with ethan about this ...why is this get white? indentation error?
            if journeys:
                return journeys[0]["duration"]

        except requests.exceptions.Timeout:
            print(f"Timeout for ({dest_lat}, {dest_lng}) — skipping")
            return None
        except requests.RequestException as e:
            print(f"TfL API error for ({dest_lat}, {dest_lng}): {e}")
            return None

    return None

# ── Active search registry ─────────────────────────────────────────────────────
active_searches = {}
active_searches_lock = threading.Lock()

def is_search_active(search_id):
    with active_searches_lock:
        return active_searches.get(search_id, False)

def register_search(search_id):
    with active_searches_lock:
        active_searches[search_id] = True

def cancel_search(search_id):
    with active_searches_lock:
        active_searches[search_id] = False

def cleanup_search(search_id):
    with active_searches_lock:
        active_searches.pop(search_id, None)

#───────── filter_stops_by_time to accept and check search_id ───────────────────────────

    
def filter_stops_by_time(user_time_specific_stops, user_lat, user_lng, max_user_commute_minutes,search_id, depart_time = None):
    user_commute_time_results = []

    def check_stops(commute_stops):
        if not is_search_active(search_id):     #check that the search hasn't been cancelled by user before each API call
            return None
        time.sleep(0.1)    # 500 req/min limit — 0.1s gives safety buffer
        user_mins = get_tfl_journey_minutes(
            user_lat, user_lng,
            commute_stops["latitude"], commute_stops["longitude"],
            commute_stops["mode"],
            depart_time = depart_time
        )
        if user_mins is not None and user_mins <= max_user_commute_minutes:
            return {**commute_stops, "journey_minutes": user_mins}
        return None

    print(f"Querying TfL API for {len(user_time_specific_stops)} stops concurrently...")
    with ThreadPoolExecutor(max_workers=6) as executor:
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

def get_ors_isochrone(lat, lng, value, filter_type="time"):
    url = "https://api.openrouteservice.org/v2/isochrones/driving-car"
    headers = {
        "Authorization": ORS_API_KEY,
        "Content-Type":  "application/json",
    }
    body = {
        "locations":  [[lng, lat]],
        "range":      [value * 60 if filter_type == "time" else value * 1000],  # allows seconds for time filter or metres for distance filter
        "range_type": "time" if filter_type == "time" else "distance",
        "smoothing":  0.5,
    }
    try:
        response = requests.post(url, json=body, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"ORS isochrone error: {e}")
        return None


# =============================================================================
# FLASK
# =============================================================================

app = Flask(__name__)

# -------load CSV once at startup------------
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
    max_minutes = float(data["maxMinutes"]) #max time entered by user to find max commuting area base on time
    modes       = [m.strip().lower() for m in data["modes"].split(",") if m.strip()] #user can filter based on mode of transport
    filter_type = data.get("filterType", "time") #user can filter based on time or distance
    search_id   = data.get("searchId") #

    depart_time_str = data.get("departTime")    
    depart_time = None
    if depart_time_str:
        today = datetime.now().date()
        depart_time = datetime.strptime(f"{today} {depart_time_str}", "%Y-%m-%d %H:%M")


    register_search(search_id)
    try:
        mode_filtered = filter_stops_by_mode(tfl_stations, mode=modes)

        if filter_type == "distance":
            max_km    = float(data["maxKm"])
            reachable = filter_stops_by_distance(mode_filtered, user_lat, user_lng, max_km)
            isochrone = get_ors_isochrone(user_lat, user_lng, max_km)   # use km for ORS too

            return jsonify({
                "origin":     {"lat": user_lat, "lng": user_lng},
                "filterType": "distance",
                "maxKm":      max_km,
                "isochrone":  isochrone,
                "stops": [
                    {
                        "name":        s["name"],
                        "lat":         s["latitude"],
                        "lng":         s["longitude"],
                        "mode":        s["mode"],
                        "distance_km": s["distance_km"],
                    }
                    for s in reachable
                ]
            })

        else:
            max_minutes = float(data["maxMinutes"])

# ── sample bus stops to reduce API calls ─────────────────────────────
            bus_stops   = [s for s in mode_filtered if s["mode"] == "bus"]
            non_bus_stops = [s for s in mode_filtered if s["mode"] != "bus"]

# split bus stops into walkable and non-walkable
            walkable_bus_stops    = []
            non_walkable_bus_stops = []
            WALK_SPEED_KMH = 5 #average persons walk speed in km/hr
            WALK_THRESHOLD_KM = (10 / 60) * WALK_SPEED_KMH  # 10 min walk ≈ 0.83km

            for sampled_stop in bus_stops:
                dist = haversine_km(user_lat, user_lng, sampled_stop["latitude"], sampled_stop["longitude"])
                if dist <= WALK_THRESHOLD_KM:
                    walkable_bus_stops.append({
                        **sampled_stop,
                        "journey_minutes": round((dist / WALK_SPEED_KMH) * 60, 1)  # estimated walk time
                    })
                else:
                    non_walkable_bus_stops.append(sampled_stop)

            print(f"Bus stops within walking distance: {len(walkable_bus_stops)}")
            print(f"Bus stops outside walking distance: {len(non_walkable_bus_stops)}")


            if len(non_walkable_bus_stops) > BUS_SAMPLE_SIZE:
                print(f"Sampling {BUS_SAMPLE_SIZE} from {len(non_walkable_bus_stops)} bus stops")
                non_walkable_bus_stops = random.sample(non_walkable_bus_stops, BUS_SAMPLE_SIZE)

            all_modes_non_walkable_stops = non_bus_stops + non_walkable_bus_stops #dataset of all rail, tube and non-walkable bus stops
            api_reachable   = filter_stops_by_time(all_modes_non_walkable_stops, user_lat, user_lng, max_minutes, search_id, depart_time=depart_time)

# combine API results with walkable stops (already have journey_minutes)
            user_reachable = api_reachable + [
                s for s in walkable_bus_stops
                if s["journey_minutes"] <= max_minutes
            ]
            user_reachable.sort(key=lambda s: s["journey_minutes"]) 

            if not is_search_active(search_id):
                return jsonify({"cancelled": True}), 200

            isochrone = get_ors_isochrone(user_lat, user_lng, max_minutes)

            return jsonify({
                "origin":     {"lat": user_lat, "lng": user_lng},
                "filterType": "time",
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
                    for s in user_reachable
                ]
            })

    finally:
        cleanup_search(search_id)

        #print(f"Walkable bus stops included: {len([s for s in walkable_bus_stops if s['journey_minutes'] <= max_minutes])}")
        #print(f"API reachable stops: {len(api_reachable)}")
        #print(f"Total reachable: {len(user_reachable)}")

# ── Add cancel endpoint ──────────────────────────────
@app.route("/cancel", methods=["POST"])
def cancel():
    search_id = request.json.get("searchId")
    cancel_search(search_id)
    return jsonify({"cancelled": True})


@app.route("/stops", methods=["GET"])
def stops():
    return jsonify([
        {
            "name": s["name"] or "Unknown", #unknown if the name field is empty from CSV
            "lat":  s["latitude"],
            "lng":  s["longitude"],
            "mode": s["mode"],
        }
        for s in tfl_stations
    ])


# =============================================================================
# RUN
# =============================================================================

if __name__ == "__main__":
    app.run(debug=True)