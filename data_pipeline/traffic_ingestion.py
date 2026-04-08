"""
Traffic Data Ingestion — Fetches real-time traffic data from TomTom Traffic Flow API.

Usage:
    python traffic_ingestion.py [--once]

Requires: TOMTOM_API_KEY environment variable.
"""

import sys
import asyncio
import logging
from pathlib import Path

import httpx
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

TOMTOM_FLOW_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
SCRAPE_INTERVAL = 15 * 60  # 15 minutes


async def fetch_traffic_flow(lat: float, lon: float, api_key: str) -> dict:
    """Fetch traffic flow data for a point from TomTom."""
    params = {
        "key": api_key,
        "point": f"{lat},{lon}",
        "unit": "KMPH",
        "thickness": 1,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(TOMTOM_FLOW_URL, params=params)
        if resp.status_code != 200:
            return {}
        data = resp.json()

    flow = data.get("flowSegmentData", {})
    return {
        "current_speed": flow.get("currentSpeed"),
        "free_flow_speed": flow.get("freeFlowSpeed"),
        "confidence": flow.get("confidence"),
    }


async def scrape_traffic():
    """Fetch traffic for sampled road segments and update the database."""
    api_key = settings.tomtom_api_key
    if not api_key:
        logger.error("TOMTOM_API_KEY not set. Using mock traffic data.")
        await seed_mock_traffic()
        return

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        # Sample road segment midpoints (limit to avoid API quota exhaustion)
        edges = await conn.fetch("""
            SELECT id,
                   ST_Y(ST_Centroid(geom)) AS lat,
                   ST_X(ST_Centroid(geom)) AS lon
            FROM road_segments
            WHERE road_type IN ('primary', 'secondary', 'trunk', 'motorway')
            ORDER BY RANDOM()
            LIMIT 100;
        """)

        logger.info(f"Fetching traffic for {len(edges)} road segments...")
        count = 0

        for edge in edges:
            try:
                flow = await fetch_traffic_flow(edge["lat"], edge["lon"], api_key)
                if not flow.get("current_speed"):
                    continue

                current = flow["current_speed"]
                freeflow = flow.get("free_flow_speed", current)
                congestion = 1.0 - (current / max(freeflow, 1))

                await conn.execute("""
                    INSERT INTO traffic_snapshots (edge_id, current_speed, free_flow_speed, congestion)
                    VALUES ($1, $2, $3, $4);
                """, edge["id"], current, freeflow, max(congestion, 0))

                # Also update the edge's speed for routing
                await conn.execute("""
                    UPDATE road_segments SET speed_kmh = $1 WHERE id = $2;
                """, current, edge["id"])

                count += 1
            except Exception as e:
                logger.warning(f"Failed for edge {edge['id']}: {e}")

            # Rate limiting
            await asyncio.sleep(0.5)

        logger.info(f"Updated traffic for {count} segments.")

    finally:
        await conn.close()


async def seed_mock_traffic():
    """Seed mock traffic data for development."""
    import random
    logger.info("Seeding mock traffic data...")

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        edges = await conn.fetch("""
            SELECT id, speed_kmh FROM road_segments LIMIT 500;
        """)

        for edge in edges:
            freeflow = edge["speed_kmh"]
            # Simulate congestion: 50-100% of free flow speed
            current = freeflow * random.uniform(0.5, 1.0)
            congestion = 1.0 - (current / max(freeflow, 1))

            await conn.execute("""
                INSERT INTO traffic_snapshots (edge_id, current_speed, free_flow_speed, congestion)
                VALUES ($1, $2, $3, $4);
            """, edge["id"], current, freeflow, max(congestion, 0))

        logger.info(f"Seeded mock traffic for {len(edges)} segments.")

    finally:
        await conn.close()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ingest traffic data for Bangalore")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        await scrape_traffic()
    else:
        while True:
            try:
                await scrape_traffic()
            except Exception as e:
                logger.error(f"Traffic scrape failed: {e}")
            logger.info(f"Sleeping {SCRAPE_INTERVAL}s...")
            await asyncio.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
