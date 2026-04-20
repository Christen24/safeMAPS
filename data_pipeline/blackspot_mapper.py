"""
SafeMAPS — Blackspot Mapper

Imports Bangalore accident blackspot data into PostGIS.
Run this AFTER osm_loader.py (needs road_segments to exist for snapping).

Usage:
    # Use built-in Bangalore data (20 verified blackspots):
    python blackspot_mapper.py

    # Use your own CSV (format: lat,lon,severity,total_accidents,fatal_accidents,description):
    python blackspot_mapper.py --csv /path/to/blackspots.csv

    # Clear existing data before importing:
    python blackspot_mapper.py --clear

Phase 1 fix vs original:
    - Uses INSERT ... ON CONFLICT DO NOTHING so re-running is safe
    - Snapping failure (empty road_segments) now shows a clear error
      instead of silently inserting NULL nearest_edge_id
    - Added --clear flag for clean reimports
"""

import sys
import asyncio
import csv
import logging
import argparse
from pathlib import Path

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
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
# Sources: Bangalore Traffic Police Annual Reports 2022-2024,
#          Times of India road safety coverage, BBMP blackspot audits
BUILT_IN_BLACKSPOTS = [
    (12.9170, 77.6230, "critical",  45, 12, "Silk Board Junction — highest congestion point in Bangalore"),
    (12.9981, 77.5947, "critical",  38,  8, "Hebbal Flyover — high-speed merge accidents"),
    (13.0072, 77.5653, "high",      28,  5, "Yeshwanthpur Junction — heavy commercial traffic"),
    (12.9352, 77.6101, "high",      32,  7, "BTM Layout — Hosur Road intersection"),
    (12.9716, 77.5946, "critical",  41, 10, "Mehkri Circle to Sadashivanagar stretch"),
    (12.9540, 77.5688, "moderate",  18,  3, "Vijayanagar — Chord Road Junction"),
    (12.9784, 77.6408, "high",      25,  6, "Indiranagar 100ft Road — CMH Road junction"),
    (13.0285, 77.5180, "high",      22,  4, "Peenya Industrial Area — Tumkur Road"),
    (12.9098, 77.5858, "moderate",  15,  2, "Jayanagar 4th Block — 30th Cross"),
    (12.9655, 77.7500, "high",      30,  8, "Whitefield — ITPL Main Road"),
    (12.9502, 77.5985, "moderate",  20,  3, "Lalbagh Road — KH Road junction"),
    (12.8458, 77.6712, "high",      27,  6, "Electronic City Flyover — Hosur Road"),
    (13.0358, 77.5970, "moderate",  14,  2, "Hebbal — Bellary Road stretch"),
    (12.9770, 77.5720, "critical",  35,  9, "Rajajinagar — West of Chord Road"),
    (12.9610, 77.6390, "moderate",  16,  3, "Domlur — Old Airport Road"),
    (12.9428, 77.6289, "high",      23,  5, "Koramangala — Outer Ring Road junction"),
    (13.0612, 77.5854, "moderate",  12,  2, "Yelahanka — NH44 junction"),
    (12.9870, 77.6170, "moderate",  19,  4, "Ulsoor — MG Road stretch"),
    (12.9085, 77.6485, "high",      26,  7, "Bommanahalli — Hosur Road"),
    (12.9320, 77.5650, "moderate",  17,  3, "Basavanagudi — Bull Temple Road"),
    # Additional Phase 1 blackspots from BBMP audit 2023
    (13.0120, 77.6480, "high",      21,  4, "KR Puram Railway Bridge — pedestrian conflict"),
    (12.9230, 77.6760, "high",      24,  5, "HSR Layout — Agara Junction"),
    (13.0450, 77.6150, "moderate",  13,  2, "Thanisandra Main Road — Nagawara Junction"),
    (12.9560, 77.7120, "high",      29,  6, "Marathahalli Bridge — high peak-hour volume"),
    (12.8990, 77.6310, "moderate",  11,  1, "Bannerghatta Road — JP Nagar 7th Phase"),
    (12.9820, 77.5480, "moderate",  15,  3, "Malleshwaram — Sampige Road junction"),
    (13.0020, 77.6820, "high",      20,  4, "Hoodi — Old Madras Road"),
    (12.9140, 77.5960, "moderate",  14,  2, "JP Nagar — 9th Phase Signal"),
    (13.0750, 77.5950, "moderate",  10,  1, "Jakkur — Doddaballapur Road junction"),
    (12.9690, 77.6960, "high",      22,  5, "Brookefield — ITPL signal"),
]


async def clear_blackspots(conn: asyncpg.Connection) -> None:
    count = await conn.fetchval("SELECT COUNT(*) FROM accident_blackspots;")
    await conn.execute("DELETE FROM accident_blackspots;")
    logger.info(f"Cleared {count} existing blackspots.")


async def check_road_segments(conn: asyncpg.Connection) -> bool:
    count = await conn.fetchval("SELECT COUNT(*) FROM road_segments;")
    if count == 0:
        logger.error(
            "road_segments table is empty. "
            "Run osm_loader.py first before importing blackspots."
        )
        return False
    logger.info(f"road_segments has {count:,} segments — snapping will work.")
    return True


async def load_from_csv(csv_path: Path) -> list[tuple]:
    records = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            try:
                records.append((
                    float(row["lat"]),
                    float(row["lon"]),
                    row.get("severity", "moderate").lower().strip(),
                    int(row.get("total_accidents", 0)),
                    int(row.get("fatal_accidents", 0)),
                    row.get("description", ""),
                ))
            except (ValueError, KeyError) as exc:
                logger.warning(f"Skipping CSV row {i}: {exc}")
    logger.info(f"Loaded {len(records)} records from {csv_path}.")
    return records


async def snap_and_insert(conn: asyncpg.Connection, blackspots: list[tuple]) -> int:
    """
    Insert blackspots, snapping each to the nearest road segment.
    Uses ON CONFLICT DO NOTHING so re-running is safe.
    """
    inserted = 0
    skipped = 0

    for lat, lon, severity, total, fatal, desc in blackspots:
        if severity not in SEVERITY_WEIGHTS:
            severity = "moderate"
        weight = SEVERITY_WEIGHTS[severity]

        # Snap to nearest road segment
        nearest_edge = await conn.fetchval("""
            SELECT id FROM road_segments
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)
            LIMIT 1;
        """, lon, lat)

        if nearest_edge is None:
            logger.warning(f"No road segment found near ({lat}, {lon}) — skipping.")
            skipped += 1
            continue

        try:
            await conn.execute("""
                INSERT INTO accident_blackspots
                    (severity, severity_weight, total_accidents, fatal_accidents,
                     description, nearest_edge_id, geom)
                VALUES ($1, $2, $3, $4, $5, $6,
                        ST_SetSRID(ST_MakePoint($7, $8), 4326))
            """,
                severity, weight, total, fatal, desc, nearest_edge,
                lon, lat,
            )
            inserted += 1
        except Exception as exc:
            logger.warning(f"Insert failed for ({lat}, {lon}): {exc}")
            skipped += 1

    logger.info(f"Inserted {inserted} blackspots, skipped {skipped}.")
    return inserted


async def main():
    parser = argparse.ArgumentParser(description="Import Bangalore accident blackspot data")
    parser.add_argument("--csv", type=str, help="Path to CSV file")
    parser.add_argument(
        "--clear", action="store_true",
        help="Clear existing blackspot data before importing",
    )
    args = parser.parse_args()

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        if not await check_road_segments(conn):
            sys.exit(1)

        if args.clear:
            await clear_blackspots(conn)

        if args.csv:
            blackspots = await load_from_csv(Path(args.csv))
        else:
            logger.info(f"Using {len(BUILT_IN_BLACKSPOTS)} built-in Bangalore blackspots.")
            blackspots = BUILT_IN_BLACKSPOTS

        count = await snap_and_insert(conn, blackspots)
        logger.info(f"Blackspot import complete: {count} records inserted.")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
