"""
Spatial query helpers for PostGIS operations.
All functions accept the database pool and return structured data.
"""

import json
from database import db


async def snap_to_nearest_node(lat: float, lon: float) -> dict:
    """Find the nearest road network node to a given coordinate."""
    query = """
        SELECT
            id,
            ST_Y(geom) AS lat,
            ST_X(geom) AS lon,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography
            ) AS distance_m
        FROM road_nodes
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint($2, $1), 4326)
        LIMIT 1;
    """
    row = await db.fetchrow(query, lat, lon)
    if row:
        return dict(row)
    return None


async def snap_to_nearest_edge(lat: float, lon: float) -> dict:
    """Find the nearest road segment (edge) to a given coordinate."""
    query = """
        SELECT
            id,
            source_node,
            target_node,
            road_name,
            length_m,
            speed_kmh,
            ST_AsGeoJSON(geom) AS geometry,
            ST_Distance(
                geom::geography,
                ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography
            ) AS distance_m
        FROM road_segments
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint($2, $1), 4326)
        LIMIT 1;
    """
    row = await db.fetchrow(query, lat, lon)
    if row:
        result = dict(row)
        result["geometry"] = json.loads(result["geometry"])
        return result
    return None


async def get_edges_in_bbox(min_lat: float, max_lat: float,
                            min_lon: float, max_lon: float) -> list[dict]:
    """Get all road segments within a bounding box."""
    query = """
        SELECT
            id,
            source_node,
            target_node,
            road_name,
            length_m,
            speed_kmh,
            ST_AsGeoJSON(geom) AS geometry
        FROM road_segments
        WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326);
    """
    rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon)
    results = []
    for row in rows:
        r = dict(row)
        r["geometry"] = json.loads(r["geometry"])
        results.append(r)
    return results


async def get_grid_aqi_for_edge(edge_id: int) -> float:
    """
    Get the interpolated AQI value for a road segment
    by spatial-joining with the AQI grid.
    """
    query = """
        SELECT COALESCE(AVG(g.aqi_value), 50.0) AS avg_aqi
        FROM road_segments e
        JOIN grid_cells g ON ST_Intersects(e.geom, g.geom)
        WHERE e.id = $1;
    """
    return await db.fetchval(query, edge_id)


async def get_segment_risk(edge_id: int) -> float:
    """
    Get the accident risk score for a road segment
    by counting nearby blackspots.
    """
    query = """
        SELECT COALESCE(
            SUM(
                b.severity_weight / GREATEST(
                    ST_Distance(e.geom::geography, b.geom::geography), 1.0
                )
            ),
            0.0
        ) AS risk_score
        FROM road_segments e
        CROSS JOIN LATERAL (
            SELECT geom, severity_weight
            FROM accident_blackspots
            WHERE ST_DWithin(e.geom::geography, geom::geography, 200)
        ) b
        WHERE e.id = $1;
    """
    return await db.fetchval(query, edge_id) or 0.0


async def get_aqi_heatmap(min_lat: float, max_lat: float,
                          min_lon: float, max_lon: float) -> list[dict]:
    """Get AQI grid cells within a bounding box for heatmap rendering."""
    query = """
        SELECT
            id,
            ST_Y(ST_Centroid(geom)) AS center_lat,
            ST_X(ST_Centroid(geom)) AS center_lon,
            aqi_value,
            ST_AsGeoJSON(geom) AS geometry
        FROM grid_cells
        WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326)
          AND aqi_value IS NOT NULL;
    """
    rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon)
    results = []
    for row in rows:
        r = dict(row)
        r["geometry"] = json.loads(r["geometry"])
        results.append(r)
    return results


async def get_blackspots_in_bbox(min_lat: float, max_lat: float,
                                 min_lon: float, max_lon: float) -> list[dict]:
    """Get accident blackspots within a bounding box."""
    query = """
        SELECT
            id,
            ST_Y(geom) AS lat,
            ST_X(geom) AS lon,
            severity,
            total_accidents,
            fatal_accidents,
            description
        FROM accident_blackspots
        WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326);
    """
    rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon)
    return [dict(row) for row in rows]


async def get_road_graph() -> tuple[dict, dict]:
    """
    Load the full road graph for A* pathfinding.
    Returns:
        nodes: {node_id: (lat, lon)}
        adjacency: {node_id: [(neighbor_id, edge_id, length_m, speed_kmh)]}
    """
    # Load nodes
    node_rows = await db.fetch(
        "SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM road_nodes;"
    )
    nodes = {row["id"]: (row["lat"], row["lon"]) for row in node_rows}

    # Load edges as adjacency list
    edge_rows = await db.fetch("""
        SELECT id, source_node, target_node, length_m, speed_kmh,
               ST_AsGeoJSON(geom) AS geometry
        FROM road_segments;
    """)
    adjacency = {}
    edge_data = {}
    for row in edge_rows:
        src, tgt = row["source_node"], row["target_node"]
        edge_info = (tgt, row["id"], row["length_m"], row["speed_kmh"])
        adjacency.setdefault(src, []).append(edge_info)
        # Also add reverse direction for bidirectional roads
        reverse_info = (src, row["id"], row["length_m"], row["speed_kmh"])
        adjacency.setdefault(tgt, []).append(reverse_info)
        edge_data[row["id"]] = {
            "geometry": json.loads(row["geometry"]),
            "length_m": row["length_m"],
            "speed_kmh": row["speed_kmh"],
        }

    return nodes, adjacency, edge_data
