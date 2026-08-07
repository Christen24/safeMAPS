"""
SafeMAPS — PostGIS Spatial Query Helpers

Phase 1 change: get_road_graph() has been removed from this module.
Graph loading now lives in graph_cache.py and runs once at startup.
All other helpers remain and are used by API routes.
"""

import json
from database import db

# Grid cell size — must match the step_lat/step_lon constants in
# data_pipeline/database_seeder.sql's grid generation block. Cells are
# uniform rectangles, so callers reconstruct bounds from a centroid +
# these steps instead of us shipping full polygon geometry per cell.
GRID_STEP_LAT = 0.0009
GRID_STEP_LON = 0.00098


async def snap_to_nearest_node(
    lat: float,
    lon: float,
    max_distance_m: float = 500.0,
) -> dict | None:
    """
    Find the nearest road network node to a lat/lon coordinate.

    Fix R1: added max_distance_m guard (default 500m).
    Without this, clicking outside the road network (e.g. a park, airport,
    or river) silently returns a node several km away, producing routes
    that start/end at the wrong location with no user feedback.
    Returns None if no node is found within max_distance_m.

    Fix R2: road_nodes contains every node referenced by an accepted OSM
    way, including pure shape/curve points that only exist to describe a
    road's geometry — not just true intersections/endpoints. Only nodes
    that appear as a source_node or target_node in road_segments are
    actual graph vertices with edges attached; anything else has degree
    0 and produces "no route found" even though the snap itself
    "succeeded". Restrict candidates to real graph vertices so this can't
    happen.
    """
    query = """
        SELECT
            n.id,
            ST_Y(n.geom) AS lat,
            ST_X(n.geom) AS lon,
            ST_Distance(
                n.geom::geography,
                ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography
            ) AS distance_m
        FROM road_nodes n
        WHERE ST_DWithin(
            n.geom::geography,
            ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography,
            $3
        )
        AND EXISTS (
            SELECT 1 FROM road_segments rs
            WHERE rs.source_node = n.id OR rs.target_node = n.id
        )
        ORDER BY n.geom <-> ST_SetSRID(ST_MakePoint($2, $1), 4326)
        LIMIT 1;
    """
    row = await db.fetchrow(query, lat, lon, max_distance_m)
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


def _aggregation_factor(zoom: int | None) -> int:
    """
    Grid cells are ~100m — at city-wide zoom levels individual cells
    aren't visually distinguishable anyway, and there's no point paying
    for tens of thousands of rows, JSON-encoding them, and shipping them
    over the wire just to draw pixels that overlap on screen. The
    further out the view, the more cells get merged into one NxN block
    server-side before the response is built.
    """
    if zoom is None or zoom >= 15:
        return 1
    if zoom >= 13:
        return 2
    if zoom >= 11:
        return 4
    return 8


async def get_aqi_heatmap(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
    zoom: int | None = None,
) -> dict:
    """
    Get AQI grid cells within a bounding box.

    Returns centroids + a value, not per-cell polygon geometry — cells
    are uniform rectangles on a fixed grid, so shipping 5 coordinate
    pairs per cell (and having Postgres ST_AsGeoJSON-encode, then Python
    json.loads, then FastAPI re-encode every one of them) was pure
    overhead. Callers reconstruct each cell's bounds from
    center +/- half of the returned cell_step_lat/cell_step_lon.

    At low zoom, cells are aggregated server-side into coarser NxN
    blocks (see _aggregation_factor) so a city-wide view returns a
    fraction of the ~110k-cell grid instead of all of it.
    """
    factor = _aggregation_factor(zoom)

    if factor == 1:
        query = """
            SELECT
                id,
                ST_Y(ST_Centroid(geom)) AS center_lat,
                ST_X(ST_Centroid(geom)) AS center_lon,
                aqi_value,
                1 AS cell_count
            FROM grid_cells
            WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326)
              AND aqi_value IS NOT NULL;
        """
        rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon)
    else:
        # row_idx/col_idx are plain ints, so integer division buckets
        # each cell into its NxN block; GROUP BY then collapses each
        # block into one averaged row. The bbox filter (index-assisted
        # via the GIST index on geom) still runs first, so this only
        # aggregates whatever's actually in view, not the whole table.
        query = """
            SELECT
                NULL::bigint AS id,
                AVG(ST_Y(ST_Centroid(geom))) AS center_lat,
                AVG(ST_X(ST_Centroid(geom))) AS center_lon,
                AVG(aqi_value) AS aqi_value,
                COUNT(*)::int AS cell_count
            FROM grid_cells
            WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326)
              AND aqi_value IS NOT NULL
            GROUP BY (row_idx / $5), (col_idx / $5);
        """
        rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon, factor)

    return {
        "cells": [dict(row) for row in rows],
        "cell_step_lat": GRID_STEP_LAT * factor,
        "cell_step_lon": GRID_STEP_LON * factor,
        "aggregation_factor": factor,
    }


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
            severity_weight,
            total_accidents,
            fatal_accidents,
            description
        FROM accident_blackspots
        WHERE geom && ST_MakeEnvelope($3, $1, $4, $2, 4326);
    """
    rows = await db.fetch(query, min_lat, max_lat, min_lon, max_lon)
    return [dict(row) for row in rows]