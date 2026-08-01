"""
SafeMAPS MCP Server
────────────────────
Exposes SafeMAPS' routing, AQI, and accident-risk logic as MCP tools so
Claude (Desktop, claude.ai, or any MCP client) can query them directly.

All tools call the same async functions the FastAPI app uses — no business
logic is duplicated, no internal HTTP hop.

Transport: streamable-http (MCP 1.2+)
  Runs as a long-lived HTTP process with a stable URL, reachable from
  claude.ai and Claude Desktop without cloning the repo.

  Local dev:
      python mcp_server.py
      # serves on http://0.0.0.0:8001/mcp

  Claude Desktop config (local):
      {
        "mcpServers": {
          "safemaps": {
            "url": "http://localhost:8001/mcp"
          }
        }
      }

  Remote (deployed):
      Replace localhost:8001 with the Railway/Fly.io URL.

This server is READ-ONLY. No tool mutates any state.
"""

import math
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from database import db
from graph_cache import graph_cache
from models import RouteProfile
from routing import find_route, get_profile_weights, haversine
from spatial_queries import get_aqi_heatmap, get_blackspots_in_bbox
from routes.aqi import list_stations
from routes.route import _parse_hour

# ── Ensure data_pipeline is importable (for LSTM predict) ───────────────
_pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
if str(_pipeline_dir) not in sys.path:
    sys.path.insert(0, str(_pipeline_dir))


# ── Lifecycle ────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server):
    """Load the road graph and DB pool before the MCP server starts accepting requests."""
    await db.connect()
    node_count = await graph_cache.load(db)
    print(f"[safemaps-mcp] graph loaded: {node_count:,} nodes", flush=True)
    yield
    # Teardown (if ever needed) goes here


# ── Server ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    "SafeMAPS",
    lifespan=lifespan,
    # Streamable-HTTP transport — reachable at /mcp
    # (FastMCP routes this automatically when run() is called with host/port)
)


# ── Shared helpers ──────────────────────────────────────────────────────

def _bbox_from_point(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """Approximate a lat/lon bounding box around a point for a given radius in metres."""
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def _route_to_dict(route) -> dict:
    """Flatten a RouteResponse into a compact dict.
    Drops per-segment geometry (too large for LLM reasoning);
    keeps route-level summary and cost breakdown."""
    return {
        "route_id": route.route_id,
        "profile": route.profile,
        "distance_km": round(route.cost_breakdown.distance_km, 2),
        "travel_time_minutes": round(route.cost_breakdown.travel_time_minutes, 1),
        "avg_aqi": round(route.cost_breakdown.avg_aqi, 1),
        "max_aqi": round(route.cost_breakdown.max_aqi, 1),
        "accident_hotspots_passed": route.cost_breakdown.accident_hotspots_passed,
        "total_cost": round(route.cost_breakdown.total_cost, 3),
        "weights_used": route.weights_used,
        "geometry": route.geometry,
    }


async def _ensure_ready() -> Optional[str]:
    """Returns an error string if the graph/DB aren't ready yet, else None."""
    if not graph_cache.is_loaded:
        return "Road graph is still loading — try again in a few seconds."
    return None


# ── Tools ────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_safe_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    profile: str = "balanced",
    departure_time: Optional[str] = None,
) -> dict:
    """
    Compute a health-and-safety-aware route across Bangalore.

    profile: one of "fastest", "safest", "healthiest", "balanced".
    departure_time: optional ISO-8601 datetime, used to apply time-of-day
    risk multipliers (e.g. school-zone hours).

    Returns distance, travel time, average/peak AQI exposure along the
    route, how many accident hotspots it passes, and the route geometry.
    """
    if (err := await _ensure_ready()):
        return {"error": err}

    try:
        route_profile = RouteProfile(profile.lower())
    except ValueError:
        return {"error": f"Unknown profile '{profile}'. Use fastest, safest, healthiest, or balanced."}

    alpha, beta, gamma = get_profile_weights(route_profile)
    hour = _parse_hour(departure_time)

    route = await find_route(
        origin_lat=origin_lat, origin_lon=origin_lon,
        dest_lat=dest_lat, dest_lon=dest_lon,
        alpha=alpha, beta=beta, gamma=gamma,
        profile=route_profile, hour=hour,
    )
    if not route:
        return {"error": "No route found — check that both points are near a mapped road in Bangalore."}

    return _route_to_dict(route)


@mcp.tool()
async def compare_route_profiles(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    departure_time: Optional[str] = None,
) -> dict:
    """
    Compute all four routing profiles (fastest, safest, healthiest, balanced)
    between two points and return them side by side, so the caller can see
    the actual trade-off (e.g. "healthiest is 6 min slower but cuts AQI
    exposure by 40%").
    """
    if (err := await _ensure_ready()):
        return {"error": err}

    import asyncio
    hour = _parse_hour(departure_time)

    results = await asyncio.gather(*[
        find_route(
            origin_lat=origin_lat, origin_lon=origin_lon,
            dest_lat=dest_lat, dest_lon=dest_lon,
            alpha=a, beta=b, gamma=g,
            profile=p, hour=hour,
        )
        for p in RouteProfile
        for a, b, g in [get_profile_weights(p)]
    ], return_exceptions=True)

    routes = [_route_to_dict(r) for r in results if r and not isinstance(r, Exception)]
    if not routes:
        return {"error": "No routes found between the given points."}
    return {"routes": routes}


@mcp.tool()
async def explain_route_cost(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    profile: str = "balanced",
) -> dict:
    """
    Compute a route and explain, in plain language, how its cost score
    breaks down — how much of the score comes from travel time vs AQI
    exposure vs accident risk, given the profile's weighting.
    """
    if (err := await _ensure_ready()):
        return {"error": err}

    try:
        route_profile = RouteProfile(profile.lower())
    except ValueError:
        return {"error": f"Unknown profile '{profile}'. Use fastest, safest, healthiest, or balanced."}

    alpha, beta, gamma = get_profile_weights(route_profile)
    route = await find_route(
        origin_lat=origin_lat, origin_lon=origin_lon,
        dest_lat=dest_lat, dest_lon=dest_lon,
        alpha=alpha, beta=beta, gamma=gamma,
        profile=route_profile,
    )
    if not route:
        return {"error": "No route found — check that both points are near a mapped road in Bangalore."}

    cb = route.cost_breakdown
    total = cb.travel_time_cost + cb.aqi_exposure_cost + cb.accident_risk_cost
    def pct(x):
        return round(100 * x / total, 1) if total else 0.0

    explanation = (
        f"This '{profile}' route weighs travel time, AQI exposure, and accident risk "
        f"as {alpha:.1f} / {beta:.1f} / {gamma:.1f}. Of the total cost score, "
        f"time contributes {pct(cb.travel_time_cost)}%, AQI exposure {pct(cb.aqi_exposure_cost)}%, "
        f"and accident risk {pct(cb.accident_risk_cost)}%. "
        f"It covers {cb.distance_km:.1f} km in about {cb.travel_time_minutes:.0f} minutes, "
        f"averaging AQI {cb.avg_aqi:.0f} (peaking at {cb.max_aqi:.0f}), "
        f"and passes {cb.accident_hotspots_passed} known accident hotspot(s)."
    )

    return {
        "route_id": route.route_id,
        "explanation": explanation,
        "weights_used": {"alpha": alpha, "beta": beta, "gamma": gamma},
        "cost_breakdown": cb.model_dump(),
    }


@mcp.tool()
async def get_aqi_heatmap_summary(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
) -> dict:
    """
    Get AQI conditions within a bounding box. Returns summary stats
    (average/min/max AQI, cell count) plus the 5 worst grid cells,
    rather than the full grid — the full GeoJSON is large and better
    suited to the /api/aqi/heatmap HTTP endpoint the map UI uses.
    """
    cells = await get_aqi_heatmap(min_lat, max_lat, min_lon, max_lon)
    if not cells:
        return {"cell_count": 0, "message": "No AQI data for this area."}

    values = [c["aqi_value"] for c in cells]
    worst = sorted(cells, key=lambda c: c["aqi_value"], reverse=True)[:5]

    return {
        "cell_count": len(cells),
        "avg_aqi": round(sum(values) / len(values), 1),
        "min_aqi": round(min(values), 1),
        "max_aqi": round(max(values), 1),
        "worst_cells": [
            {"lat": round(c["center_lat"], 4), "lon": round(c["center_lon"], 4), "aqi": round(c["aqi_value"], 1)}
            for c in worst
        ],
    }


@mcp.tool()
async def get_aqi_near(lat: float, lon: float, radius_m: float = 1000.0) -> dict:
    """
    Get the best available AQI estimate near a specific point: prefers the
    nearest interpolated grid cell, falls back to the nearest monitoring
    station's latest reading if no grid cell is found.
    """
    min_lat, max_lat, min_lon, max_lon = _bbox_from_point(lat, lon, radius_m)
    cells = await get_aqi_heatmap(min_lat, max_lat, min_lon, max_lon)

    if cells:
        nearest = min(cells, key=lambda c: haversine(lat, lon, c["center_lat"], c["center_lon"]))
        return {
            "source": "grid_cell",
            "aqi": round(nearest["aqi_value"], 1),
            "lat": round(nearest["center_lat"], 4),
            "lon": round(nearest["center_lon"], 4),
        }

    stations_resp = await list_stations()
    stations = stations_resp["stations"]
    if not stations:
        return {"error": "No AQI data available near this location."}

    nearest = min(stations, key=lambda s: haversine(lat, lon, s["lat"], s["lon"]))
    return {
        "source": "station",
        "station_name": nearest["station_name"],
        "aqi": nearest["latest_aqi"],
        "distance_m": round(haversine(lat, lon, nearest["lat"], nearest["lon"]), 0),
        "as_of": nearest["latest_at"],
    }


@mcp.tool()
async def get_accident_risk_near(lat: float, lon: float, radius_m: float = 500.0) -> dict:
    """
    Get known accident blackspots within a radius of a point — useful for
    "is this junction/stretch risky?" questions independent of routing.
    Returns real BTP/OpenCity station-jurisdiction data.
    """
    bbox = _bbox_from_point(lat, lon, radius_m)
    spots = await get_blackspots_in_bbox(*bbox)
    if not spots:
        return {"count": 0, "message": "No recorded accident blackspots in this area."}

    spots_sorted = sorted(spots, key=lambda s: s.get("severity_weight", 0), reverse=True)
    return {
        "count": len(spots_sorted),
        "total_accidents": sum(s.get("total_accidents", 0) for s in spots_sorted),
        "total_fatal": sum(s.get("fatal_accidents", 0) for s in spots_sorted),
        "blackspots": [
            {
                "lat": s["lat"], "lon": s["lon"],
                "severity": s.get("severity"),
                "severity_weight": s.get("severity_weight"),
                "total_accidents": s.get("total_accidents"),
                "fatal_accidents": s.get("fatal_accidents"),
                "description": s.get("description"),
            }
            for s in spots_sorted[:10]
        ],
    }


@mcp.tool()
async def predict_aqi_near(
    lat: float,
    lon: float,
    minutes_ahead: int = 30,
) -> dict:
    """
    Predict AQI near a geographic point N minutes from now (15–120 min).

    Finds the nearest AQI monitoring station to the given coordinates,
    then returns its forecasted AQI value using the trained LSTM model
    (or a cached scheduler prediction if available — latency < 5ms).

    This is the only tool that reasons about the *future* rather than
    querying current state, making it useful for planning trips:
    e.g. "what will the air quality be like at MG Road in 30 minutes?"

    Returns null predicted_aqi if no model has been trained for the
    nearest station yet.
    """
    if not 15 <= minutes_ahead <= 120:
        return {"error": "minutes_ahead must be between 15 and 120."}

    # Find the nearest station geographically
    stations_resp = await list_stations()
    stations = stations_resp.get("stations", [])
    if not stations:
        return {"error": "No AQI stations available."}

    nearest = min(stations, key=lambda s: haversine(lat, lon, s["lat"], s["lon"]))
    station_id = nearest["station_id"]
    distance_m = round(haversine(lat, lon, nearest["lat"], nearest["lon"]), 0)

    # Try prediction cache first
    from datetime import datetime, timezone, timedelta
    freshness_window = max(minutes_ahead + 5, 35)
    row = await db.fetchrow("""
        SELECT predicted_aqi, confidence, predicted_for, created_at
        FROM aqi_predictions
        WHERE station_id    = $1
          AND minutes_ahead = $2
          AND created_at   >= NOW() - ($3 || ' minutes')::INTERVAL
        ORDER BY created_at DESC
        LIMIT 1;
    """, station_id, minutes_ahead, str(freshness_window))

    if row:
        return {
            "station_id": station_id,
            "station_name": nearest["station_name"],
            "station_lat": nearest["lat"],
            "station_lon": nearest["lon"],
            "distance_to_station_m": distance_m,
            "predicted_aqi": round(float(row["predicted_aqi"]), 1),
            "minutes_ahead": minutes_ahead,
            "predicted_for": row["predicted_for"].isoformat(),
            "confidence": round(float(row["confidence"] or 0), 3),
            "source": "cache",
        }

    # Fallback: inline LSTM inference
    try:
        from lstm_trainer import predict
        pred_aqi = await predict(station_id, minutes_ahead=minutes_ahead, save=True)
    except Exception as exc:
        return {
            "station_id": station_id,
            "station_name": nearest["station_name"],
            "distance_to_station_m": distance_m,
            "predicted_aqi": None,
            "error": f"Inference failed: {exc}. Run: python data_pipeline/lstm_trainer.py --train --station-id {station_id}",
        }

    if pred_aqi is None:
        return {
            "station_id": station_id,
            "station_name": nearest["station_name"],
            "distance_to_station_m": distance_m,
            "predicted_aqi": None,
            "error": f"No trained model for station '{station_id}'. Current AQI: {nearest['latest_aqi']}.",
        }

    return {
        "station_id": station_id,
        "station_name": nearest["station_name"],
        "station_lat": nearest["lat"],
        "station_lon": nearest["lon"],
        "distance_to_station_m": distance_m,
        "predicted_aqi": round(pred_aqi, 1),
        "minutes_ahead": minutes_ahead,
        "predicted_for": (
            datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
        ).isoformat(),
        "confidence": None,
        "source": "inference",
    }


# ── Entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.getenv("MCP_PORT", "8001"))
    # streamable-http transport — reachable at http://<host>:<port>/mcp
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
