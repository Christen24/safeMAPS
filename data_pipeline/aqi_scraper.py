"""
AQI Scraper — Fetches air quality data from WAQI (World Air Quality Index) API.

Usage:
    python aqi_scraper.py [--once]

By default runs continuously every 15 minutes.
With --once, runs a single scrape and exits.

Requires: WAQI_API_TOKEN environment variable.
"""

import os
import sys
import asyncio
import logging
from pathlib import Path

import httpx
import asyncpg
import numpy as np
from scipy.interpolate import griddata

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

# WAQI API endpoint for stations in a bounding box
WAQI_MAP_URL = "https://api.waqi.info/v2/map/bounds"
WAQI_STATION_URL = "https://api.waqi.info/feed/@{station_id}/"

SCRAPE_INTERVAL = 15 * 60  # 15 minutes


async def fetch_stations_in_bbox(token: str) -> list[dict]:
    """Fetch all AQI monitoring stations within Bangalore's bounding box."""
    params = {
        "latlng": f"{settings.bbox_min_lat},{settings.bbox_min_lon},"
                  f"{settings.bbox_max_lat},{settings.bbox_max_lon}",
        "networks": "all",
        "token": token,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(WAQI_MAP_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "ok":
        logger.error(f"WAQI API error: {data}")
        return []

    stations = []
    for item in data.get("data", []):
        stations.append({
            "uid": str(item.get("uid", "")),
            "name": item.get("station", {}).get("name", "Unknown"),
            "lat": float(item.get("lat", 0)),
            "lon": float(item.get("lon", 0)),
            "aqi": float(item.get("aqi", 0)) if str(item.get("aqi", "-")).replace(".", "").isdigit() else None,
        })

    logger.info(f"Found {len(stations)} stations in bounding box.")
    return stations


async def fetch_station_detail(token: str, station_uid: str) -> dict:
    """Fetch detailed AQI data for a specific station."""
    url = WAQI_STATION_URL.format(station_id=station_uid)
    params = {"token": token}

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    if data.get("status") != "ok":
        return {}

    d = data.get("data", {})
    iaqi = d.get("iaqi", {})

    return {
        "aqi": float(d.get("aqi", 0)) if str(d.get("aqi", "-")).replace(".", "").isdigit() else None,
        "pm25": iaqi.get("pm25", {}).get("v"),
        "pm10": iaqi.get("pm10", {}).get("v"),
        "no2": iaqi.get("no2", {}).get("v"),
        "co": iaqi.get("co", {}).get("v"),
        "o3": iaqi.get("o3", {}).get("v"),
    }


async def interpolate_aqi_to_grid(conn: asyncpg.Connection):
    """
    Interpolate AQI from station readings to the 100m grid cells
    using Inverse Distance Weighting (IDW).
    """
    # Get latest readings per station
    readings = await conn.fetch("""
        SELECT DISTINCT ON (s.id)
            ST_Y(s.geom) AS lat, ST_X(s.geom) AS lon, r.aqi
        FROM aqi_stations s
        JOIN aqi_readings r ON r.station_id = s.id
        WHERE r.aqi IS NOT NULL
        ORDER BY s.id, r.recorded_at DESC;
    """)

    if len(readings) < 2:
        logger.warning("Not enough station data for interpolation.")
        return

    # Station locations and values
    points = np.array([(r["lat"], r["lon"]) for r in readings])
    values = np.array([r["aqi"] for r in readings])

    # Get grid cell centroids
    cells = await conn.fetch("""
        SELECT id, ST_Y(ST_Centroid(geom)) AS lat, ST_X(ST_Centroid(geom)) AS lon
        FROM grid_cells;
    """)

    grid_points = np.array([(c["lat"], c["lon"]) for c in cells])
    cell_ids = [c["id"] for c in cells]

    # Interpolate using scipy griddata (linear + nearest for edges)
    try:
        interpolated = griddata(points, values, grid_points, method="linear")
        # Fill NaN (points outside convex hull) with nearest
        mask = np.isnan(interpolated)
        if mask.any():
            nearest = griddata(points, values, grid_points[mask], method="nearest")
            interpolated[mask] = nearest
    except Exception as e:
        logger.error(f"Interpolation failed: {e}. Using nearest-neighbor fallback.")
        interpolated = griddata(points, values, grid_points, method="nearest")

    # Bulk update grid cells
    logger.info(f"Updating {len(cell_ids)} grid cells with interpolated AQI...")
    for i, cell_id in enumerate(cell_ids):
        aqi_val = float(interpolated[i]) if not np.isnan(interpolated[i]) else 50.0
        await conn.execute("""
            UPDATE grid_cells SET aqi_value = $1, aqi_updated = NOW()
            WHERE id = $2;
        """, aqi_val, cell_id)

    logger.info("Grid AQI interpolation complete.")


async def scrape_once():
    """Run a single AQI scrape cycle."""
    token = settings.waqi_api_token
    if not token:
        logger.error("WAQI_API_TOKEN not set. Using mock data.")
        await seed_mock_aqi()
        return

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        # Fetch stations
        stations = await fetch_stations_in_bbox(token)

        for s in stations:
            if s["aqi"] is None:
                continue

            # Upsert station
            station_id = await conn.fetchval("""
                INSERT INTO aqi_stations (station_uid, name, geom)
                VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326))
                ON CONFLICT (station_uid) DO UPDATE SET name = EXCLUDED.name
                RETURNING id;
            """, s["uid"], s["name"], s["lon"], s["lat"])

            # Fetch detailed readings
            detail = await fetch_station_detail(token, s["uid"])
            aqi = detail.get("aqi") or s["aqi"]

            # Insert reading
            await conn.execute("""
                INSERT INTO aqi_readings (station_id, aqi, pm25, pm10, no2, co, o3)
                VALUES ($1, $2, $3, $4, $5, $6, $7);
            """,
                station_id, aqi,
                detail.get("pm25"), detail.get("pm10"),
                detail.get("no2"), detail.get("co"), detail.get("o3"),
            )

        logger.info(f"Stored readings from {len(stations)} stations.")

        # Interpolate to grid
        await interpolate_aqi_to_grid(conn)

    finally:
        await conn.close()


async def seed_mock_aqi():
    """Seed the database with mock AQI data for development."""
    logger.info("Seeding mock AQI data...")

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        # Create mock stations across Bangalore
        mock_stations = [
            ("mock_1", "BTM Layout", 12.9166, 77.6101, 85),
            ("mock_2", "Koramangala", 12.9352, 77.6245, 120),
            ("mock_3", "Whitefield", 12.9698, 77.7500, 95),
            ("mock_4", "Jayanagar", 12.9308, 77.5838, 75),
            ("mock_5", "Yelahanka", 13.1005, 77.5940, 65),
            ("mock_6", "Peenya", 13.0285, 77.5180, 150),
            ("mock_7", "Silk Board", 12.9170, 77.6230, 180),
            ("mock_8", "Hebbal", 13.0358, 77.5970, 110),
            ("mock_9", "Electronic City", 12.8458, 77.6712, 90),
            ("mock_10", "Indiranagar", 12.9784, 77.6408, 100),
        ]

        for uid, name, lat, lon, aqi in mock_stations:
            station_id = await conn.fetchval("""
                INSERT INTO aqi_stations (station_uid, name, geom)
                VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326))
                ON CONFLICT (station_uid) DO UPDATE SET name = EXCLUDED.name
                RETURNING id;
            """, uid, name, lon, lat)

            await conn.execute("""
                INSERT INTO aqi_readings (station_id, aqi, pm25, pm10)
                VALUES ($1, $2, $3, $4);
            """, station_id, float(aqi), aqi * 0.6, aqi * 0.8)

        # Interpolate to grid
        await interpolate_aqi_to_grid(conn)
        logger.info("Mock AQI data seeded successfully.")

    finally:
        await conn.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape AQI data for Bangalore")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        await scrape_once()
    else:
        while True:
            try:
                await scrape_once()
            except Exception as e:
                logger.error(f"Scrape cycle failed: {e}")
            logger.info(f"Sleeping {SCRAPE_INTERVAL}s until next scrape...")
            await asyncio.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
