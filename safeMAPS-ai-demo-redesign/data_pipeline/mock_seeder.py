"""
SafeMAPS — Mock Data Seeder
Generates a mock road network (nodes, segments, grid AQI, and blackspots)
centered around Bangalore center [12.9716, 77.5946] so the routing engine
and dashboard function fully without requiring a 500MB OSM PBF download.
"""

import asyncio
import random
import logging
from pathlib import Path
import sys

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

async def seed_mock_data():
    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
    )
    logger.info("Connected to PostGIS database.")

    # 1. Clear existing network data
    logger.info("Clearing existing network tables...")
    await conn.execute("TRUNCATE road_segments, road_nodes, accident_blackspots, aqi_readings, aqi_stations CASCADE;")

    # Bangalore center
    center_lat = 12.9716
    center_lon = 77.5946

    # 2. Generate a grid of road nodes
    # Let's create a 15x15 grid of nodes spaced by 0.005 degrees (~500m)
    logger.info("Generating mock road nodes...")
    grid_size = 15
    spacing = 0.005
    nodes = []  # List of (osm_id, lon, lat)
    node_id_map = {}  # (col, row) -> db_id

    osm_id_counter = 1000001
    for r in range(grid_size):
        for c in range(grid_size):
            lon = center_lon + (c - grid_size // 2) * spacing
            lat = center_lat + (r - grid_size // 2) * spacing
            osm_id = osm_id_counter
            osm_id_counter += 1
            nodes.append((osm_id, lon, lat))

    # Insert nodes and get their database IDs
    node_db_ids = []
    for osm_id, lon, lat in nodes:
        db_id = await conn.fetchval(
            "INSERT INTO road_nodes (osm_id, geom) VALUES ($1, ST_SetSRID(ST_MakePoint($2, $3), 4326)) RETURNING id;",
            osm_id, lon, lat
        )
        node_db_ids.append(db_id)

    # Map grid coordinates to db_id
    idx = 0
    for r in range(grid_size):
        for c in range(grid_size):
            node_id_map[(c, r)] = node_db_ids[idx]
            idx += 1

    logger.info(f"Inserted {len(node_db_ids)} mock road nodes.")

    # 3. Generate road segments connecting the grid nodes
    # We will connect each node to its horizontal, vertical, and diagonal neighbors.
    logger.info("Generating mock road segments...")
    segments_count = 0
    road_types = ["primary", "secondary", "tertiary", "residential"]
    road_names = [
        "MG Road", "Brigade Road", "Residency Road", "Richmond Road",
        "Lavelle Road", "Kasturba Road", "St Marks Road", "Cunningham Road",
        "Infantry Road", "Commercial Street", "Indiranagar 100 Feet Rd",
        "Halasuru Road", "Victoria Road", "Museum Road", "Queens Road"
    ]

    for r in range(grid_size):
        for c in range(grid_size):
            current_id = node_id_map[(c, r)]
            current_lon = center_lon + (c - grid_size // 2) * spacing
            current_lat = center_lat + (r - grid_size // 2) * spacing

            # Horizontal neighbor (c + 1, r)
            if c + 1 < grid_size:
                target_id = node_id_map[(c + 1, r)]
                target_lon = center_lon + ((c + 1) - grid_size // 2) * spacing
                target_lat = current_lat
                length = 500.0  # approximate meters
                speed = random.choice([30, 40, 50])
                name = random.choice(road_names)
                rtype = random.choice(road_types)
                # Forward segment
                await conn.execute(
                    "INSERT INTO road_segments (osm_id, source_node, target_node, road_name, road_type, length_m, speed_kmh, oneway, geom) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, ST_SetSRID(ST_MakeLine(ST_MakePoint($8, $9), ST_MakePoint($10, $11)), 4326));",
                    random.randint(2000000, 9000000), current_id, target_id, name, rtype, length, speed,
                    current_lon, current_lat, target_lon, target_lat
                )
                # Reverse segment (bidirectional)
                await conn.execute(
                    "INSERT INTO road_segments (osm_id, source_node, target_node, road_name, road_type, length_m, speed_kmh, oneway, geom) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, ST_SetSRID(ST_MakeLine(ST_MakePoint($8, $9), ST_MakePoint($10, $11)), 4326));",
                    random.randint(2000000, 9000000), target_id, current_id, name, rtype, length, speed,
                    target_lon, target_lat, current_lon, current_lat
                )
                segments_count += 2

            # Vertical neighbor (c, r + 1)
            if r + 1 < grid_size:
                target_id = node_id_map[(c, r + 1)]
                target_lon = current_lon
                target_lat = center_lat + ((r + 1) - grid_size // 2) * spacing
                length = 500.0
                speed = random.choice([30, 40, 50])
                name = random.choice(road_names)
                rtype = random.choice(road_types)
                # Forward segment
                await conn.execute(
                    "INSERT INTO road_segments (osm_id, source_node, target_node, road_name, road_type, length_m, speed_kmh, oneway, geom) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, ST_SetSRID(ST_MakeLine(ST_MakePoint($8, $9), ST_MakePoint($10, $11)), 4326));",
                    random.randint(2000000, 9000000), current_id, target_id, name, rtype, length, speed,
                    current_lon, current_lat, target_lon, target_lat
                )
                # Reverse segment
                await conn.execute(
                    "INSERT INTO road_segments (osm_id, source_node, target_node, road_name, road_type, length_m, speed_kmh, oneway, geom) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, ST_SetSRID(ST_MakeLine(ST_MakePoint($8, $9), ST_MakePoint($10, $11)), 4326));",
                    random.randint(2000000, 9000000), target_id, current_id, name, rtype, length, speed,
                    target_lon, target_lat, current_lon, current_lat
                )
                segments_count += 2

            # Diagonal neighbor (c + 1, r + 1)
            if c + 1 < grid_size and r + 1 < grid_size:
                target_id = node_id_map[(c + 1, r + 1)]
                target_lon = center_lon + ((c + 1) - grid_size // 2) * spacing
                target_lat = center_lat + ((r + 1) - grid_size // 2) * spacing
                length = 707.0  # hypotenuse
                speed = random.choice([30, 40])
                name = f"{random.choice(road_names)} Diagonal"
                rtype = random.choice(road_types)
                # Forward segment
                await conn.execute(
                    "INSERT INTO road_segments (osm_id, source_node, target_node, road_name, road_type, length_m, speed_kmh, oneway, geom) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, ST_SetSRID(ST_MakeLine(ST_MakePoint($8, $9), ST_MakePoint($10, $11)), 4326));",
                    random.randint(2000000, 9000000), current_id, target_id, name, rtype, length, speed,
                    current_lon, current_lat, target_lon, target_lat
                )
                # Reverse segment
                await conn.execute(
                    "INSERT INTO road_segments (osm_id, source_node, target_node, road_name, road_type, length_m, speed_kmh, oneway, geom) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, FALSE, ST_SetSRID(ST_MakeLine(ST_MakePoint($8, $9), ST_MakePoint($10, $11)), 4326));",
                    random.randint(2000000, 9000000), target_id, current_id, name, rtype, length, speed,
                    target_lon, target_lat, current_lon, current_lat
                )
                segments_count += 2

    logger.info(f"Inserted {segments_count} mock road segments.")

    # 4. Insert some mock accident blackspots
    logger.info("Generating mock accident blackspots...")
    blackspots = [
        ("high", 4.0, 15, 3, "MG Road - Brigade Road Intersection", center_lon + 0.002, center_lat - 0.001),
        ("critical", 8.0, 34, 11, "Richmond Road Flyover Approach", center_lon - 0.01, center_lat + 0.008),
        ("moderate", 2.5, 8, 1, "Lavelle Road Narrow Corner", center_lon + 0.008, center_lat + 0.012),
        ("low", 1.2, 4, 0, "Museum Road Crossing", center_lon - 0.005, center_lat - 0.008),
    ]

    for severity, weight, total, fatal, desc, lon, lat in blackspots:
        # Find nearest edge
        edge_id = await conn.fetchval(
            "SELECT id FROM road_segments ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1, $2), 4326) LIMIT 1;",
            lon, lat
        )
        await conn.execute(
            "INSERT INTO accident_blackspots (severity, severity_weight, total_accidents, fatal_accidents, description, nearest_edge_id, geom) "
            "VALUES ($1, $2, $3, $4, $5, $6, ST_SetSRID(ST_MakePoint($7, $8), 4326));",
            severity, weight, total, fatal, desc, edge_id, lon, lat
        )
    logger.info("Inserted mock accident blackspots.")

    # 5. Insert mock AQI stations & readings
    logger.info("Generating mock AQI stations...")
    stations = [
        ("station_1", "MG Road Central", center_lon, center_lat),
        ("station_2", "Richmond Town Park", center_lon - 0.015, center_lat - 0.005),
        ("station_3", "Lavelle Road Residential", center_lon + 0.015, center_lat + 0.01),
    ]

    for uid, name, lon, lat in stations:
        station_id = await conn.fetchval(
            "INSERT INTO aqi_stations (station_uid, name, geom) VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326)) RETURNING id;",
            uid, name, lon, lat
        )
        # Seed reading
        await conn.execute(
            "INSERT INTO aqi_readings (station_id, aqi, pm25, pm10, no2, co, o3) VALUES ($1, $2, $3, $4, $5, $6, $7);",
            station_id, random.choice([45.0, 72.0, 110.0, 155.0]), 25.0, 40.0, 12.0, 0.8, 18.0
        )
    logger.info("Inserted mock AQI stations & readings.")

    # 6. Populate grid cell AQI values to create heatmaps
    logger.info("Seeding grid cell AQI values...")
    await conn.execute(
        "UPDATE grid_cells SET aqi_value = $1, aqi_updated = NOW() WHERE (row_idx + col_idx) % 3 = 0;",
        45.0
    )
    await conn.execute(
        "UPDATE grid_cells SET aqi_value = $1, aqi_updated = NOW() WHERE (row_idx + col_idx) % 3 = 1;",
        82.0
    )
    await conn.execute(
        "UPDATE grid_cells SET aqi_value = $1, aqi_updated = NOW() WHERE (row_idx + col_idx) % 3 = 2;",
        145.0
    )
    logger.info("Grid cells seeded successfully.")

    await conn.close()
    logger.info("Database seeding complete!")

if __name__ == "__main__":
    asyncio.run(seed_mock_data())
