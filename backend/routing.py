"""
SafeMAPS — Weighted A* Routing Engine

Phase 1: in-memory graph cache eliminates per-request DB queries.
Phase 3: graph_cache uses CSR arrays instead of Python dicts;
         routing iterates CSR slices; road_type/road_name fetched on-demand.

Cost function:
    C_e = α·T_e + β·(AQI_e/500)·T_min + γ·(min(R_e/10,1) + I_e)
"""

import heapq
import math
import uuid
from typing import Optional

import numpy as np

from database import db
from graph_cache import graph_cache
from spatial_queries import snap_to_nearest_node
from models import (
    RouteResponse,
    CostBreakdown,
    SegmentInfo,
    RouteProfile,
)

# Routes longer than this straight-line distance use bidirectional A*
BIDIRECTIONAL_THRESHOLD_M = 5_000   # 5 km


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in metres between two WGS-84 coordinates."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_profile_weights(profile: RouteProfile) -> tuple[float, float, float]:
    """Return (α, β, γ) weights for a named routing profile."""
    profiles = {
        # Pure travel-time minimization — naturally favours arterials via speed_kmh.
        RouteProfile.FASTEST:    (1.0, 0.0, 0.0),
        # Risk-dominated; AQI still counts as proxy for traffic density.
        RouteProfile.SAFEST:     (0.1, 0.15, 0.75),
        # No time constraint — purely minimize pollution/road-class exposure.
        RouteProfile.HEALTHIEST: (0.0, 0.85, 0.15),
        RouteProfile.BALANCED:   (0.4, 0.3, 0.3),
    }
    return profiles.get(profile, (0.4, 0.3, 0.3))


# Road-class exposure/risk proxies.
# The AQI grid and accident-blackspot data are both spatially coarse —
# two parallel roads a block apart (a main road and the gully next to it)
# can land in the same grid cell / share the same nearest blackspot.
# Without this, "healthiest"/"safest" collapse onto the same path as
# "fastest" whenever local data doesn't happen to vary.
ROAD_TYPE_EXPOSURE: dict[str, float] = {
    "motorway": 1.8, "motorway_link": 1.6,
    "trunk": 1.6, "trunk_link": 1.5,
    "primary": 1.4, "primary_link": 1.3,
    "secondary": 1.2, "secondary_link": 1.15,
    "tertiary": 1.0, "tertiary_link": 1.0,
    "unclassified": 0.9, "residential": 0.75,
    "living_street": 0.55, "service": 0.65,
    "road": 1.0,
}

ROAD_TYPE_RISK_BASELINE: dict[str, float] = {
    "motorway": 3.0, "motorway_link": 2.5,
    "trunk": 2.5, "trunk_link": 2.2,
    "primary": 2.0, "primary_link": 1.7,
    "secondary": 1.2, "secondary_link": 1.0,
    "tertiary": 0.6, "tertiary_link": 0.5,
    "unclassified": 0.4, "residential": 0.2,
    "living_street": 0.1, "service": 0.15,
    "road": 0.5,
}


def get_time_multiplier(road_type: str | None, hour: int | None) -> float:
    """Return time-of-day risk multiplier for a road class."""
    if hour is None or not 0 <= hour <= 23:
        return 1.0

    road_type_norm = (road_type or "").lower()
    school_zone_types = {"school_zone", "school", "school_zone_link"}
    trunk_types = {"trunk", "trunk_link", "motorway", "motorway_link"}
    primary_secondary_types = {
        "primary",
        "primary_link",
        "secondary",
        "secondary_link",
    }

    if road_type_norm in school_zone_types and (8 <= hour < 10 or 15 <= hour < 17):
        return 2.0
    if road_type_norm in trunk_types and (hour >= 22 or hour < 6):
        return 1.8
    if road_type_norm in primary_secondary_types and (8 <= hour < 10 or 17 <= hour < 20):
        return 1.4
    return 1.0


def compute_edge_cost(
    travel_time_s: float,
    aqi_value: float,
    risk_score: float,
    alpha: float,
    beta: float,
    gamma: float,
    time_multiplier: float = 1.0,
    incident_cost: float = 0.0,
    road_type: str | None = None,
) -> float:
    """
    Composite edge cost:
        C_e = α·T_e + β·AQI_exposure + γ·(R_e + I_e)

    AQI exposure = (AQI × road-class exposure factor / 500) × travel_time_min
    Risk         = min((R_e × time_multiplier + road-class baseline) / 10, 1.0)
    I_e          = live incident cost (0–10.0 based on severity)

    Road-class factors differentiate a gully from a main road even inside
    the same coarse AQI grid cell / blackspot radius.
    """
    key = (road_type or "").lower()
    exposure_factor = ROAD_TYPE_EXPOSURE.get(key, 1.0)
    risk_baseline   = ROAD_TYPE_RISK_BASELINE.get(key, 1.0)

    travel_time_min = travel_time_s / 60.0
    aqi_exposure = (aqi_value * exposure_factor / 500.0) * travel_time_min
    risk_norm    = min(
        (risk_score * max(time_multiplier, 1.0) + risk_baseline) / 10.0, 1.0
    )
    incident_norm = min(incident_cost / 10.0, 1.0)
    cost = alpha * travel_time_min + beta * aqi_exposure + gamma * (risk_norm + incident_norm)
    return max(cost, 0.001)




def _astar_search(
    start_idx: int,
    goal_idx: int,
    alpha: float,
    beta: float,
    gamma: float,
    hour: Optional[int],
) -> Optional[list]:
    """
    Standard unidirectional A* on CSR graph.
    Returns (from_idx, to_idx, compact_eid, length_m, speed_kmh) steps.
    """
    gc = graph_cache
    if start_idx < 0 or goal_idx < 0:
        return None
    goal_lat = float(gc.node_lat[goal_idx])
    goal_lon = float(gc.node_lon[goal_idx])

    open_set = [(0.0, start_idx)]
    came_from: dict[int, tuple[int, int, float, float]] = {}  # node_idx -> (prev_idx, compact_eid, length_m, speed_kmh)
    g_score: dict[int, float] = {start_idx: 0.0}
    path_found = False

    while open_set:
        _f, current = heapq.heappop(open_set)
        if current == goal_idx:
            path_found = True
            break
        s, e = int(gc.fwd_indptr[current]), int(gc.fwd_indptr[current + 1])
        for i in range(s, e):
            neighbour   = int(gc.fwd_nbr[i])
            compact_eid = int(gc.fwd_eid[i])
            length_m    = float(gc.fwd_length[i])
            speed_kmh   = float(gc.fwd_speed[i])
            speed_ms    = max(speed_kmh / 3.6, 0.5)
            travel_time_s = length_m / speed_ms
            edge_cost = compute_edge_cost(
                travel_time_s,
                gc.get_aqi(compact_eid),
                gc.get_risk(compact_eid),
                alpha, beta, gamma,
                1.0,
                gc.get_incident(compact_eid),
                None,
            )
            tentative_g = g_score[current] + edge_cost
            if tentative_g < g_score.get(neighbour, float("inf")):
                came_from[neighbour] = (current, compact_eid, length_m, speed_kmh)
                g_score[neighbour]   = tentative_g
                nlat = float(gc.node_lat[neighbour])
                nlon = float(gc.node_lon[neighbour])
                h_cost = alpha * (haversine(nlat, nlon, goal_lat, goal_lon) / 3.6 / 120.0 / 60.0)
                heapq.heappush(open_set, (tentative_g + h_cost, neighbour))

    if not path_found:
        return None
    path_steps = []
    cur = goal_idx
    while cur in came_from:
        prev, eid, length_m, speed_kmh = came_from[cur]
        path_steps.append((prev, cur, eid, length_m, speed_kmh))
        cur = prev
    path_steps.reverse()
    return path_steps or None


def _orient_edge_coords(coords: list | tuple, from_idx: int, to_idx: int) -> list:
    """Orient stored edge geometry so it follows the actual traversal direction.
    
    Now takes compact node indices and reads lat/lon from CSR arrays.
    """
    if len(coords) < 2:
        return list(coords)

    gc = graph_cache
    from_lat = float(gc.node_lat[from_idx]) if from_idx >= 0 else None
    from_lon = float(gc.node_lon[from_idx]) if from_idx >= 0 else None
    to_lat   = float(gc.node_lat[to_idx])   if to_idx   >= 0 else None
    to_lon   = float(gc.node_lon[to_idx])   if to_idx   >= 0 else None

    def _match(coord, lat, lon) -> bool:
        if not coord or lat is None:
            return False
        return abs(float(coord[1]) - lat) < 1e-7 and abs(float(coord[0]) - lon) < 1e-7

    if _match(coords[0], from_lat, from_lon) and _match(coords[-1], to_lat, to_lon):
        return list(coords)
    if _match(coords[-1], from_lat, from_lon) and _match(coords[0], to_lat, to_lon):
        return list(reversed(coords))

    def dist2(coord, lat, lon):
        if not coord or lat is None:
            return float("inf")
        return (float(coord[1]) - lat) ** 2 + (float(coord[0]) - lon) ** 2

    return list(coords) if dist2(coords[0], from_lat, from_lon) <= dist2(coords[-1], from_lat, from_lon) else list(reversed(coords))

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compass bearing (0-360, 0=North, clockwise) from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _compass_direction(bearing_deg: float) -> str:
    dirs = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
    return dirs[round(bearing_deg / 45) % 8]


def _turn_angle(bearing_in: float, bearing_out: float) -> float:
    """Signed turn angle in (-180, 180]. Positive = right turn, negative = left."""
    return ((bearing_out - bearing_in + 540) % 360) - 180


def _maneuver_for_angle(angle: float) -> tuple[str, str]:
    """Returns (maneuver_code, human_verb) for a signed turn angle."""
    a = abs(angle)
    if a < 12:
        return "straight", "Continue straight"
    if a < 45:
        return ("slight_right", "Bear right") if angle > 0 else ("slight_left", "Bear left")
    if a < 135:
        return ("right", "Turn right") if angle > 0 else ("left", "Turn left")
    return "uturn", "Make a U-turn"


def _first_bearing(coords: list) -> Optional[float]:
    """Bearing of the first real movement in a [lon, lat] coordinate list."""
    for i in range(len(coords) - 1):
        (lon1, lat1), (lon2, lat2) = coords[i], coords[i + 1]
        if (lon1, lat1) != (lon2, lat2):
            return _bearing(lat1, lon1, lat2, lon2)
    return None


def _last_bearing(coords: list) -> Optional[float]:
    """Bearing of the last real movement in a [lon, lat] coordinate list."""
    for i in range(len(coords) - 1, 0, -1):
        (lon1, lat1), (lon2, lat2) = coords[i - 1], coords[i]
        if (lon1, lat1) != (lon2, lat2):
            return _bearing(lat1, lon1, lat2, lon2)
    return None


def generate_turn_instructions(segments: list) -> list:
    """
    Build Google-Maps-style turn-by-turn steps from a route's ordered
    SegmentInfo list.

    Consecutive segments sharing the same road_name are merged into one
    step (otherwise you'd get a new instruction at every single OSM
    intersection along a straight arterial road, which is not what a
    navigation UI wants). A new instruction is emitted at each road-name
    change, describing the turn based on the actual bearing change
    between the end of the previous step and the start of the next —
    not just "a turn happened", but left/right/straight/U-turn with a
    real angle behind the classification.
    """
    if not segments:
        return []

    # ── Merge into road-name runs ───────────────────────────────────
    steps = []
    for seg in segments:
        coords = seg.geometry.get("coordinates", [])
        if steps and steps[-1]["road_name"] == seg.road_name:
            steps[-1]["coords"].extend(coords[1:] if steps[-1]["coords"] else coords)
            steps[-1]["distance_m"] += seg.length_m
            steps[-1]["travel_time_s"] += seg.travel_time_s
        else:
            steps.append({
                "road_name": seg.road_name,
                "coords": list(coords),
                "distance_m": seg.length_m,
                "travel_time_s": seg.travel_time_s,
            })

    # ── Emit instructions at each step ──────────────────────────────
    instructions = []
    for i, step in enumerate(steps):
        name = step["road_name"] or "the road"
        entry_bearing = _first_bearing(step["coords"])
        start_lon, start_lat = (step["coords"][0] if step["coords"] else (None, None))

        if i == 0:
            heading = _compass_direction(entry_bearing) if entry_bearing is not None else None
            text = f"Head {heading} on {name}" if heading else f"Head on {name}"
            maneuver = "depart"
        else:
            prev_bearing = _last_bearing(steps[i - 1]["coords"])
            if prev_bearing is not None and entry_bearing is not None:
                angle = _turn_angle(prev_bearing, entry_bearing)
                maneuver, verb = _maneuver_for_angle(angle)
            else:
                maneuver, verb = "straight", "Continue"
            text = f"{verb} onto {name}" if maneuver != "straight" else f"Continue onto {name}"

        instructions.append({
            "maneuver": maneuver,
            "instruction": text,
            "road_name": step["road_name"],
            "distance_m": round(step["distance_m"], 1),
            "travel_time_s": round(step["travel_time_s"], 1),
            "location": {"lat": start_lat, "lon": start_lon} if start_lat is not None else None,
        })

    instructions.append({
        "maneuver": "arrive",
        "instruction": "Arrive at your destination",
        "road_name": None,
        "distance_m": 0.0,
        "travel_time_s": 0.0,
        "location": None,
    })
    return instructions


async def find_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    alpha: float = 0.4,
    beta: float = 0.3,
    gamma: float = 0.3,
    profile: RouteProfile = RouteProfile.BALANCED,
    hour: Optional[int] = None,
) -> Optional[RouteResponse]:
    """
    Run weighted A* from origin to destination.

    DB calls: 2 (snap) + 1 (geometry) + 1 (road metadata) per route.
    In-memory: all node/edge/AQI/risk data from CSR graph_cache arrays.
    """
    if not graph_cache.is_loaded:
        return None

    gc = graph_cache

    # ── Snap to nearest road nodes (2 DB calls) ───────────────────────
    origin_node = await snap_to_nearest_node(origin_lat, origin_lon)
    dest_node   = await snap_to_nearest_node(dest_lat, dest_lon)
    if not origin_node or not dest_node:
        return None

    start_db_id = origin_node["id"]
    goal_db_id  = dest_node["id"]
    start_idx   = gc.node_idx(start_db_id)
    goal_idx    = gc.node_idx(goal_db_id)

    if start_idx < 0 or goal_idx < 0 or start_idx == goal_idx:
        return None

    # ── Dispatch: bidirectional A* for long routes ─────────────────────
    s_lat = float(gc.node_lat[start_idx])
    s_lon = float(gc.node_lon[start_idx])
    g_lat = float(gc.node_lat[goal_idx])
    g_lon = float(gc.node_lon[goal_idx])
    straight_m = haversine(s_lat, s_lon, g_lat, g_lon)

    if straight_m >= BIDIRECTIONAL_THRESHOLD_M:
        from bidirectional_astar import bidirectional_astar
        path_steps = bidirectional_astar(start_idx, goal_idx, alpha, beta, gamma, hour)
        if not path_steps:
            path_steps = _astar_search(start_idx, goal_idx, alpha, beta, gamma, hour)
    else:
        path_steps = _astar_search(start_idx, goal_idx, alpha, beta, gamma, hour)

    if not path_steps:
        return None

    # ── Fetch geometry and metadata for this route's edges only ───────
    # Convert compact edge indices back to DB edge ids for the DB fetch
    route_compact_eids = [eid for _f, _t, eid in path_steps]
    route_db_eids = [int(gc.edge_db_ids[eid]) for eid in route_compact_eids]

    edge_geometries = await gc.fetch_edge_geometries(db, route_db_eids)
    edge_metadata   = await gc.fetch_edge_metadata(db, route_db_eids)

    # ── Build response ─────────────────────────────────────────────────
    segments: list[SegmentInfo] = []
    all_coords: list = []
    _first_edge = True
    total_time = 0.0
    total_distance = 0.0
    total_aqi_weighted = 0.0
    max_aqi = 0.0
    hotspots = 0

    for from_idx, to_idx, compact_eid, length_m, speed_kmh in path_steps:
        db_eid    = int(gc.edge_db_ids[compact_eid])
        meta      = edge_metadata.get(db_eid, {})
        road_name = meta.get("road_name")
        road_type = meta.get("road_type")

        speed_ms      = max(speed_kmh / 3.6, 0.5)
        travel_time_s = length_m / speed_ms

        aqi_val  = gc.get_aqi(compact_eid)
        risk_val = gc.get_risk(compact_eid)
        time_multiplier = get_time_multiplier(road_type, hour)

        seg_cost = compute_edge_cost(
            travel_time_s, aqi_val, risk_val,
            alpha, beta, gamma,
            time_multiplier, 0.0, road_type,
        )

        raw_geom = edge_geometries.get(db_eid, {"type": "LineString", "coordinates": []})
        geom = raw_geom
        if "coordinates" in geom:
            coords = _orient_edge_coords(geom["coordinates"], from_idx, to_idx)
            geom = {**geom, "coordinates": coords}
            if coords:
                if _first_edge:
                    all_coords.extend(coords)
                    _first_edge = False
                else:
                    all_coords.extend(coords[1:])

        congestion = gc.get_congestion(compact_eid)

        segments.append(SegmentInfo(
            edge_id=db_eid,
            road_name=road_name,
            length_m=length_m,
            travel_time_s=travel_time_s,
            aqi_value=aqi_val,
            risk_score=risk_val,
            segment_cost=seg_cost,
            geometry=geom,
            congestion=congestion,
        ))

        total_time         += travel_time_s
        total_distance     += length_m
        total_aqi_weighted += aqi_val * (travel_time_s / 60.0)
        max_aqi             = max(max_aqi, aqi_val)
        if risk_val > 0.5:
            hotspots += 1

    avg_aqi = total_aqi_weighted / max(total_time / 60.0, 0.001)

    cost_breakdown = CostBreakdown(
        total_cost=sum(seg.segment_cost for seg in segments),
        travel_time_cost=alpha * (total_time / 60.0),
        aqi_exposure_cost=beta * (total_aqi_weighted / 500.0),
        accident_risk_cost=gamma * sum(
            min(gc.get_risk(eid) * get_time_multiplier(
                edge_metadata.get(int(gc.edge_db_ids[eid]), {}).get("road_type"), hour
            ) / 10.0, 1.0)
            for _f, _t, eid, _l, _s in path_steps
        ),
        travel_time_minutes=total_time / 60.0,
        distance_km=total_distance / 1000.0,
        avg_aqi=round(avg_aqi, 1),
        max_aqi=round(max_aqi, 1),
        accident_hotspots_passed=hotspots,
    )

    return RouteResponse(
        route_id=str(uuid.uuid4()),
        profile=profile,
        cost_breakdown=cost_breakdown,
        geometry={"type": "LineString", "coordinates": all_coords},
        segments=segments,
        weights_used={"alpha": alpha, "beta": beta, "gamma": gamma},
        instructions=generate_turn_instructions(segments),
    )
