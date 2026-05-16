"""
SafeMAPS — BTP Accident Data Importer

Purpose:
    Ingest the granular historical accident coordinate data once received
    from the RTI (Right to Information) response from Bangalore City Police.

RTI Usage:
    python btp_accident_importer.py --file data/btp_accidents_2022_2024.csv
    python btp_accident_importer.py --file data/btp_accidents.xlsx --clear

Output:
    Inserts clustered blackspots into accident_blackspots.
    Severity = max(fatal*3 + grievous*2 + minor). Cluster radius = 50m.
"""

import asyncio
import logging
import math
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

BBOX = {"min_lat": 12.85, "max_lat": 13.15, "min_lon": 77.45, "max_lon": 77.78}
CLUSTER_RADIUS_M = 50


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2-lat1)/2)**2
         + math.cos(phi1)*math.cos(phi2)*math.sin(math.radians(lon2-lon1)/2)**2)
    return 2*R*math.atan2(math.sqrt(a), math.sqrt(1-a))


def _in_bbox(lat, lon):
    return (BBOX["min_lat"] <= lat <= BBOX["max_lat"] and
            BBOX["min_lon"] <= lon <= BBOX["max_lon"])


def _classify_severity(total, fatal):
    if fatal > 0 or total >= 5:
        return "severe",   min(10.0, fatal*3.0 + total*0.5)
    elif total >= 2:
        return "moderate", min(5.0, total*0.8)
    return "minor", 1.0


def _normalize_columns(df):
    col_lower = {c.lower().strip(): c for c in df.columns}
    def _find(candidates):
        for c in candidates:
            if c in col_lower:
                return col_lower[c]
        return None
    lat = _find(["latitude","lat","y","gps_lat"])
    lon = _find(["longitude","lon","lng","x","gps_lon"])
    if not lat or not lon:
        raise ValueError(f"Lat/lon columns not found. Available: {list(df.columns)}")
    return {
        "lat":      lat,
        "lon":      lon,
        "fatal":    _find(["fatal","fatalities","killed","deaths"]),
        "grievous": _find(["grievous","grievous_hurt","serious","severely_injured"]),
        "minor":    _find(["minor","minor_hurt","lightly_injured"]),
        "location": _find(["location","place","road","road_name","address"]),
    }


def cluster_accidents(accidents):
    clusters = []
    used = [False] * len(accidents)
    for i, acc in enumerate(accidents):
        if used[i]:
            continue
        pts = [acc]
        for j in range(i+1, len(accidents)):
            if not used[j] and _haversine_m(acc["lat"], acc["lon"],
                                             accidents[j]["lat"], accidents[j]["lon"]) <= CLUSTER_RADIUS_M:
                used[j] = True
                pts.append(accidents[j])
        lat   = sum(p["lat"] for p in pts) / len(pts)
        lon   = sum(p["lon"] for p in pts) / len(pts)
        total = len(pts)
        fatal = sum(p["fatal"] for p in pts)
        locs  = [p["location"] for p in pts if p["location"]]
        sev, wt = _classify_severity(total, fatal)
        clusters.append({
            "lat": lat, "lon": lon,
            "severity": sev, "severity_weight": round(wt, 2),
            "total_accidents": total, "fatal_accidents": fatal,
            "description": f"{locs[0] if locs else 'Unknown'} ({total} accidents, {fatal} fatal)",
        })
    return clusters


async def import_btp_accidents(filepath: str, clear_existing: bool = False) -> dict:
    import sys
    import pandas as pd
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from config import settings

    fp = Path(filepath)
    df = pd.read_excel(fp) if fp.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(fp)
    logger.info(f"Loaded {len(df)} rows from {fp.name}")

    cols = _normalize_columns(df)
    accidents = []
    for _, row in df.iterrows():
        try:
            lat, lon = float(row[cols["lat"]]), float(row[cols["lon"]])
        except (TypeError, ValueError):
            continue
        if not _in_bbox(lat, lon):
            continue
        def safe_int(c):
            try: return max(0, int(float(row[c]))) if c else 0
            except: return 0
        accidents.append({
            "lat": lat, "lon": lon,
            "fatal":    safe_int(cols.get("fatal")),
            "grievous": safe_int(cols.get("grievous")),
            "minor":    safe_int(cols.get("minor")),
            "location": str(row[cols["location"]]) if cols.get("location") else "",
        })

    logger.info(f"Valid Bangalore rows: {len(accidents)}")
    blackspots = cluster_accidents(accidents)
    logger.info(f"Clustered to {len(blackspots)} blackspots")

    conn = await asyncpg.connect(
        host=settings.postgres_host, port=settings.postgres_port,
        database=settings.postgres_db, user=settings.postgres_user,
        password=settings.postgres_password,
    )
    try:
        if clear_existing:
            await conn.execute("TRUNCATE TABLE accident_blackspots RESTART IDENTITY;")
        for b in blackspots:
            await conn.execute("""
                INSERT INTO accident_blackspots
                    (lat, lon, geom, severity, severity_weight,
                     total_accidents, fatal_accidents, description)
                VALUES ($1,$2,ST_SetSRID(ST_MakePoint($2,$1),4326),$3,$4,$5,$6,$7);
            """, b["lat"], b["lon"], b["severity"], b["severity_weight"],
                b["total_accidents"], b["fatal_accidents"], b["description"])
    finally:
        await conn.close()

    print(f"\nImport complete: {len(blackspots)} blackspots from {len(accidents)} accidents.")
    print("Run: curl -X POST /api/admin/refresh-graph -H 'X-Admin-Key: <key>'")
    return {"blackspots": len(blackspots), "raw_accidents": len(accidents)}


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    p = argparse.ArgumentParser(description="Import BTP RTI accident data")
    p.add_argument("--file",  required=True)
    p.add_argument("--clear", action="store_true")
    args = p.parse_args()
    asyncio.run(import_btp_accidents(args.file, args.clear))
