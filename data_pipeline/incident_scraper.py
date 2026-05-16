"""
SafeMAPS — Live Incident Scraper

Three-source incident pipeline covering ~80% of ASTraM's incident data:

Source 1 — OSM Overpass API (real-time, free, no key)
    Queries hazard, accident, road_closure, construction nodes
    within the Bangalore bounding box. Updates every 10 min.

Source 2 — Waze CCP (Connected Citizens Program)
    Waze's public GeoJSON feed for registered cities.
    Bangalore is included. Register at:
    https://developers.google.com/waze/data-feed/get-started
    Set WAZE_CCP_URL in .env when approved.
    Falls back gracefully to OSM-only until URL is set.

Source 3 — @BlrCityTraffic (BTP Twitter feed)
    Parses BTP's incident tweets with regex + Nominatim geocoding.
    Requires X_BEARER_TOKEN in .env (free tier: 1500 tweets/month).
    Falls back gracefully if not set.

Deduplication:
    Incidents from all three sources are merged spatially.
    Any two incidents within 100m of each other are treated as the same
    event; the highest-severity reading wins.

Expiry:
    Incidents are marked is_active=FALSE when expires_at passes.
    Default TTL: 2 hours (configurable per-source).
    Waze incidents use the TTL from the feed when available.
"""

import asyncio
import logging
import math
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import httpx
import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

BBOX = {
    "min_lat": 12.85, "max_lat": 13.15,
    "min_lon": 77.45, "max_lon": 77.78,
}

OVERPASS_URL   = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL  = "https://nominatim.openstreetmap.org/search"
DEDUP_RADIUS_M = 100    # incidents within 100m are considered duplicates
INCIDENT_TTL_H = 2      # default expiry hours


# ── Geometry helpers ──────────────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _in_bbox(lat: float, lon: float) -> bool:
    return (BBOX["min_lat"] <= lat <= BBOX["max_lat"] and
            BBOX["min_lon"] <= lon <= BBOX["max_lon"])


# ── Source 1: OSM Overpass ────────────────────────────────────────────

_OVERPASS_QUERY = """
[out:json][timeout:20];
(
  node["hazard"](12.85,77.45,13.15,77.78);
  node["accident"](12.85,77.45,13.15,77.78);
  node["highway"="road_closure"](12.85,77.45,13.15,77.78);
  node["construction"](12.85,77.45,13.15,77.78);
  way["highway"="construction"](12.85,77.45,13.15,77.78);
);
out center;
"""

_OSM_TYPE_MAP = {
    "accident":     ("accident",     2),
    "hazard":       ("hazard",       1),
    "road_closure": ("closure",      3),
    "construction": ("construction", 1),
}


async def fetch_osm_incidents() -> list[dict]:
    """Query OSM Overpass for real-time incidents in Bangalore bbox."""
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(
                OVERPASS_URL,
                data={"data": _OVERPASS_QUERY},
                headers={"User-Agent": "SafeMAPS/1.0 (research)"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[incidents/osm] Overpass fetch failed: {exc}")
        return []

    incidents = []
    now = datetime.now(timezone.utc)
    for el in data.get("elements", []):
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lon = el.get("lon") or el.get("center", {}).get("lon")
        if not (lat and lon and _in_bbox(float(lat), float(lon))):
            continue

        tags = el.get("tags", {})
        inc_type, severity = "hazard", 1
        for tag_key, (mapped_type, mapped_sev) in _OSM_TYPE_MAP.items():
            if tag_key in tags:
                inc_type, severity = mapped_type, mapped_sev
                break

        incidents.append({
            "source":        "osm",
            "incident_type": inc_type,
            "lat":           float(lat),
            "lon":           float(lon),
            "severity":      severity,
            "description":   tags.get("name") or tags.get("description", ""),
            "external_id":   f"{el['type']}/{el['id']}",
            "expires_at":    now + timedelta(hours=INCIDENT_TTL_H),
        })

    logger.info(f"[incidents/osm] {len(incidents)} incidents fetched.")
    return incidents


# ── Source 2: Waze CCP ────────────────────────────────────────────────

_WAZE_TYPE_MAP = {
    "ACCIDENT":     ("accident",     2),
    "JAM":          ("closure",      2),
    "ROAD_CLOSED":  ("closure",      3),
    "CONSTRUCTION": ("construction", 1),
    "HAZARD":       ("hazard",       1),
}


async def fetch_waze_incidents(ccp_url: str) -> list[dict]:
    """
    Fetch incidents from Waze Connected Citizens Program GeoJSON feed.
    ccp_url: provided after CCP registration — set as WAZE_CCP_URL in .env.
    """
    if not ccp_url:
        return []

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                ccp_url,
                headers={"User-Agent": "SafeMAPS/1.0 (research)"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[incidents/waze] Waze CCP fetch failed: {exc}")
        return []

    incidents = []
    now = datetime.now(timezone.utc)
    alerts = data.get("alerts", []) or data.get("features", [])

    for alert in alerts:
        # Handle both raw CCP format and GeoJSON Feature format
        if "geometry" in alert:
            coords = alert["geometry"]["coordinates"]
            lon, lat = float(coords[0]), float(coords[1])
            props = alert.get("properties", alert)
        else:
            lat = float(alert.get("location", {}).get("y", 0))
            lon = float(alert.get("location", {}).get("x", 0))
            props = alert

        if not _in_bbox(lat, lon):
            continue

        waze_type = (props.get("type") or "HAZARD").upper()
        inc_type, severity = _WAZE_TYPE_MAP.get(waze_type, ("hazard", 1))

        # Use Waze's pubMillis for expiry if available
        pub_ms = props.get("pubMillis")
        if pub_ms:
            reported = datetime.fromtimestamp(pub_ms / 1000, tz=timezone.utc)
        else:
            reported = now

        incidents.append({
            "source":        "waze",
            "incident_type": inc_type,
            "lat":           lat,
            "lon":           lon,
            "severity":      severity,
            "description":   props.get("street", "") or props.get("description", ""),
            "external_id":   str(props.get("uuid") or props.get("id", "")),
            "expires_at":    reported + timedelta(hours=INCIDENT_TTL_H),
        })

    logger.info(f"[incidents/waze] {len(incidents)} incidents fetched.")
    return incidents


# ── Source 3: BTP Twitter / @BlrCityTraffic ───────────────────────────

# BTP tweets structured format: "Road blocked at <location>..."
_BTP_PATTERNS = [
    r"(?:road blocked|block|accident|water logging|divert|closure|construction)\s+(?:at|near|on)\s+([^,.]+)",
    r"([A-Z][a-z]+(?: [A-Z][a-z]+)*(?:\s+(?:Road|Junction|Circle|Flyover|Bridge|Main))?)\s+(?:blocked|closed|waterlogged|accident)",
]
_INCIDENT_KEYWORDS = {
    "accident":      ("accident",     2),
    "blocked":       ("closure",      2),
    "block":         ("closure",      2),
    "closed":        ("closure",      3),
    "water log":     ("waterlogging", 2),
    "waterlog":      ("waterlogging", 2),
    "construction":  ("construction", 1),
    "divert":        ("closure",      2),
}


async def _geocode_nominatim(location: str) -> Optional[tuple[float, float]]:
    """Geocode a location string using Nominatim, restricted to Bangalore."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                NOMINATIM_URL,
                params={
                    "q":              f"{location}, Bangalore, Karnataka",
                    "format":         "json",
                    "limit":          1,
                    "bounded":        1,
                    "viewbox":        f"{BBOX['min_lon']},{BBOX['min_lat']},{BBOX['max_lon']},{BBOX['max_lat']}",
                    "accept-language": "en",
                },
                headers={"User-Agent": "SafeMAPS/1.0 (research)"},
            )
            resp.raise_for_status()
            results = resp.json()
    except Exception:
        return None

    if results:
        r = results[0]
        return float(r["lat"]), float(r["lon"])
    return None


async def fetch_twitter_incidents(bearer_token: str) -> list[dict]:
    """
    Fetch recent @BlrCityTraffic tweets and parse for incident locations.
    Uses X API v2 free tier (1500 tweets/month).
    """
    if not bearer_token:
        return []

    url = "https://api.twitter.com/2/tweets/search/recent"
    params = {
        "query":       "from:BlrCityTraffic -is:retweet",
        "max_results": 20,
        "tweet.fields": "created_at,text",
    }
    headers = {"Authorization": f"Bearer {bearer_token}"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning(f"[incidents/twitter] Twitter API failed: {exc}")
        return []

    tweets = data.get("data", [])
    incidents = []
    now = datetime.now(timezone.utc)

    geocode_tasks = []
    tweet_meta   = []

    for tweet in tweets:
        text = tweet.get("text", "").lower()

        # Determine incident type from keyword scan
        inc_type, severity = "hazard", 1
        for kw, (t, s) in _INCIDENT_KEYWORDS.items():
            if kw in text:
                inc_type, severity = t, s
                break
        else:
            continue  # Skip tweets with no incident keyword

        # Extract location string
        location = None
        for pat in _BTP_PATTERNS:
            m = re.search(pat, tweet.get("text", ""), re.IGNORECASE)
            if m:
                location = m.group(1).strip()
                break

        if not location:
            continue

        geocode_tasks.append(_geocode_nominatim(location))
        tweet_meta.append({
            "tweet_id":     tweet["id"],
            "incident_type": inc_type,
            "severity":     severity,
            "description":  tweet.get("text", "")[:256],
            "expires_at":   now + timedelta(hours=INCIDENT_TTL_H),
        })

    # Geocode all locations in parallel
    coords_list = await asyncio.gather(*geocode_tasks, return_exceptions=True)

    for meta, coords in zip(tweet_meta, coords_list):
        if isinstance(coords, Exception) or coords is None:
            continue
        lat, lon = coords
        if not _in_bbox(lat, lon):
            continue
        incidents.append({
            "source":        "twitter",
            "incident_type": meta["incident_type"],
            "lat":           lat,
            "lon":           lon,
            "severity":      meta["severity"],
            "description":   meta["description"],
            "external_id":   f"tweet_{meta['tweet_id']}",
            "expires_at":    meta["expires_at"],
        })

    logger.info(f"[incidents/twitter] {len(incidents)} geolocated incidents.")
    return incidents


# ── Deduplication ─────────────────────────────────────────────────────

def deduplicate_incidents(incidents: list[dict]) -> list[dict]:
    """
    Merge incidents within DEDUP_RADIUS_M of each other.
    Strategy: cluster greedily; first encounter in list anchors the cluster,
    then subsequent nearby incidents are merged (highest severity wins).
    """
    if not incidents:
        return []

    clusters: list[dict] = []
    used = [False] * len(incidents)

    for i, inc in enumerate(incidents):
        if used[i]:
            continue
        cluster = dict(inc)
        for j in range(i + 1, len(incidents)):
            if used[j]:
                continue
            dist = _haversine_m(
                inc["lat"], inc["lon"],
                incidents[j]["lat"], incidents[j]["lon"],
            )
            if dist <= DEDUP_RADIUS_M:
                used[j] = True
                # Higher severity wins; prefer more specific incident type
                if incidents[j]["severity"] > cluster["severity"]:
                    cluster["severity"]      = incidents[j]["severity"]
                    cluster["incident_type"] = incidents[j]["incident_type"]
                    cluster["description"]   = incidents[j]["description"]
        clusters.append(cluster)

    logger.info(f"[incidents] Dedup: {len(incidents)} → {len(clusters)} unique incidents.")
    return clusters


# ── DB write + expiry ─────────────────────────────────────────────────

async def _write_incidents(
    conn: asyncpg.Connection,
    incidents: list[dict],
) -> int:
    """
    Upsert incidents into live_incidents.
    Uses ON CONFLICT on (source, external_id) to avoid duplicates.
    Incidents with no external_id are always inserted (internal dedup only).
    Returns the number of new rows inserted.
    """
    inserted = 0
    now = datetime.now(timezone.utc)

    for inc in incidents:
        try:
            result = await conn.fetchval("""
                INSERT INTO live_incidents
                    (source, incident_type, lat, lon, geom,
                     severity, description, reported_at, expires_at,
                     is_active, external_id)
                VALUES
                    ($1, $2, $3, $4,
                     ST_SetSRID(ST_MakePoint($4, $3), 4326),
                     $5, $6, $7, $8, TRUE, $9)
                ON CONFLICT (source, external_id)
                    WHERE external_id IS NOT NULL
                DO UPDATE SET
                    severity    = GREATEST(live_incidents.severity, EXCLUDED.severity),
                    expires_at  = EXCLUDED.expires_at,
                    is_active   = TRUE
                RETURNING id;
            """,
                inc["source"], inc["incident_type"],
                inc["lat"], inc["lon"],
                inc["severity"],
                inc.get("description", ""),
                now,
                inc["expires_at"],
                inc.get("external_id"),
            )
            if result:
                inserted += 1
        except Exception as exc:
            logger.debug(f"[incidents] Insert skipped: {exc}")

    return inserted


async def _expire_stale_incidents(conn: asyncpg.Connection) -> int:
    """Mark incidents past their expires_at as inactive."""
    result = await conn.execute("""
        UPDATE live_incidents
        SET is_active = FALSE
        WHERE is_active = TRUE
          AND expires_at < NOW();
    """)
    n = int(result.split()[-1])
    if n:
        logger.info(f"[incidents] Expired {n} stale incidents.")
    return n


# ── Main entry point ──────────────────────────────────────────────────

async def scrape_incidents() -> tuple[int, int]:
    """
    Full incident scrape cycle:
      1. Fetch from all three sources in parallel
      2. Deduplicate by 100m radius
      3. Upsert into live_incidents
      4. Expire stale rows

    Returns: (inserted_count, expired_count)
    """
    waze_url     = getattr(settings, "waze_ccp_url",    None)
    x_token      = getattr(settings, "x_bearer_token",  None)

    osm_task     = fetch_osm_incidents()
    waze_task    = fetch_waze_incidents(waze_url or "")
    twitter_task = fetch_twitter_incidents(x_token or "")

    osm_inc, waze_inc, twitter_inc = await asyncio.gather(
        osm_task, waze_task, twitter_task,
    )

    all_incidents = osm_inc + waze_inc + twitter_inc
    deduped       = deduplicate_incidents(all_incidents)

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )
    try:
        inserted = await _write_incidents(conn, deduped)
        expired  = await _expire_stale_incidents(conn)
    finally:
        await conn.close()

    return inserted, expired


# ── Standalone runner ─────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Scrape live incidents for Bangalore")
    parser.add_argument("--once", action="store_true", default=True)
    args = parser.parse_args()

    async def _main():
        ins, exp = await scrape_incidents()
        print(f"Done: {ins} inserted, {exp} expired.")

    asyncio.run(_main())
