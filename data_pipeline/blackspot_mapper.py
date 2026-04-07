"""
Blackspot Mapper — Imports accident blackspot data and snaps to nearest road segments.

Usage:
    python blackspot_mapper.py [--csv path/to/blackspots.csv]

If no CSV is given, uses built-in Bangalore blackspot data.

Expected CSV format:
    lat,lon,severity,total_accidents,fatal_accidents,description
"""

import sys
import asyncio
import csv
import logging
from pathlib import Path

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

SEVERITY_WEIGHTS = {
    "low": 1.0,
    "moderate": 3.0,
    "high": 6.0,
    "critical": 10.0,
}

# ── Built-in Bangalore Blackspot Data ────────────────────────────────
# Source: Bangalore Traffic Police Annual Reports & News
BUILT_IN_BLACKSPOTS = [
    (12.9170, 77.6230, "critical", 45, 12, "Silk Board Junction — highest congestion point"),
    (12.9981, 77.5947, "critical", 38, 8, "Hebbal Flyover — high-speed merge accidents"),
    (13.0072, 77.5653, "high", 28, 5, "Yeshwanthpur Junction — heavy commercial traffic"),
    (12.9352, 77.6101, "high", 32, 7, "BTM Layout — Hosur Road intersection"),
    (12.9716, 77.5946, "critical", 41, 10, "Mehkri Circle to Sadashivanagar stretch"),
    (12.9540, 77.5688, "moderate", 18, 3, "Vijayanagar — Chord Road Junction"),
    (12.9784, 77.6408, "high", 25, 6, "Indiranagar 100ft Road — CMH Road junction"),
    (13.0285, 77.5180, "high", 22, 4, "Peenya Industrial Area — Tumkur Road"),
    (12.9098, 77.5858, "moderate", 15, 2, "Jayanagar 4th Block — 30th Cross"),
    (12.9655, 77.7500, "high", 30, 8, "Whitefield — ITPL Main Road"),
    (12.9502, 77.5985, "moderate", 20, 3, "Lalbagh Road — KH Road junction"),
    (12.8458, 77.6712, "high", 27, 6, "Electronic City Flyover — Hosur Road"),
    (13.0358, 77.5970, "moderate", 14, 2, "Hebbal — Bellary Road stretch"),
    (12.9770, 77.5720, "critical", 35, 9, "Rajajinagar — West of Chord Road"),
    (12.9610, 77.6390, "moderate", 16, 3, "Domlur — Old Airport Road"),
    (12.9428, 77.6289, "high", 23, 5, "Koramangala — Outer Ring Road junction"),
    (13.0612, 77.5854, "moderate", 12, 2, "Yelahanka — NH44 junction"),
    (12.9870, 77.6170, "moderate", 19, 4, "Ulsoor — MG Road stretch"),
    (12.9085, 77.6485, "high", 26, 7, "Bommanahalli — Hosur Road"),
    (12.9320, 77.5650, "moderate", 17, 3, "Basavanagudi — Bull Temple Road"),
]


async def load_from_csv(csv_path: Path) -> list[tuple]:
    """Parse blackspot CSV file."""
    records = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append((
                float(row["lat"]),
                float(row["lon"]),
                row.get("severity", "moderate"),
                int(row.get("total_accidents", 0)),
                int(row.get("fatal_accidents", 0)),
                row.get("description", ""),
            ))
    return records


async def snap_and_insert(conn: asyncpg.Connection, blackspots: list[tuple]):
    """Insert blackspots and snap each to the nearest road segment."""
    count = 0

    for lat, lon, severity, total, fatal, desc in blackspots:
        weight = SEVERITY_WEIGHTS.get(severity, 3.0)

        # Find nearest road segment
        nearest_edge = await conn.fetchval("""
            SELECT id FROM road_segments
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)
            LIMIT 1;
        """, lon, lat)

        await conn.execute("""
            INSERT INTO accident_blackspots
                (severity, severity_weight, total_accidents, fatal_accidents,
                 description, nearest_edge_id, geom)
            VALUES ($1, $2, $3, $4, $5, $6,
                    ST_SetSRID(ST_MakePoint($7, $8), 4326));
        """,
            severity, weight, total, fatal, desc, nearest_edge,
            lon, lat,
        )
        count += 1

    logger.info(f"Inserted {count} blackspots, snapped to nearest road segments.")


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Import accident blackspot data")
    parser.add_argument("--csv", type=str, help="Path to blackspot CSV file")
    args = parser.parse_args()

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        if args.csv:
            logger.info(f"Loading blackspots from CSV: {args.csv}")
            blackspots = await load_from_csv(Path(args.csv))
        else:
            logger.info("Using built-in Bangalore blackspot data.")
            blackspots = BUILT_IN_BLACKSPOTS

        await snap_and_insert(conn, blackspots)

    finally:
        await conn.close()

    logger.info("Blackspot mapping complete!")


if __name__ == "__main__":
    asyncio.run(main())
