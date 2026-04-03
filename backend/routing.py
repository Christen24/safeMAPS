"""
Custom Weighted A* Routing Engine for SafeMAPS.

Implements the composite cost function:
    C_e = α·T_e + β·∫AQI(t)dt + γ·R_e

Where:
    T_e  = Travel time on edge e
    AQI  = Air quality index (approximated as AQI · T_e for the integral)
    R_e  = Historical accident risk score
    α,β,γ = User-defined weights
"""

import heapq
import math
import uuid
import json
from typing import Optional

from spatial_queries import (
    snap_to_nearest_node,
    get_road_graph,
    get_grid_aqi_for_edge,
    get_segment_risk,
)
from models import (
    RouteResponse,
    CostBreakdown,
    SegmentInfo,
    RouteProfile,
)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters between two coordinates."""
    R = 6_371_000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_profile_weights(profile: RouteProfile) -> tuple[float, float, float]:
    """Return α, β, γ weights for predefined profiles."""
    profiles = {
        RouteProfile.FASTEST:    (1.0, 0.0, 0.0),
        RouteProfile.SAFEST:     (0.2, 0.1, 0.7),
        RouteProfile.HEALTHIEST: (0.1, 0.7, 0.2),
        RouteProfile.BALANCED:   (0.4, 0.3, 0.3),
    }
    return profiles.get(profile, (0.4, 0.3, 0.3))


def compute_edge_cost(
    travel_time_s: float,
    aqi_value: float,
    risk_score: float,
    alpha: float,
    beta: float,
    gamma: float,
) -> float:
    """
    Compute the composite cost for a road segment.

    C_e = α·T_e + β·(AQI × T_e) + γ·R_e

    The AQI integral ∫AQI(t)dt is approximated as AQI_avg × T_e,
    since AQI is assumed constant across a short segment.
    """
    # Normalize travel time to minutes for better scaling
    travel_time_min = travel_time_s / 60.0

    # AQI exposure = AQI value × time spent breathing it (in minutes)
    aqi_exposure = (aqi_value / 500.0) * travel_time_min  # Normalize AQI to [0,1]

    # Risk score is already a probability-like value
    risk_normalized = min(risk_score / 10.0, 1.0)  # Normalize to [0,1]

    cost = (
        alpha * travel_time_min
        + beta * aqi_exposure
        + gamma * risk_normalized
    )
    return max(cost, 0.001)  # Ensure positive cost


async def find_route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    alpha: float = 0.4,
    beta: float = 0.3,
    gamma: float = 0.3,
    profile: RouteProfile = RouteProfile.BALANCED,
) -> Optional[RouteResponse]:
    """
    Run weighted A* pathfinding from origin to destination.

    Steps:
    1. Snap origin/destination to nearest road nodes
    2. Load the road graph from PostGIS
    3. Run A* with composite cost function
    4. Collect segment metadata and build GeoJSON response
    """
    # ── Step 1: Snap to road network ─────────────────────────────────
    origin_node = await snap_to_nearest_node(origin_lat, origin_lon)
    dest_node = await snap_to_nearest_node(dest_lat, dest_lon)

    if not origin_node or not dest_node:
        return None

    start_id = origin_node["id"]
    goal_id = dest_node["id"]

    if start_id == goal_id:
        return None

    # ── Step 2: Load graph ───────────────────────────────────────────
    nodes, adjacency, edge_data = await get_road_graph()

    if start_id not in nodes or goal_id not in nodes:
        return None

    goal_lat, goal_lon = nodes[goal_id]

    # ── Step 3: A* Search ────────────────────────────────────────────
    # Priority queue: (f_score, node_id)
    open_set = [(0.0, start_id)]
    came_from = {}  # node_id -> (prev_node_id, edge_id)
    g_score = {start_id: 0.0}

    # Cache for AQI and risk lookups
    edge_aqi_cache = {}
    edge_risk_cache = {}

    while open_set:
        current_f, current = heapq.heappop(open_set)

        if current == goal_id:
            break

        if current not in adjacency:
            continue

        for neighbor, edge_id, length_m, speed_kmh in adjacency[current]:
            # Calculate travel time
            speed_ms = max(speed_kmh / 3.6, 0.5)  # Convert to m/s, min 0.5
            travel_time_s = length_m / speed_ms

            # Get AQI for this edge (cached)
            if edge_id not in edge_aqi_cache:
                try:
                    edge_aqi_cache[edge_id] = await get_grid_aqi_for_edge(edge_id)
                except Exception:
                    edge_aqi_cache[edge_id] = 50.0  # Default moderate AQI
            aqi_value = edge_aqi_cache[edge_id]

            # Get risk for this edge (cached)
            if edge_id not in edge_risk_cache:
                try:
                    edge_risk_cache[edge_id] = await get_segment_risk(edge_id)
                except Exception:
                    edge_risk_cache[edge_id] = 0.0
            risk_score = edge_risk_cache[edge_id]

            # Compute composite edge cost
            edge_cost = compute_edge_cost(
                travel_time_s, aqi_value, risk_score,
                alpha, beta, gamma
            )

            tentative_g = g_score[current] + edge_cost

            if tentative_g < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = (current, edge_id)
                g_score[neighbor] = tentative_g

                # Heuristic: Haversine distance / max speed → optimistic time
                if neighbor in nodes:
                    n_lat, n_lon = nodes[neighbor]
                    h = haversine(n_lat, n_lon, goal_lat, goal_lon) / 33.3  # ~120 km/h max
                    h_cost = alpha * (h / 60.0)  # Convert to minutes
                else:
                    h_cost = 0.0

                f_score = tentative_g + h_cost
                heapq.heappush(open_set, (f_score, neighbor))
    else:
        # No path found
        return None

    # ── Step 4: Reconstruct path ─────────────────────────────────────
    path_edges = []
    current = goal_id
    while current in came_from:
        prev, edge_id = came_from[current]
        path_edges.append(edge_id)
        current = prev
    path_edges.reverse()

    if not path_edges:
        return None

    # ── Step 5: Build response ───────────────────────────────────────
    segments = []
    all_coords = []
    total_time = 0.0
    total_distance = 0.0
    total_aqi_weighted = 0.0
    max_aqi = 0.0
    hotspots = 0

    for eid in path_edges:
        ed = edge_data.get(eid, {})
        length_m = ed.get("length_m", 0)
        speed_kmh = ed.get("speed_kmh", 30)
        speed_ms = max(speed_kmh / 3.6, 0.5)
        travel_time_s = length_m / speed_ms

        aqi_val = edge_aqi_cache.get(eid, 50.0)
        risk_val = edge_risk_cache.get(eid, 0.0)

        seg_cost = compute_edge_cost(
            travel_time_s, aqi_val, risk_val,
            alpha, beta, gamma
        )

        geom = ed.get("geometry", {"type": "LineString", "coordinates": []})
        if "coordinates" in geom:
            all_coords.extend(geom["coordinates"])

        segments.append(SegmentInfo(
            edge_id=eid,
            road_name=None,
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

    avg_aqi = (total_aqi_weighted / max(total_time / 60.0, 0.001))

    route_geojson = {
        "type": "LineString",
        "coordinates": all_coords,
    }

    cost_breakdown = CostBreakdown(
        total_cost=g_score.get(goal_id, 0.0),
        travel_time_cost=alpha * (total_time / 60.0),
        aqi_exposure_cost=beta * (total_aqi_weighted / 500.0),
        accident_risk_cost=gamma * sum(
            edge_risk_cache.get(eid, 0) for eid in path_edges
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
        geometry=route_geojson,
        segments=segments,
        weights_used={"alpha": alpha, "beta": beta, "gamma": gamma},
    )
