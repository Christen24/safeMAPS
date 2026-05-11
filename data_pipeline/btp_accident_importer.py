"""
SafeMAPS — Real Bangalore Accident Data Pipeline
=================================================
Downloads all BTP crash CSVs and the jurisdiction KML from OpenCity,
geocodes each police station to GPS coordinates, weights by crash severity,
and imports everything into the accident_blackspots table.

Run from the project root:
    cd data_pipeline
    pip install httpx asyncpg fastkml lxml shapely
    python btp_accident_importer.py

What it does, step by step
---------------------------
1. Download 4 BTP station-wise crash CSVs (2020–2025) from OpenCity
2. Download the 2022 jurisdiction KML (station boundary polygons)
3. Parse KML → {station_name: polygon_centroid (lat, lon)}
4. Parse all CSVs → {station_name: {fatal, non_fatal, total, years}}
5. Merge: match station names between CSV and KML (fuzzy match)
6. Supplement: for stations not in KML, geocode via Nominatim
7. Compute severity_weight = (fatal × 3 + non_fatal × 1) / years_of_data
8. Clear existing blackspots and insert real data into accident_blackspots
9. Print a summary of what was imported

Data sources (all public domain, from OpenCity)
------------------------------------------------
CSVs: https://data.opencity.in/dataset/bengaluru-road-crashes-data
KML:  https://data.opencity.in/dataset/bengaluru-traffic-police-jurisdictions
"""

import sys
import asyncio
import csv
import io
import logging
import re
import time
from pathlib import Path

import httpx
import asyncpg
from shapely.geometry import Point, Polygon

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

# ── Direct download URLs from OpenCity ────────────────────────────────
DATASETS = {
    "2018_2020": "https://data.opencity.in/dataset/94e986d6-7836-4a8e-aa3f-273bee4ea795/resource/fcc5762c-2739-4c86-93fd-67fa4cca0750/download/aef42379-f1f7-4a3b-94f5-5f344e7120f2.csv",
    "2020_2022": "https://data.opencity.in/dataset/94e986d6-7836-4a8e-aa3f-273bee4ea795/resource/b3744a95-e486-4022-9c20-ad178dcf23dd/download/492d3dc6-ffc3-4b0e-b7d9-176d0ef7f1ec.csv",
    "2023":      "https://data.opencity.in/dataset/94e986d6-7836-4a8e-aa3f-273bee4ea795/resource/8f0f281c-2cb6-4491-ac76-d1874ce38583/download/abc5af52-08a7-4435-8ba1-12b99f62ee28.csv",
    "2024":      "https://data.opencity.in/dataset/94e986d6-7836-4a8e-aa3f-273bee4ea795/resource/e59bc255-7b94-49df-b934-9b40fb2cc741/download/74e645e3-85d2-4d81-a133-4f346f87fdd6.csv",
    "2025":      "https://data.opencity.in/dataset/94e986d6-7836-4a8e-aa3f-273bee4ea795/resource/cf9acd17-f593-45b6-9b98-d3eab9d81143/download/btp_2025_station_wise.csv",
}

KML_URL = "https://data.opencity.in/dataset/ba9be930-e313-4f16-b4e2-39a5d8d7eb3f/resource/3e7e6a4d-4dce-44ec-aef3-64278c30c06f/download/faceb23a-79e8-47e9-b9ba-c418c5cf6e9c.kml"

# Local cache directory so re-runs don't re-download
CACHE_DIR = Path(__file__).parent / "_btp_cache"
CACHE_DIR.mkdir(exist_ok=True)


# ── Download helpers ───────────────────────────────────────────────────

async def download(url: str, filename: str) -> bytes:
    """Download a URL, caching to disk so re-runs are instant."""
    cache_path = CACHE_DIR / filename
    if cache_path.exists():
        logger.info(f"  (cached) {filename}")
        return cache_path.read_bytes()

    logger.info(f"  Downloading {filename}...")
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        cache_path.write_bytes(resp.content)
        logger.info(f"  Saved {len(resp.content):,} bytes → {cache_path}")
        return resp.content


# ── CSV parsing ────────────────────────────────────────────────────────

def _safe_int(val: str) -> int:
    """Convert a string to int, returning 0 for empty or non-numeric."""
    try:
        return int(str(val).strip().replace(",", ""))
    except (ValueError, TypeError):
        return 0


def parse_csv_2023_style(raw: bytes, year_label: str) -> dict[str, dict]:
    """
    Parse the 2023-format CSV.
    Columns: Zone, Sub-division, Station,
             {year} - Fatal Cases, {year} - Killed People,
             {year} - Non-Fatal, {year} - Injured People,
             {year} - Total Cases

    Returns {station_name: {fatal, killed, non_fatal, injured, total}}
    Skips subtotal rows (Station is empty or contains 'Total').
    """
    results = {}
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    # Discover column names dynamically
    fieldnames = reader.fieldnames or []
    fatal_col    = next((f for f in fieldnames if "fatal cases" in f.lower()), None)
    killed_col   = next((f for f in fieldnames if "killed" in f.lower()), None)
    nonfatal_col = next((f for f in fieldnames if "non-fatal" in f.lower() or "non fatal" in f.lower()), None)
    injured_col  = next((f for f in fieldnames if "injured" in f.lower()), None)
    total_col    = next((f for f in fieldnames if "total cases" in f.lower()), None)
    station_col  = next((f for f in fieldnames if "station" in f.lower()), "Station")

    for row in reader:
        station = (row.get(station_col) or "").strip()
        # Skip empty rows, zone totals, sub-division totals, grand total
        if not station or "total" in station.lower() or "grand" in station.lower():
            continue

        results[station] = {
            "fatal":    _safe_int(row.get(fatal_col,    "0")),
            "killed":   _safe_int(row.get(killed_col,   "0")),
            "non_fatal":_safe_int(row.get(nonfatal_col, "0")),
            "injured":  _safe_int(row.get(injured_col,  "0")),
            "total":    _safe_int(row.get(total_col,    "0")),
            "year":     year_label,
        }

    logger.info(f"  Parsed {len(results)} stations from {year_label} CSV.")
    return results


def merge_station_data(all_years: list[dict]) -> dict[str, dict]:
    """
    Merge multi-year station data into a single dict.
    For stations present in multiple years, sum the crash counts.
    Returns {station_name: {fatal, non_fatal, total, years_count}}
    """
    merged = {}
    for year_data in all_years:
        for station, counts in year_data.items():
            if station not in merged:
                merged[station] = {
                    "fatal": 0, "killed": 0,
                    "non_fatal": 0, "injured": 0,
                    "total": 0, "years_count": 0,
                }
            merged[station]["fatal"]     += counts["fatal"]
            merged[station]["killed"]    += counts["killed"]
            merged[station]["non_fatal"] += counts["non_fatal"]
            merged[station]["injured"]   += counts["injured"]
            merged[station]["total"]     += counts["total"]
            merged[station]["years_count"] += 1

    logger.info(f"Merged {len(merged)} unique stations across all years.")
    return merged


# ── KML parsing ────────────────────────────────────────────────────────

def parse_kml_centroids(raw: bytes) -> dict[str, tuple[float, float]]:
    """
    Parse a KML file of traffic jurisdiction polygons.
    Returns {station_name: (centroid_lat, centroid_lon)}.

    Uses standard XML parsing — no KML library required.
    """
    import xml.etree.ElementTree as ET

    centroids = {}

    # KML namespaces
    ns = {
        "kml": "http://www.opengis.net/kml/2.2",
        "kml22": "http://earth.google.com/kml/2.2",
    }

    text = raw.decode("utf-8-sig", errors="replace")
    root = ET.fromstring(text)

    # Try both namespace variants
    placemarks = (
        root.findall(".//kml:Placemark", ns)
        or root.findall(".//kml22:Placemark", ns)
        or root.findall(".//Placemark")
    )

    for pm in placemarks:
        # Station name from <name> tag
        name_el = pm.find("kml:name", ns) or pm.find("name")
        if name_el is None or not name_el.text:
            continue
        name = name_el.text.strip()

        # Get polygon coordinates
        coords_el = (
            pm.find(".//kml:coordinates", ns)
            or pm.find(".//coordinates")
        )
        if coords_el is None or not coords_el.text:
            continue

        try:
            # KML coordinates: "lon,lat,alt lon,lat,alt ..."
            raw_coords = coords_el.text.strip().split()
            points = []
            for c in raw_coords:
                parts = c.split(",")
                if len(parts) >= 2:
                    lon, lat = float(parts[0]), float(parts[1])
                    points.append((lon, lat))

            if len(points) < 3:
                continue

            # Shapely centroid
            poly = Polygon(points)
            centroid = poly.centroid
            centroids[name] = (centroid.y, centroid.x)  # (lat, lon)

        except Exception as e:
            logger.warning(f"  KML parse failed for '{name}': {e}")

    logger.info(f"Parsed {len(centroids)} station polygons from KML.")
    return centroids


# ── Name matching ──────────────────────────────────────────────────────

def normalise(name: str) -> str:
    """Lowercase, strip punctuation/spaces for fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_stations(
    csv_stations: dict[str, dict],
    kml_centroids: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """
    Fuzzy-match CSV station names to KML station names.
    Returns {csv_station_name: (lat, lon)} for matched stations.

    Strategy:
    1. Exact match (after normalisation)
    2. Substring match (one name contains the other)
    3. Manual overrides for known mismatches
    """
    # Manual overrides for known BTP name mismatches between CSV and KML
    OVERRIDES = {
        "Int. Aiport":     "International Airport",
        "Airport":         "International Airport",
        "H.Grounds":       "High Grounds",
        "S.S.Nagar":       "Sadashivanagar",
        "U.Gate":          "Ulsoor Gate",
        "W.Garden":        "Wilson Garden",
        "K G Halli":       "KG Halli",
        "K R Puram":       "KR Puram",
        "Micolayout":      "Mico Layout",
        "Bytarayanapura":  "Byatarayanapura",
        "K S Layout":      "KS Layout",
        "Thalagattapura":  "Thalagattapura",
        "Chikkajala":      "Chikkajala",
        "Banasawadi":      "Banaswadi",
    }

    kml_norm = {normalise(k): v for k, v in kml_centroids.items()}
    matched = {}
    unmatched = []

    for csv_name in csv_stations:
        # Apply manual override if exists
        lookup_name = OVERRIDES.get(csv_name, csv_name)
        norm = normalise(lookup_name)

        # 1. Exact normalised match
        if norm in kml_norm:
            matched[csv_name] = kml_norm[norm]
            continue

        # 2. Substring match
        found = None
        for kml_norm_name, coords in kml_norm.items():
            if norm in kml_norm_name or kml_norm_name in norm:
                found = coords
                break

        if found:
            matched[csv_name] = found
        else:
            unmatched.append(csv_name)

    logger.info(
        f"Matched {len(matched)}/{len(csv_stations)} stations via KML. "
        f"Will geocode {len(unmatched)} via Nominatim."
    )
    if unmatched:
        logger.info(f"  Unmatched: {unmatched}")

    return matched, unmatched


# ── Nominatim geocoding for unmatched stations ─────────────────────────

async def geocode_station(name: str) -> tuple[float, float] | None:
    """
    Geocode a police station name via Nominatim OSM.
    Adds ", Bangalore, India" for context.
    Returns (lat, lon) or None if not found.
    """
    query = f"{name} Traffic Police Station, Bangalore, India"
    url   = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}

    async with httpx.AsyncClient(
        timeout=15,
        headers={"User-Agent": "SafeMAPS/1.0 (road safety research)"},
    ) as client:
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            results = resp.json()
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
        except Exception as e:
            logger.warning(f"  Nominatim failed for '{name}': {e}")

    return None


async def geocode_all_unmatched(
    unmatched: list[str],
) -> dict[str, tuple[float, float]]:
    """Geocode all unmatched station names with rate limiting."""
    geocoded = {}
    for name in unmatched:
        result = await geocode_station(name)
        if result:
            geocoded[name] = result
            logger.info(f"  Geocoded '{name}' → {result}")
        else:
            logger.warning(f"  Could not geocode '{name}' — skipping.")
        await asyncio.sleep(1.1)   # Nominatim rate limit: 1 req/sec
    return geocoded


# ── Severity weight calculation ────────────────────────────────────────

def compute_severity(counts: dict) -> tuple[float, str]:
    """
    Severity weight for a station's accumulated crash data.

    Formula:
        raw = (fatal_cases × 5 + killed_people × 3 + non_fatal × 1)
              / max(years_count, 1)
        weight = min(raw / 10.0, 10.0)   # normalise to 0–10 scale

    Severity tier:
        weight ≥ 7 → critical
        weight ≥ 4 → high
        weight ≥ 2 → moderate
        else       → low
    """
    years  = max(counts.get("years_count", 1), 1)
    raw    = (
        counts["fatal"]     * 5
        + counts["killed"]  * 3
        + counts["non_fatal"] * 1
    ) / years

    weight = min(raw / 10.0, 10.0)

    if weight >= 7:   severity = "critical"
    elif weight >= 4: severity = "high"
    elif weight >= 2: severity = "moderate"
    else:             severity = "low"

    return round(weight, 2), severity


# ── PostGIS import ─────────────────────────────────────────────────────

async def import_to_postgis(
    station_data: dict[str, dict],
    coordinates:  dict[str, tuple[float, float]],
    clear_existing: bool = True,
) -> int:
    """
    Insert real BTP blackspot data into accident_blackspots.
    Returns the number of records inserted.
    """
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    inserted = 0
    skipped  = 0

    try:
        if clear_existing:
            count = await conn.fetchval("SELECT COUNT(*) FROM accident_blackspots;")
            await conn.execute("DELETE FROM accident_blackspots;")
            logger.info(f"Cleared {count} existing blackspots.")

        records = []
        for station_name, counts in station_data.items():
            if station_name not in coordinates:
                skipped += 1
                continue

            lat, lon = coordinates[station_name]
            weight, severity = compute_severity(counts)
            years = max(counts.get("years_count", 1), 1)

            # Average annual figures for display
            avg_total  = round(counts["total"]     / years)
            avg_fatal  = round(counts["fatal"]     / years)

            desc = (
                f"{station_name} Traffic Station jurisdiction — "
                f"avg {avg_total} crashes/yr, {avg_fatal} fatal/yr "
                f"({years} yr{'s' if years > 1 else ''} of BTP data)"
            )

            records.append((
                severity, weight,
                avg_total, avg_fatal,
                desc, lat, lon,
            ))

        # Find nearest edge for each blackspot
        for severity, weight, total, fatal, desc, lat, lon in records:
            try:
                nearest_edge = await conn.fetchval("""
                    SELECT id FROM road_segments
                    ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326)
                    LIMIT 1;
                """, lon, lat)

                await conn.execute("""
                    INSERT INTO accident_blackspots
                        (severity, severity_weight, total_accidents,
                         fatal_accidents, description, nearest_edge_id, geom)
                    VALUES ($1, $2, $3, $4, $5, $6,
                            ST_SetSRID(ST_MakePoint($7, $8), 4326))
                """,
                    severity, weight, total, fatal, desc,
                    nearest_edge, lon, lat,
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"Insert failed for {desc[:40]}…: {e}")
                skipped += 1

        logger.info(
            f"Import complete: {inserted} blackspots inserted, "
            f"{skipped} skipped (no coordinates or DB error)."
        )

    finally:
        await conn.close()

    return inserted


# ── Summary report ─────────────────────────────────────────────────────

def print_summary(
    station_data: dict[str, dict],
    coordinates:  dict[str, tuple[float, float]],
):
    """Print a ranked table of the worst accident zones."""
    print("\n" + "=" * 70)
    print("TOP 20 ACCIDENT ZONES — BANGALORE (BTP DATA, ALL YEARS)")
    print("=" * 70)
    print(f"{'Station':<25} {'Severity':<10} {'Weight':>7} {'Total/yr':>9} {'Fatal/yr':>9}")
    print("-" * 70)

    ranked = []
    for name, counts in station_data.items():
        if name not in coordinates:
            continue
        weight, severity = compute_severity(counts)
        years   = max(counts.get("years_count", 1), 1)
        avg_tot = round(counts["total"] / years)
        avg_fat = round(counts["fatal"] / years)
        ranked.append((name, severity, weight, avg_tot, avg_fat))

    ranked.sort(key=lambda x: x[2], reverse=True)
    for name, severity, weight, avg_tot, avg_fat in ranked[:20]:
        print(f"{name:<25} {severity:<10} {weight:>7.2f} {avg_tot:>9} {avg_fat:>9}")

    print("=" * 70)
    print(f"Total stations with coordinates: {len(coordinates)}")
    print(f"Total stations in BTP data:      {len(station_data)}")
    print()


# ── Main ───────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Import BTP accident data into SafeMAPS"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse and print summary without writing to DB"
    )
    parser.add_argument(
        "--keep-existing", action="store_true",
        help="Append to existing blackspots instead of clearing first"
    )
    args = parser.parse_args()

    logger.info("Step 1 — Downloading BTP crash CSVs from OpenCity...")
    all_year_data = []
    for label, url in DATASETS.items():
        raw  = await download(url, f"btp_{label}.csv")
        data = parse_csv_2023_style(raw, label)
        if data:
            all_year_data.append(data)

    logger.info("Step 2 — Merging multi-year station data...")
    station_data = merge_station_data(all_year_data)

    logger.info("Step 3 — Downloading jurisdiction KML from OpenCity...")
    kml_raw      = await download(KML_URL, "btp_jurisdictions_2022.kml")
    kml_centroids = parse_kml_centroids(kml_raw)

    logger.info("Step 4 — Matching station names CSV ↔ KML...")
    matched_coords, unmatched = match_stations(station_data, kml_centroids)

    if unmatched:
        logger.info(f"Step 5 — Geocoding {len(unmatched)} unmatched stations via Nominatim...")
        geocoded = await geocode_all_unmatched(unmatched)
        matched_coords.update(geocoded)
    else:
        logger.info("Step 5 — All stations matched via KML, no Nominatim needed.")

    # Print summary regardless of dry-run
    print_summary(station_data, matched_coords)

    if args.dry_run:
        logger.info("Dry run — skipping DB import.")
        return

    logger.info("Step 6 — Importing into accident_blackspots table...")
    inserted = await import_to_postgis(
        station_data,
        matched_coords,
        clear_existing=not args.keep_existing,
    )

    logger.info(
        f"\nDone. {inserted} real BTP blackspots are now in SafeMAPS.\n"
        "The router will automatically use these on the next route request.\n"
        "Run /api/admin/refresh-graph to reload edge risk scores immediately."
    )


if __name__ == "__main__":
    asyncio.run(main())
