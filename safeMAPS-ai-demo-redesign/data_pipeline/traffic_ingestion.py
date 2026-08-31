"""
Traffic Data Ingestion — Fetches real-time traffic speeds from TomTom.

Usage (standalone):
    python traffic_ingestion.py --once      # single cycle then exit
    python traffic_ingestion.py             # loop every 5 minutes

Phase 2 change
──────────────
scrape_traffic() now returns dict[int, float] — {edge_id: speed_kmh} —
for every segment it successfully updated. The scheduler passes this
dict to graph_cache.update_speeds() so the in-memory adjacency list
reflects live congestion without a full graph reload.

Previously the function returned None, so the cache was never informed
of speed changes and A* always used the original OSM free-flow speeds.

Both scrape_traffic() (real TomTom API) and seed_mock_traffic()
(development fallback) return the same shape so the scheduler
doesn't need to distinguish between them.
"""

import sys
import asyncio
import logging
import random
from pathlib import Path

import httpx
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

TOMTOM_FLOW_URL = (
    "https://api.tomtom.com/traffic/services/4"
    "/flowSegmentData/absolute/10/json"
)
SCRAPE_INTERVAL = 5 * 60   # 5 minutes (tighter loop than AQI)


# ── TomTom API call ───────────────────────────────────────────────────

async def fetch_traffic_flow(lat: float, lon: float, api_key: str) -> dict:
    """Fetch live traffic flow for a single point from TomTom."""
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


# ── Main scrape function ──────────────────────────────────────────────

async def scrape_traffic() -> dict[int, float]:
    """
    Fetch live speeds from TomTom for a sample of major road segments,
    write updated speed_kmh to PostGIS, and return the mapping of
    updated edge IDs → new speeds for the graph cache to consume.

    Returns
    ───────
    dict[int, float]
        {edge_id: new_speed_kmh} for every segment successfully updated.
        Empty dict if no API key is configured (mock fallback is called).
    """
    api_key = settings.tomtom_api_key
    if not api_key:
        logger.warning("TOMTOM_API_KEY not set — using mock traffic data.")
        return await seed_mock_traffic()

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    updated: dict[int, float] = {}

    try:
        # Sample major road segment midpoints.
        # We cap at 100 to stay within TomTom's free-tier quota.
        # Primary/trunk roads matter most for routing decisions.
        edges = await conn.fetch("""
            SELECT
                id,
                ST_Y(ST_Centroid(geom)) AS lat,
                ST_X(ST_Centroid(geom)) AS lon
            FROM road_segments
            WHERE road_type IN ('motorway', 'trunk', 'primary', 'secondary')
            ORDER BY RANDOM()
            LIMIT 100;
        """)

        logger.info(f"Fetching TomTom traffic for {len(edges)} segments...")

        for edge in edges:
            try:
                flow = await fetch_traffic_flow(
                    edge["lat"], edge["lon"], api_key
                )
                if not flow.get("current_speed"):
                    continue

                current_speed = float(flow["current_speed"])
                free_flow    = float(flow.get("free_flow_speed") or current_speed)
                congestion   = max(0.0, 1.0 - current_speed / max(free_flow, 1.0))

                # Persist to PostGIS so the history is queryable
                await conn.execute("""
                    INSERT INTO traffic_snapshots
                        (edge_id, current_speed, free_flow_speed, congestion)
                    VALUES ($1, $2, $3, $4);
                """, edge["id"], current_speed, free_flow, congestion)

                # Update the canonical speed on the segment row
                await conn.execute("""
                    UPDATE road_segments
                    SET speed_kmh = $1
                    WHERE id = $2;
                """, current_speed, edge["id"])

                # Collect for the return value → graph_cache.update_speeds()
                updated[edge["id"]] = current_speed

            except Exception as exc:
                logger.warning(f"Traffic fetch failed for edge {edge['id']}: {exc}")

            # Gentle rate limiting — TomTom free tier: 5 req/s
            await asyncio.sleep(0.2)

        logger.info(f"Traffic scrape complete: {len(updated)} segments updated.")

    finally:
        await conn.close()

    return updated


# ── Development fallback ──────────────────────────────────────────────

async def seed_mock_traffic() -> dict[int, float]:
    """
    Generate synthetic congestion for development when no TomTom key exists.

    Simulates rush-hour patterns:
      - Motorway/trunk: 60–90% of free-flow speed
      - Primary/secondary: 40–80% of free-flow speed
      - Others: 50–100% of free-flow speed

    Returns the same {edge_id: speed_kmh} shape as scrape_traffic()
    so graph_cache.update_speeds() receives consistent input.
    """
    logger.info("Seeding mock traffic data...")

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    updated: dict[int, float] = {}

    try:
        edges = await conn.fetch("""
            SELECT id, speed_kmh, road_type
            FROM road_segments
            LIMIT 500;
        """)

        records = []
        for edge in edges:
            road_type = edge["road_type"] or "residential"
            free_flow = float(edge["speed_kmh"])

            # Vary congestion by road type
            if road_type in ("motorway", "trunk"):
                ratio = random.uniform(0.60, 0.90)
            elif road_type in ("primary", "secondary"):
                ratio = random.uniform(0.40, 0.80)
            else:
                ratio = random.uniform(0.50, 1.00)

            current_speed = round(free_flow * ratio, 1)
            congestion    = round(1.0 - ratio, 3)

            records.append((edge["id"], current_speed, free_flow, congestion))
            updated[edge["id"]] = current_speed

        # Bulk insert traffic snapshots
        await conn.executemany("""
            INSERT INTO traffic_snapshots
                (edge_id, current_speed, free_flow_speed, congestion)
            VALUES ($1, $2, $3, $4);
        """, records)

        logger.info(f"Mock traffic seeded for {len(updated)} segments.")

    finally:
        await conn.close()

    return updated


# ── Standalone entry point ────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ingest traffic data for Bangalore")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        result = await scrape_traffic()
        logger.info(f"Done — {len(result)} edges updated.")
    else:
        while True:
            try:
                await scrape_traffic()
            except Exception as exc:
                logger.error(f"Traffic scrape failed: {exc}")
            logger.info(f"Sleeping {SCRAPE_INTERVAL}s until next cycle...")
            await asyncio.sleep(SCRAPE_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
