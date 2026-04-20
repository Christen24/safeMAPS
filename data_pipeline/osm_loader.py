"""
SafeMAPS — OSM Road Network Loader

Downloads the Karnataka OSM extract from Geofabrik and imports the
Bangalore road network into PostGIS.

Usage:
    # Full run (download + import):
    python osm_loader.py

    # Skip download if you already have the PBF:
    python osm_loader.py --skip-download

    # Use a pre-clipped Bangalore PBF:
    python osm_loader.py --pbf-path ./bangalore.osm.pbf

Dependencies:
    pip install osmium httpx asyncpg

Why osmium instead of pyrosm?
    pyrosm is convenient but internally uses pandas + geopandas which adds
    significant overhead and occasionally fails on large PBFs. osmium-tool
    (python bindings: osmium) is the reference C++ library — it's faster,
    uses less memory, and gives direct access to OSM primitives.

One-way handling:
    We read the OSM `oneway` tag and store it as a boolean in road_segments.
    The graph_cache then uses this flag when building the adjacency list —
    one-way roads get only a forward edge, not a reverse edge.
"""

import sys
import asyncio
import argparse
import logging
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

GEOFABRIK_URL = "https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf"
DEFAULT_PBF = Path(__file__).parent / "southern-zone-latest.osm.pbf"

# Bangalore bounding box
BBOX = (
    settings.bbox_min_lon, settings.bbox_min_lat,
    settings.bbox_max_lon, settings.bbox_max_lat,
)

# Road types we care about (OSM highway tag values)
ROAD_TYPES = {
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "unclassified", "residential",
    "living_street", "service",
    "road",
}

# Default speed limits by road type (km/h)
DEFAULT_SPEEDS: dict[str, float] = {
    "motorway": 80,      "motorway_link": 60,
    "trunk": 60,         "trunk_link": 50,
    "primary": 50,       "primary_link": 40,
    "secondary": 40,     "secondary_link": 35,
    "tertiary": 35,      "tertiary_link": 30,
    "unclassified": 25,  "residential": 25,
    "living_street": 15, "service": 15,
    "road": 30,
}

# Bulk insert batch size
BATCH_SIZE = 2000


# ── Download ──────────────────────────────────────────────────────────

async def download_pbf(url: str, dest: Path) -> None:
    if dest.exists():
        logger.info(f"PBF already exists at {dest}, skipping download.")
        return
    logger.info(f"Downloading {url} ...")
    headers = {"User-Agent": "SafeMAPS-Data-Pipeline/1.0"}
    async with httpx.AsyncClient(timeout=900, follow_redirects=True, headers=headers) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            done = 0
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=4 * 1024 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        logger.info(f"  {done/1024/1024:.0f} / {total/1024/1024:.0f} MB")
    logger.info("Download complete.")


# ── OSM Parsing ───────────────────────────────────────────────────────

def in_bbox(lon: float, lat: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = BBOX
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def parse_network(pbf_path: Path) -> tuple[dict, list]:
    """
    Parse the OSM PBF and extract nodes and ways for the Bangalore bbox.

    Returns:
        nodes_by_osm_id: {osm_node_id: (lon, lat)}
        ways:            list of way dicts with keys:
                           osm_id, name, highway, maxspeed, oneway, node_ids
    """
    try:
        import osmium
    except ImportError:
        logger.error(
            "osmium not installed.\n"
            "Install with: pip install osmium\n"
            "On Ubuntu you may also need: sudo apt install libosmium-dev"
        )
        sys.exit(1)

    logger.info(f"Parsing {pbf_path} (this takes 2–5 minutes for Karnataka)...")

    # ── Pass 1: collect all node IDs referenced by road ways ─────────
    class WayNodeCollector(osmium.SimpleHandler):
        def __init__(self):
            super().__init__()
            self.needed_node_ids: set[int] = set()
            self.raw_ways: list[dict] = []

        def way(self, w):
            tags = {t.k: t.v for t in w.tags}
            highway = tags.get("highway", "")
            if highway not in ROAD_TYPES:
                return
            node_ids = [n.ref for n in w.nodes]
            self.needed_node_ids.update(node_ids)
            self.raw_ways.append({
                "osm_id": w.id,
                "name": tags.get("name") or tags.get("name:en"),
                "highway": highway,
                "maxspeed": tags.get("maxspeed", ""),
                "oneway": tags.get("oneway", "no"),
                "node_ids": node_ids,
            })

    collector = WayNodeCollector()
    collector.apply_file(str(pbf_path), locations=False)
    logger.info(
        f"Pass 1: found {len(collector.raw_ways):,} road ways, "
        f"{len(collector.needed_node_ids):,} referenced nodes."
    )

    # ── Pass 2: collect coordinates for needed nodes ──────────────────
    class NodeCollector(osmium.SimpleHandler):
        def __init__(self, needed: set[int]):
            super().__init__()
            self.needed = needed
            self.coords: dict[int, tuple[float, float]] = {}  # osm_id → (lon, lat)

        def node(self, n):
            if n.id in self.needed:
                self.coords[n.id] = (n.location.lon, n.location.lat)

    node_collector = NodeCollector(collector.needed_node_ids)
    node_collector.apply_file(str(pbf_path), locations=True)
    logger.info(f"Pass 2: resolved {len(node_collector.coords):,} node coordinates.")

    # ── Filter nodes to Bangalore bbox ────────────────────────────────
    nodes_in_bbox: dict[int, tuple[float, float]] = {
        osm_id: (lon, lat)
        for osm_id, (lon, lat) in node_collector.coords.items()
        if in_bbox(lon, lat)
    }
    logger.info(f"Nodes inside Bangalore bbox: {len(nodes_in_bbox):,}")

    # Filter ways to only those with at least 2 nodes in bbox
    ways_in_bbox = [
        w for w in collector.raw_ways
        if sum(1 for nid in w["node_ids"] if nid in nodes_in_bbox) >= 2
    ]
    logger.info(f"Ways with ≥2 nodes in bbox: {len(ways_in_bbox):,}")

    return nodes_in_bbox, ways_in_bbox


# ── PostGIS Import ────────────────────────────────────────────────────

async def import_to_postgis(
    nodes_by_osm_id: dict[int, tuple[float, float]],
    ways: list[dict],
) -> None:
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        # ── Insert nodes ──────────────────────────────────────────────
        logger.info(f"Inserting {len(nodes_by_osm_id):,} nodes...")
        node_records = [
            (osm_id, lon, lat)
            for osm_id, (lon, lat) in nodes_by_osm_id.items()
        ]

        for i in range(0, len(node_records), BATCH_SIZE):
            batch = node_records[i:i + BATCH_SIZE]
            await conn.executemany("""
                INSERT INTO road_nodes (osm_id, geom)
                VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326))
                ON CONFLICT (osm_id) DO NOTHING;
            """, batch)
            logger.info(f"  Nodes: {min(i + BATCH_SIZE, len(node_records)):,} / {len(node_records):,}")

        # ── Build osm_id → internal id map ───────────────────────────
        rows = await conn.fetch("SELECT id, osm_id FROM road_nodes;")
        osm_to_id: dict[int, int] = {r["osm_id"]: r["id"] for r in rows}
        logger.info(f"OSM→internal ID map: {len(osm_to_id):,} entries.")

        # ── Build edge records from ways ──────────────────────────────
        logger.info("Building edge records from ways...")
        edge_records = []

        for way in ways:
            highway = way["highway"]
            oneway_tag = way["oneway"].lower().strip()
            is_oneway = oneway_tag in ("yes", "true", "1", "-1")
            # "-1" means one-way in reverse — we handle this by swapping nodes
            reverse_oneway = oneway_tag == "-1"

            # Parse speed
            raw_speed = way["maxspeed"].replace(" mph", "").replace(" kph", "").strip()
            try:
                speed_kmh = float(raw_speed)
                if "mph" in way["maxspeed"]:
                    speed_kmh *= 1.609
            except ValueError:
                speed_kmh = DEFAULT_SPEEDS.get(highway, 30.0)

            # Walk node_ids in pairs to produce edge segments
            node_ids = way["node_ids"]
            for j in range(len(node_ids) - 1):
                u_osm = node_ids[j]
                v_osm = node_ids[j + 1]

                # Skip if either node is outside bbox or wasn't inserted
                u_id = osm_to_id.get(u_osm)
                v_id = osm_to_id.get(v_osm)
                if u_id is None or v_id is None:
                    continue

                u_coords = nodes_by_osm_id.get(u_osm)
                v_coords = nodes_by_osm_id.get(v_osm)
                if u_coords is None or v_coords is None:
                    continue

                u_lon, u_lat = u_coords
                v_lon, v_lat = v_coords

                # Haversine length
                import math
                R = 6_371_000
                phi1, phi2 = math.radians(u_lat), math.radians(v_lat)
                dphi = math.radians(v_lat - u_lat)
                dlam = math.radians(v_lon - u_lon)
                a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlam/2)**2
                length_m = 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

                # WKT for the 2-point LineString
                wkt = f"LINESTRING({u_lon} {u_lat}, {v_lon} {v_lat})"

                if reverse_oneway:
                    # Road goes v → u only
                    src_id, tgt_id = v_id, u_id
                else:
                    src_id, tgt_id = u_id, v_id

                edge_records.append((
                    int(way["osm_id"]),
                    src_id,
                    tgt_id,
                    way["name"],
                    highway,
                    length_m,
                    speed_kmh,
                    is_oneway,
                    wkt,
                ))

        logger.info(f"Built {len(edge_records):,} edge records. Inserting...")

        for i in range(0, len(edge_records), BATCH_SIZE):
            batch = edge_records[i:i + BATCH_SIZE]
            await conn.executemany("""
                INSERT INTO road_segments
                    (osm_id, source_node, target_node, road_name, road_type,
                     length_m, speed_kmh, oneway, geom)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                        ST_SetSRID(ST_GeomFromText($9), 4326))
                ON CONFLICT DO NOTHING;
            """, batch)
            logger.info(f"  Edges: {min(i + BATCH_SIZE, len(edge_records)):,} / {len(edge_records):,}")

        # ── Final counts ──────────────────────────────────────────────
        n_nodes = await conn.fetchval("SELECT COUNT(*) FROM road_nodes;")
        n_edges = await conn.fetchval("SELECT COUNT(*) FROM road_segments;")
        n_oneway = await conn.fetchval(
            "SELECT COUNT(*) FROM road_segments WHERE oneway = TRUE;"
        )
        logger.info(
            f"Import complete: {n_nodes:,} nodes, {n_edges:,} edges "
            f"({n_oneway:,} one-way)."
        )

    finally:
        await conn.close()


# ── Entry Point ───────────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="Load Bangalore OSM data into PostGIS")
    parser.add_argument("--pbf-path", default=str(DEFAULT_PBF))
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()

    pbf = Path(args.pbf_path)

    if not args.skip_download:
        await download_pbf(GEOFABRIK_URL, pbf)

    if not pbf.exists():
        logger.error(f"PBF not found at {pbf}. Run without --skip-download.")
        sys.exit(1)

    nodes, ways = parse_network(pbf)
    await import_to_postgis(nodes, ways)
    logger.info("Done. Next: run blackspot_mapper.py, then aqi_scraper.py --once")


if __name__ == "__main__":
    asyncio.run(main())
