"""
CPCB AQI Scraper — Fetches real-time AQI data from data.gov.in

Endpoint: https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69
Free API — register at https://data.gov.in → find "CPCB Real Time AQI Data"

Response fields per station record:
    Country, State, City, Station, Last Update, Latitude, Longitude,
    Pollutant Id, Pollutant Min, Pollutant Max, Pollutant Avg

Strategy:
    1. Fetch all Karnataka stations (State=Karnataka filter)
    2. Pivot the flat rows into one dict per station (multiple pollutants per station)
    3. Compute AQI from the dominant pollutant using CPCB breakpoint table
    4. Return list of station dicts compatible with WAQI format for merge
"""

import logging
import math
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CPCB_API_URL = "https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"

# Bangalore bounding box — used to filter CPCB results spatially
BBOX = {
    "min_lat": 12.85, "max_lat": 13.15,
    "min_lon": 77.45, "max_lon": 77.78,
}

# ── CPCB AQI sub-index breakpoints ────────────────────────────────────
# Source: CPCB AQI calculation bulletin
# Format: [(conc_lo, conc_hi, aqi_lo, aqi_hi), ...]
_PM25_BP = [
    (0,    30,   0,   50),
    (30,   60,   51,  100),
    (60,   90,  101,  200),
    (90,  120,  201,  300),
    (120, 250,  301,  400),
    (250, 500,  401,  500),
]
_PM10_BP = [
    (0,    50,   0,   50),
    (50,  100,  51,  100),
    (100, 250, 101,  200),
    (250, 350, 201,  300),
    (350, 430, 301,  400),
    (430, 600, 401,  500),
]
_NO2_BP = [
    (0,   40,   0,  50),
    (40,  80,  51, 100),
    (80, 180, 101, 200),
    (180, 280, 201, 300),
    (280, 400, 301, 400),
    (400, 800, 401, 500),
]
_SO2_BP = [
    (0,   40,   0,  50),
    (40,   80,  51, 100),
    (80,  380, 101, 200),
    (380, 800, 201, 300),
    (800,1600, 301, 400),
    (1600,2400,401, 500),
]
_O3_BP = [
    (0,   50,   0,  50),
    (50,  100,  51, 100),
    (100, 168, 101, 200),
    (168, 208, 201, 300),
    (208, 748, 301, 400),
    (748, 1000,401, 500),
]

POLLUTANT_BREAKPOINTS = {
    "PM2.5": _PM25_BP,
    "PM10":  _PM10_BP,
    "NO2":   _NO2_BP,
    "SO2":   _SO2_BP,
    "OZONE": _O3_BP,
}


def _compute_sub_index(concentration: float, breakpoints: list) -> Optional[float]:
    """Linear interpolation within CPCB breakpoint range."""
    for c_lo, c_hi, i_lo, i_hi in breakpoints:
        if c_lo <= concentration <= c_hi:
            return i_lo + (concentration - c_lo) * (i_hi - i_lo) / (c_hi - c_lo)
    if concentration > breakpoints[-1][1]:
        return 500.0  # Beyond scale
    return None


def compute_cpcb_aqi(pollutants: dict) -> Optional[float]:
    """
    Compute overall AQI as the maximum sub-index across all measured pollutants.
    This follows the CPCB AQI methodology (dominant pollutant approach).
    """
    sub_indices = []
    for pollutant_id, bps in POLLUTANT_BREAKPOINTS.items():
        avg = pollutants.get(pollutant_id)
        if avg is not None:
            si = _compute_sub_index(float(avg), bps)
            if si is not None:
                sub_indices.append(si)
    return round(max(sub_indices), 1) if sub_indices else None


def _in_bangalore_bbox(lat: float, lon: float) -> bool:
    return (BBOX["min_lat"] <= lat <= BBOX["max_lat"] and
            BBOX["min_lon"] <= lon <= BBOX["max_lon"])


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two WGS-84 points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def fetch_cpcb_stations(api_key: str) -> list[dict]:
    """
    Fetch all Bangalore-area CPCB stations from data.gov.in.

    Returns a list of station dicts:
        {
          "uid": str,          # e.g. "cpcb_BTM"
          "name": str,
          "city": str,
          "lat": float,
          "lon": float,
          "aqi": float | None,
          "pm25": float | None,
          "pm10": float | None,
          "no2":  float | None,
          "so2":  float | None,
          "o3":   float | None,
          "pm25_24h_avg": float | None,
          "last_update": str,
          "source": "cpcb",
        }
    """
    params = {
        "api-key":  api_key,
        "format":   "json",
        "filters[State]": "Karnataka",
        "limit":    500,
        "offset":   0,
    }

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(CPCB_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        logger.error(f"[cpcb] HTTP error: {exc}")
        return []

    records = data.get("records", [])
    if not records:
        logger.warning("[cpcb] No records returned from data.gov.in CPCB endpoint.")
        return []

    # Pivot: group records by (City, Station, Latitude, Longitude) — multiple rows per station
    by_station: dict[str, dict] = {}
    for rec in records:
        try:
            lat = float(rec.get("latitude") or 0)
            lon = float(rec.get("longitude") or 0)
        except (TypeError, ValueError):
            continue

        if not _in_bangalore_bbox(lat, lon):
            continue

        station_name = rec.get("station", "Unknown")
        city         = rec.get("city", "")
        key = f"{city}|{station_name}|{lat:.4f}|{lon:.4f}"

        if key not in by_station:
            by_station[key] = {
                "uid":          f"cpcb_{station_name.replace(' ', '_').lower()[:30]}",
                "name":         station_name,
                "city":         city,
                "lat":          lat,
                "lon":          lon,
                "pollutants":   {},  # pollutant_id → avg
                "last_update":  rec.get("last_update", ""),
                "source":       "cpcb",
            }

        pol_id  = (rec.get("pollutant_id") or "").upper().strip()
        pol_avg = rec.get("pollutant_avg")
        if pol_id and pol_avg not in (None, "", "NA"):
            try:
                by_station[key]["pollutants"][pol_id] = float(pol_avg)
            except (TypeError, ValueError):
                pass

    # Build final station list
    stations = []
    for s in by_station.values():
        pols = s["pollutants"]
        aqi  = compute_cpcb_aqi(pols)
        stations.append({
            "uid":          s["uid"],
            "name":         s["name"],
            "city":         s["city"],
            "lat":          s["lat"],
            "lon":          s["lon"],
            "aqi":          aqi,
            "pm25":         pols.get("PM2.5"),
            "pm10":         pols.get("PM10"),
            "no2":          pols.get("NO2"),
            "so2":          pols.get("SO2"),
            "o3":           pols.get("OZONE"),
            "pm25_24h_avg": pols.get("PM2.5"),  # CPCB reports 24h rolling avg
            "last_update":  s["last_update"],
            "source":       "cpcb",
        })

    logger.info(f"[cpcb] Fetched {len(stations)} Bangalore-area stations.")
    return stations


def merge_cpcb_waqi(
    cpcb_stations: list[dict],
    waqi_stations: list[dict],
    match_radius_km: float = 0.5,
) -> list[dict]:
    """
    Merge CPCB and WAQI station lists.

    Strategy:
    - For each CPCB station, check if any WAQI station is within match_radius_km.
    - If a match exists: CPCB wins for recency; WAQI fills pollutants CPCB lacks.
    - All un-matched WAQI stations are appended (CPCB doesn't have full coverage).
    - Result is annotated with source='cpcb', 'waqi', or 'merged'.
    """
    merged: list[dict] = []
    waqi_used: set[int] = set()

    for cs in cpcb_stations:
        best_waqi = None
        best_dist = float("inf")

        for i, ws in enumerate(waqi_stations):
            dist = _haversine_km(cs["lat"], cs["lon"], ws["lat"], ws["lon"])
            if dist < match_radius_km and dist < best_dist:
                best_waqi = (i, ws)
                best_dist = dist

        if best_waqi is not None:
            idx, ws = best_waqi
            waqi_used.add(idx)
            # CPCB takes priority; fill any None fields from WAQI
            merged.append({
                **ws,
                **{k: v for k, v in cs.items() if v is not None},
                "source": "merged",
            })
        else:
            merged.append({**cs, "source": "cpcb"})

    for i, ws in enumerate(waqi_stations):
        if i not in waqi_used:
            merged.append({**ws, "source": "waqi"})

    logger.info(
        f"[cpcb] Merge: {len(cpcb_stations)} CPCB + {len(waqi_stations)} WAQI "
        f"→ {len(merged)} merged stations."
    )
    return merged
