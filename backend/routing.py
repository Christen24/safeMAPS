"""
SafeMAPS — Weighted A* Routing Engine

Phase 1: in-memory graph cache eliminates per-request DB queries.
Phase 6: Live incident cost I_e added to cost formula.
Phase 11: Bidirectional A* dispatched for routes >5km (halves search space).

Cost function:
    C_e = α·T_e + β·(AQI_e/500)·T_min + γ·(min(R_e/10,1) + I_e)
"""

import heapq
import math
import uuid
from typing import Optional

from graph_cache import graph_cache
from spatial_queries import snap_to_nearest_node
from models import (
    RouteResponse,
    CostBreakdown,
    SegmentInfo,
    RouteProfile,
)

# Routes longer than this straight-line distance use bidirectional A*
# Shorter routes use standard A* (lower overhead for short searches)
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
    start_id, goal_id, nodes, adjacency, edge_data, alpha, beta, gamma, hour
):
    """Standard unidirectional A*. Returns (from_node, to_node, edge_id) steps."""
    if start_id not in nodes or goal_id not in nodes:
        return None
    goal_lat, goal_lon = nodes[goal_id]
    open_set = [(0.0, start_id)]
    came_from = {}
    g_score   = {start_id: 0.0}
    path_found = False

    while open_set:
        _f, current = __import__("heapq").heappop(open_set)
        if current == goal_id:
            path_found = True
            break
        for neighbour, edge_id, length_m, speed_kmh in adjacency.get(current, []):
            speed_ms      = max(speed_kmh / 3.6, 0.5)
            travel_time_s = length_m / speed_ms
            road_type     = edge_data.get(edge_id, {}).get("road_type")
            edge_cost = compute_edge_cost(
                travel_time_s,
                graph_cache.get_aqi(edge_id),
                graph_cache.get_risk(edge_id),
                alpha, beta, gamma,
                get_time_multiplier(road_type, hour),
                graph_cache.get_incident(edge_id),
                road_type,
            )
            tentative_g = g_score[current] + edge_cost
            if tentative_g < g_score.get(neighbour, float("inf")):
                came_from[neighbour] = (current, edge_id)
                g_score[neighbour]   = tentative_g
                if neighbour in nodes:
                    nlat, nlon = nodes[neighbour]
                    h_cost = alpha * (haversine(nlat, nlon, goal_lat, goal_lon) / 3.6 / 120.0 / 60.0)
                else:
                    h_cost = 0.0
                __import__("heapq").heappush(open_set, (tentative_g + h_cost, neighbour))

    if not path_found:
        return None
    path_steps = []
    cur = goal_id
    while cur in came_from:
        prev, eid = came_from[cur]
        path_steps.append((prev, cur, eid))
        cur = prev
    path_steps.reverse()
    return path_steps or None


def _coords_match_node(coord: list | tuple, node: tuple[float, float]) -> bool:
    """Return True when a GeoJSON lon/lat coordinate belongs to a cached node."""
    if not coord or not node:
        return False
    lat, lon = node
    return abs(float(coord[1]) - float(lat)) < 1e-7 and abs(float(coord[0]) - float(lon)) < 1e-7


def _orient_edge_coords(coords: list, from_node: int, to_node: int, nodes: dict) -> list:
    """Orient stored edge geometry so it follows the actual traversal direction."""
    if len(coords) < 2:
        return coords

    from_ll = nodes.get(from_node)
    to_ll = nodes.get(to_node)
    if _coords_match_node(coords[0], from_ll) and _coords_match_node(coords[-1], to_ll):
        return coords
    if _coords_match_node(coords[-1], from_ll) and _coords_match_node(coords[0], to_ll):
        return list(reversed(coords))

    # Fallback for tiny precision mismatches: pick the orientation whose first
    # point is closer to the traversal's from-node.
    def dist2(coord, node):
        if not coord or not node:
            return float("inf")
        lat, lon = node
        return (float(coord[1]) - float(lat)) ** 2 + (float(coord[0]) - float(lon)) ** 2

    return coords if dist2(coords[0], from_ll) <= dist2(coords[-1], from_ll) else list(reversed(coords))

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

    DB calls: 2 (snap origin, snap destination)
    In-memory lookups: all node/edge/AQI/risk data from graph_cache
    """
    if not graph_cache.is_loaded:
        return None

    # ── Snap to nearest road nodes (2 DB calls total) ─────────────────
    origin_node = await snap_to_nearest_node(origin_lat, origin_lon)
    dest_node   = await snap_to_nearest_node(dest_lat, dest_lon)

    if not origin_node or not dest_node:
        return None

    start_id = origin_node["id"]
    goal_id  = dest_node["id"]

    if start_id == goal_id:
        return None

    nodes     = graph_cache.nodes
    adjacency = graph_cache.adjacency
    edge_data = graph_cache.edge_data

    # ── Dispatch: bidirectional A* for long routes ─────────────────────
    if start_id not in nodes or goal_id not in nodes:
        return None

    s_lat, s_lon = nodes[start_id]
    g_lat, g_lon = nodes[goal_id]
    straight_m = haversine(s_lat, s_lon, g_lat, g_lon)

    if straight_m >= BIDIRECTIONAL_THRESHOLD_M:
        from bidirectional_astar import bidirectional_astar
        path_steps = bidirectional_astar(start_id, goal_id, alpha, beta, gamma, hour)
        if not path_steps:
            # Fallback to standard A* if bidirectional fails
            path_steps = _astar_search(
                start_id, goal_id, nodes, adjacency, edge_data, alpha, beta, gamma, hour
            )
    else:
        path_steps = _astar_search(
            start_id, goal_id, nodes, adjacency, edge_data, alpha, beta, gamma, hour
        )

    if not path_steps:
        return None

    # ── Build response ────────────────────────────────────────────────
    segments: list[SegmentInfo] = []
    all_coords: list = []
    _first_edge = True  # Fix R3: track first edge to avoid duplicate join coords
    total_time = 0.0
    total_distance = 0.0
    total_aqi_weighted = 0.0
    max_aqi = 0.0
    hotspots = 0

    for from_node, to_node, eid in path_steps:
        ed = edge_data.get(eid, {})
        length_m  = ed.get("length_m", 0)
        speed_kmh = ed.get("speed_kmh", 30)
        speed_ms  = max(speed_kmh / 3.6, 0.5)
        travel_time_s = length_m / speed_ms

        aqi_val  = graph_cache.get_aqi(eid)
        risk_val = graph_cache.get_risk(eid)
        time_multiplier = get_time_multiplier(ed.get("road_type"), hour)

        seg_cost = compute_edge_cost(
            travel_time_s,
            aqi_val,
            risk_val,
            alpha,
            beta,
            gamma,
            time_multiplier,
            0.0,
            ed.get("road_type"),
        )

        raw_geom = ed.get("geometry", {"type": "LineString", "coordinates": []})
        geom = raw_geom
        if "coordinates" in geom:
            coords = _orient_edge_coords(geom["coordinates"], from_node, to_node, nodes)
            geom = {**geom, "coordinates": coords}
            if coords:
                if _first_edge:
                    # Fix R3: include all coords for the first edge
                    all_coords.extend(coords)
                    _first_edge = False
                else:
                    # Fix R3: skip first coord of subsequent edges — it is the
                    # same geographic point as the last coord of the previous edge
                    # (shared road node). Without this, every junction appears
                    # twice in the geometry, causing polyline kinks on the map.
                    all_coords.extend(coords[1:])

        segments.append(SegmentInfo(
            edge_id=eid,
            road_name=ed.get("road_name"),
            length_m=length_m,
            travel_time_s=travel_time_s,
            aqi_value=aqi_val,
            risk_score=risk_val,
            segment_cost=seg_cost,
            geometry=geom,
        ))

        total_time += travel_time_s
        total_distance += length_m
        total_aqi_weighted += aqi_val * (travel_time_s / 60.0)
        max_aqi = max(max_aqi, aqi_val)
        if risk_val > 0.5:
            hotspots += 1

    avg_aqi = total_aqi_weighted / max(total_time / 60.0, 0.001)

    cost_breakdown = CostBreakdown(
        total_cost=sum(seg.segment_cost for seg in segments),
        travel_time_cost=alpha * (total_time / 60.0),
        aqi_exposure_cost=beta * (total_aqi_weighted / 500.0),
        accident_risk_cost=gamma * sum(
            min(
                (
                    graph_cache.get_risk(eid)
                    * get_time_multiplier(edge_data.get(eid, {}).get("road_type"), hour)
                ) / 10.0,
                1.0,
            )
            for _from_node, _to_node, eid in path_steps
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
    )
