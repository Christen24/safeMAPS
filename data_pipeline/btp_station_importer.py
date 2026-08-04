"""
SafeMAPS - BTP station-wise accident importer.

Builds accident blackspots from real Bangalore Traffic Police crash counts
published through OpenCity:

    https://data.opencity.in/dataset/bengaluru-road-crashes-data

The source data is station-jurisdiction aggregate data, not per-crash GPS
records. Each station is placed at one representative coordinate from
btp_station_geocoding.py, then weighted by its relative fatal/non-fatal crash
history. This is a real-data coarse proxy until point-level RTI data is
available.

Usage:
    python btp_station_importer.py
    python btp_station_importer.py --load-db --clear

Output:
    data/btp_station_blackspots.csv
"""

import argparse
import asyncio
import csv
import logging
import sys
from collections import defaultdict
from pathlib import Path

from btp_station_geocoding import STATION_COORDS, resolve_station

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "_btp_cache"
DEFAULT_OUT = BASE_DIR / "data" / "btp_station_blackspots.csv"

# (filename, station_col_index, [(year, fatal_idx, killed_idx_or_none, nonfatal_idx), ...])
SOURCES = [
    ("btp_2018_2020.csv", 1, [(2018, 2, 3, 4), (2019, 7, 8, 9), (2020, 12, 13, 14)]),
    ("btp_2020_2022.csv", 1, [(2021, 2, 3, 4), (2022, 7, 8, 9)]),
    ("btp_2023.csv", 2, [(2023, 3, 4, 5)]),
    ("btp_2024.csv", 2, [(2024, 4, None, 3)]),
    ("btp_2025.csv", 2, [(2025, 4, None, 3)]),
]


def _to_int(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def load_station_year_data():
    """Aggregate source rows into one record per canonical BTP station."""
    agg = defaultdict(
        lambda: {"fatal": 0, "killed": 0, "nonfatal": 0, "years": 0, "display": None}
    )
    unmapped = set()

    for filename, station_col, year_specs in SOURCES:
        path = CACHE_DIR / filename
        if not path.exists():
            logger.warning("Missing cached BTP source file, skipping: %s", path)
            continue

        with open(path, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))

        for row in rows[1:]:
            if len(row) <= station_col:
                continue
            raw_station = row[station_col].strip()
            if not raw_station or "total" in raw_station.lower():
                continue

            resolved = resolve_station(raw_station)
            if resolved is None:
                unmapped.add(raw_station)
                continue

            key, _lat, _lon, display = resolved
            for _year, fatal_idx, killed_idx, nonfatal_idx in year_specs:
                try:
                    fatal = _to_int(row[fatal_idx])
                    killed = _to_int(row[killed_idx]) if killed_idx is not None else fatal
                    nonfatal = _to_int(row[nonfatal_idx])
                except IndexError:
                    continue

                agg[key]["fatal"] += fatal
                agg[key]["killed"] += killed
                agg[key]["nonfatal"] += nonfatal
                agg[key]["years"] += 1
                agg[key]["display"] = display

    if unmapped:
        logger.warning(
            "%s station name(s) not in geocoding table and skipped: %s",
            len(unmapped),
            sorted(unmapped),
        )

    return agg


def classify(weight: float) -> str:
    if weight >= 7:
        return "critical"
    if weight >= 4:
        return "high"
    if weight >= 2:
        return "moderate"
    return "low"


def build_blackspots():
    """Return CSV-ready blackspot dicts sorted by risk descending."""
    agg = load_station_year_data()
    raw_scores = {}

    for key, data in agg.items():
        years = data["years"]
        if years:
            raw_scores[key] = (
                data["fatal"] * 5 + data["killed"] * 3 + data["nonfatal"]
            ) / years

    if not raw_scores:
        return []

    low = min(raw_scores.values())
    high = max(raw_scores.values())
    span = high - low or 1.0

    blackspots = []
    for key, data in agg.items():
        if key not in raw_scores:
            continue

        severity_weight = round((raw_scores[key] - low) / span * 10.0, 2)
        lat, lon, display = STATION_COORDS[key]
        total_accidents = data["fatal"] + data["nonfatal"]
        blackspots.append(
            {
                "lat": lat,
                "lon": lon,
                "severity": classify(severity_weight),
                "severity_weight": severity_weight,
                "total_accidents": total_accidents,
                "fatal_accidents": data["fatal"],
                "description": (
                    f"{display} Traffic PS jurisdiction - {data['fatal']} fatal, "
                    f"{data['nonfatal']} non-fatal cases across {data['years']} yr(s) "
                    f"(BTP/OpenCity real data)"
                ),
            }
        )

    blackspots.sort(key=lambda item: item["severity_weight"], reverse=True)
    return blackspots


def write_csv(blackspots, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "lat",
                "lon",
                "severity",
                "severity_weight",
                "total_accidents",
                "fatal_accidents",
                "description",
            ],
        )
        writer.writeheader()
        writer.writerows(blackspots)
    logger.info("Wrote %s station blackspots to %s", len(blackspots), out_path)


async def load_into_db(csv_path: Path, clear: bool) -> None:
    """Reuse blackspot_mapper.py so station points snap to nearest road edge."""
    import asyncpg
    import blackspot_mapper as mapper

    backend_dir = BASE_DIR.parent / "backend"
    if backend_dir.exists():
        sys.path.insert(0, str(backend_dir))
    else:
        sys.path.insert(0, str(BASE_DIR.parent))

    from config import settings

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        if not await mapper.check_road_segments(conn):
            return
        if clear:
            await mapper.clear_blackspots(conn)
        records = await mapper.load_from_csv(csv_path)
        count = await mapper.snap_and_insert(conn, records)
        logger.info("Imported %s real BTP station blackspots into accident_blackspots.", count)
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build/import real BTP station-wise crash blackspots"
    )
    parser.add_argument("--load-db", action="store_true", help="Insert generated CSV into PostGIS")
    parser.add_argument("--clear", action="store_true", help="Clear existing blackspots first")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output CSV path")
    args = parser.parse_args()

    blackspots = build_blackspots()
    write_csv(blackspots, args.out)

    print(f"\n{len(blackspots)} real BTP station-level blackspots built.")
    print("Top 5 by severity:")
    for item in blackspots[:5]:
        print(
            f"  [{item['severity']:>8}] "
            f"w={item['severity_weight']:>4.1f}  {item['description']}"
        )

    if args.load_db:
        asyncio.run(load_into_db(args.out, args.clear))
    else:
        print("\nTo load into the database, run:")
        print(f"  python btp_station_importer.py --load-db --clear")
        print("Then refresh the backend graph cache:")
        print("  curl -X POST http://localhost:8000/api/admin/refresh-graph")


if __name__ == "__main__":
    main()
