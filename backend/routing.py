"""
SafeMAPS — Weighted A* Routing Engine

Phase 1 changes vs original:
  - Reads nodes, adjacency, edge_data from graph_cache (in-memory)
    instead of calling get_road_graph() which loaded ~500k rows per request
  - AQI and risk lookups hit graph_cache.get_aqi() / get_risk()
    instead of individual DB queries per visited edge
  - snap_to_nearest_node() is the only DB call remaining per route request
  - else branch after while loop now correctly signals "no path found"

Cost function (unchanged):
    C_e = α·T_e + β·(AQI_e / 500) · T_e_min + γ·min(R_e / 10, 1)
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
        RouteProfile.FASTEST:    (1.0, 0.0, 0.0),
        RouteProfile.SAFEST:     (0.2, 0.1, 0.7),
        RouteProfile.HEALTHIEST: (0.1, 0.7, 0.2),
        RouteProfile.BALANCED:   (0.4, 0.3, 0.3),
    }
    return profiles.get(profile, (0.4, 0.3, 0.3))


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
) -> float:
    """
    Composite edge cost: C_e = α·T_e + β·AQI_exposure + γ·R_e

    AQI exposure = (AQI / 500) × travel_time_min
    Risk         = min(risk_score / 10, 1.0)
    """
    travel_time_min = travel_time_s / 60.0
    aqi_exposure = (aqi_value / 500.0) * travel_time_min
    risk_norm = min((risk_score * max(time_multiplier, 1.0)) / 10.0, 1.0)
    cost = alpha * travel_time_min + beta * aqi_exposure + gamma * risk_norm
    return max(cost, 0.001)


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

    if start_id not in nodes or goal_id not in nodes:
        return None

    goal_lat, goal_lon = nodes[goal_id]

    # ── A* Search ─────────────────────────────────────────────────────
    open_set = [(0.0, start_id)]
    came_from: dict[int, tuple[int, int]] = {}   # node → (prev_node, edge_id)
    g_score: dict[int, float] = {start_id: 0.0}
    path_found = False

    while open_set:
        _f, current = heapq.heappop(open_set)

        if current == goal_id:
            path_found = True
            break

        neighbours = adjacency.get(current)
        if not neighbours:
            continue

        for neighbour, edge_id, length_m, speed_kmh in neighbours:
            speed_ms = max(speed_kmh / 3.6, 0.5)
            travel_time_s = length_m / speed_ms

            # All lookups are now O(1) dict reads — no DB calls in the loop
            aqi_value  = graph_cache.get_aqi(edge_id)
            risk_score = graph_cache.get_risk(edge_id)
            road_type = edge_data.get(edge_id, {}).get("road_type")
            time_multiplier = get_time_multiplier(road_type, hour)

            edge_cost = compute_edge_cost(
                travel_time_s,
                aqi_value,
                risk_score,
                alpha,
                beta,
                gamma,
                time_multiplier,
            )
            tentative_g = g_score[current] + edge_cost

            if tentative_g < g_score.get(neighbour, float("inf")):
                came_from[neighbour] = (current, edge_id)
                g_score[neighbour] = tentative_g

                # Heuristic: optimistic travel time using straight-line distance
                if neighbour in nodes:
                    n_lat, n_lon = nodes[neighbour]
                    h_dist = haversine(n_lat, n_lon, goal_lat, goal_lon)
                    h_cost = alpha * (h_dist / 3.6 / 33.3 / 60.0)  # ~120 km/h max
                else:
                    h_cost = 0.0

                heapq.heappush(open_set, (tentative_g + h_cost, neighbour))

    if not path_found:
        return None

    # ── Reconstruct path ──────────────────────────────────────────────
    path_edges: list[int] = []
    current = goal_id
    while current in came_from:
        prev, edge_id = came_from[current]
        path_edges.append(edge_id)
        current = prev
    path_edges.reverse()

    if not path_edges:
        return None

    # ── Build response ────────────────────────────────────────────────
    segments: list[SegmentInfo] = []
    all_coords: list = []
    total_time = 0.0
    total_distance = 0.0
    total_aqi_weighted = 0.0
    max_aqi = 0.0
    hotspots = 0

    for eid in path_edges:
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
        )

        geom = ed.get("geometry", {"type": "LineString", "coordinates": []})
        if "coordinates" in geom:
            all_coords.extend(geom["coordinates"])

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
        total_cost=g_score.get(goal_id, 0.0),
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
            for eid in path_edges
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
