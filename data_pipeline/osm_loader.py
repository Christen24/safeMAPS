"""
OSM Loader — Downloads and imports Bangalore road network into PostGIS.

Usage:
    python osm_loader.py [--pbf-path path/to/file.osm.pbf]

If no PBF path is given, downloads Karnataka extract from Geofabrik.
"""

import os
import sys
import asyncio
import argparse
import logging
from pathlib import Path

import httpx
import asyncpg
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Add parent dir to path for config
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

# Geofabrik Karnataka extract URL
GEOFABRIK_URL = "https://download.geofabrik.de/asia/india/karnataka-latest.osm.pbf"
DEFAULT_PBF = Path(__file__).parent / "karnataka-latest.osm.pbf"

# Bangalore bounding box for filtering
BBOX = (settings.bbox_min_lon, settings.bbox_min_lat,
        settings.bbox_max_lon, settings.bbox_max_lat)


async def download_pbf(url: str, dest: Path):
    """Download the OSM PBF file."""
    if dest.exists():
        logger.info(f"PBF file already exists at {dest}, skipping download.")
        return

    logger.info(f"Downloading OSM PBF from {url}...")
    async with httpx.AsyncClient(timeout=600) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = (downloaded / total) * 100
                        logger.info(f"  {pct:.1f}% ({downloaded // (1024*1024)} MB)")

    logger.info(f"Download complete: {dest}")


def parse_osm_network(pbf_path: Path):
    """
    Parse OSM PBF and extract road network for Bangalore.
    Returns (nodes_df, edges_df) using pyrosm.
    """
    try:
        from pyrosm import OSM
    except ImportError:
        logger.error("pyrosm not installed. Run: pip install pyrosm")
        sys.exit(1)

    logger.info(f"Parsing OSM network from {pbf_path}...")
    osm = OSM(str(pbf_path), bounding_box=BBOX)

    # Extract the driving network
    nodes, edges = osm.get_network(network_type="driving", nodes=True)

    logger.info(f"Extracted {len(nodes)} nodes and {len(edges)} edges.")
    return nodes, edges


async def load_into_postgis(nodes_gdf, edges_gdf):
    """Bulk-insert nodes and edges into PostGIS tables."""
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )

    try:
        # ── Insert Nodes ──────────────────────────────────────────────
        logger.info("Inserting road nodes...")
        node_count = 0

        for idx, row in nodes_gdf.iterrows():
            geom = row.geometry
            if geom is None:
                continue
            lon, lat = geom.x, geom.y
            osm_id = int(row.get("id", idx))

            await conn.execute("""
                INSERT INTO road_nodes (osm_id, geom)
                VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326))
                ON CONFLICT (osm_id) DO NOTHING;
            """, osm_id, lon, lat)
            node_count += 1

        logger.info(f"Inserted {node_count} nodes.")

        # ── Build OSM ID → internal ID mapping ───────────────────────
        rows = await conn.fetch("SELECT id, osm_id FROM road_nodes;")
        osm_to_id = {r["osm_id"]: r["id"] for r in rows}

        # ── Insert Edges ──────────────────────────────────────────────
        logger.info("Inserting road segments...")
        edge_count = 0

        for idx, row in edges_gdf.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue

            # Get source/target OSM IDs
            u = int(row.get("u", 0))
            v = int(row.get("v", 0))
            src_id = osm_to_id.get(u)
            tgt_id = osm_to_id.get(v)

            if src_id is None or tgt_id is None:
                continue

            # Extract attributes
            road_name = str(row.get("name", "")) or None
            road_type = str(row.get("highway", "")) or None
            length_m = float(row.get("length", geom.length * 111000))
            speed = row.get("maxspeed")
            if speed and str(speed).isdigit():
                speed_kmh = float(speed)
            else:
                # Default speeds by road type
                speed_defaults = {
                    "motorway": 80, "trunk": 60, "primary": 50,
                    "secondary": 40, "tertiary": 35, "residential": 25,
                    "unclassified": 25, "service": 15,
                }
                speed_kmh = speed_defaults.get(road_type, 30)

            oneway = str(row.get("oneway", "")).lower() in ("yes", "true", "1")

            # Convert geometry to WKT
            wkt = geom.wkt

            await conn.execute("""
                INSERT INTO road_segments
                    (osm_id, source_node, target_node, road_name, road_type,
                     length_m, speed_kmh, oneway, geom)
                VALUES
                    ($1, $2, $3, $4, $5, $6, $7, $8,
                     ST_SetSRID(ST_GeomFromText($9), 4326))
                ON CONFLICT DO NOTHING;
            """,
                int(row.get("id", idx)),
                src_id, tgt_id,
                road_name, road_type,
                length_m, speed_kmh, oneway,
                wkt,
            )
            edge_count += 1

        logger.info(f"Inserted {edge_count} edges.")

    finally:
        await conn.close()


async def main():
    parser = argparse.ArgumentParser(description="Load Bangalore OSM data into PostGIS")
    parser.add_argument("--pbf-path", type=str, default=str(DEFAULT_PBF),
                        help="Path to the .osm.pbf file")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip downloading PBF file")
    args = parser.parse_args()

    pbf_path = Path(args.pbf_path)

    # Step 1: Download PBF if needed
    if not args.skip_download:
        await download_pbf(GEOFABRIK_URL, pbf_path)

    if not pbf_path.exists():
        logger.error(f"PBF file not found at {pbf_path}")
        sys.exit(1)

    # Step 2: Parse OSM network
    nodes, edges = parse_osm_network(pbf_path)

    # Step 3: Load into PostGIS
    await load_into_postgis(nodes, edges)

    logger.info("OSM loading complete!")


if __name__ == "__main__":
    asyncio.run(main())
