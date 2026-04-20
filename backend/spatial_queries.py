"""
SafeMAPS — PostGIS Spatial Query Helpers

Phase 1 change: get_road_graph() has been removed from this module.
Graph loading now lives in graph_cache.py and runs once at startup.
All other helpers remain and are used by API routes.
"""

import json
from database import db


async def snap_to_nearest_node(lat: float, lon: float) -> dict | None:
    """Find the nearest road network node to a lat/lon coordinate."""
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
    return dict(row) if row else None


async def snap_to_nearest_edge(lat: float, lon: float) -> dict | None:
    """Find the nearest road segment to a lat/lon coordinate."""
    query = """
        SELECT
            id,
            source_node,
            target_node,
            road_name,
            length_m,
            speed_kmh,
            oneway,
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


async def get_edges_in_bbox(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
) -> list[dict]:
    """Get all road segments within a bounding box."""
    query = """
        SELECT
            id,
            source_node,
            target_node,
            road_name,
            length_m,
            speed_kmh,
            oneway,
            ST_AsGeoJSON(geom) AS geometry
        FROM road_segments
        WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326);
    """
    rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon)
    return [
        {**dict(row), "geometry": json.loads(row["geometry"])}
        for row in rows
    ]


async def get_aqi_heatmap(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
) -> list[dict]:
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
    return [
        {**dict(row), "geometry": json.loads(row["geometry"])}
        for row in rows
    ]


async def get_blackspots_in_bbox(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
) -> list[dict]:
    """Get accident blackspot locations within a bounding box."""
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
