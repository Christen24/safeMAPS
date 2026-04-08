-- ============================================================
-- SafeMAPS — PostGIS Database Schema
-- Health & Safety Aware Routing for Bangalore
-- ============================================================

-- Ensure extensions are available
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgrouting;

-- ============================================================
-- 1. Road Network (from OpenStreetMap)
-- ============================================================

CREATE TABLE IF NOT EXISTS road_nodes (
    id          BIGSERIAL PRIMARY KEY,
    osm_id      BIGINT UNIQUE,
    geom        GEOMETRY(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_road_nodes_geom
    ON road_nodes USING GIST (geom);

CREATE TABLE IF NOT EXISTS road_segments (
    id          BIGSERIAL PRIMARY KEY,
    osm_id      BIGINT,
    source_node BIGINT REFERENCES road_nodes(id),
    target_node BIGINT REFERENCES road_nodes(id),
    road_name   TEXT,
    road_type   TEXT,            -- highway, residential, primary, etc.
    length_m    DOUBLE PRECISION NOT NULL DEFAULT 0,
    speed_kmh   DOUBLE PRECISION NOT NULL DEFAULT 30,
    oneway      BOOLEAN DEFAULT FALSE,
    geom        GEOMETRY(LineString, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_road_segments_geom
    ON road_segments USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_road_segments_source
    ON road_segments (source_node);
CREATE INDEX IF NOT EXISTS idx_road_segments_target
    ON road_segments (target_node);

-- ============================================================
-- 2. Grid Cells (100m × 100m for AQI/Risk heatmap)
-- ============================================================

CREATE TABLE IF NOT EXISTS grid_cells (
    id          BIGSERIAL PRIMARY KEY,
    row_idx     INT NOT NULL,
    col_idx     INT NOT NULL,
    aqi_value   DOUBLE PRECISION,       -- Latest interpolated AQI
    aqi_updated TIMESTAMPTZ,
    geom        GEOMETRY(Polygon, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_grid_cells_geom
    ON grid_cells USING GIST (geom);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_cells_rowcol
    ON grid_cells (row_idx, col_idx);

-- ============================================================
-- 3. AQI Readings (from WAQI / CPCB stations)
-- ============================================================

CREATE TABLE IF NOT EXISTS aqi_stations (
    id          SERIAL PRIMARY KEY,
    station_uid TEXT UNIQUE NOT NULL,
    name        TEXT,
    geom        GEOMETRY(Point, 4326) NOT NULL
);

CREATE TABLE IF NOT EXISTS aqi_readings (
    id          BIGSERIAL PRIMARY KEY,
    station_id  INT REFERENCES aqi_stations(id),
    aqi         DOUBLE PRECISION NOT NULL,
    pm25        DOUBLE PRECISION,
    pm10        DOUBLE PRECISION,
    no2         DOUBLE PRECISION,
    co          DOUBLE PRECISION,
    o3          DOUBLE PRECISION,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_aqi_readings_station
    ON aqi_readings (station_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_aqi_stations_geom
    ON aqi_stations USING GIST (geom);

-- ============================================================
-- 4. Accident Blackspots
-- ============================================================

CREATE TABLE IF NOT EXISTS accident_blackspots (
    id              BIGSERIAL PRIMARY KEY,
    severity        TEXT NOT NULL DEFAULT 'moderate',
        -- 'low', 'moderate', 'high', 'critical'
    severity_weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    total_accidents INT NOT NULL DEFAULT 0,
    fatal_accidents INT NOT NULL DEFAULT 0,
    description     TEXT,
    nearest_edge_id BIGINT REFERENCES road_segments(id),
    geom            GEOMETRY(Point, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_blackspots_geom
    ON accident_blackspots USING GIST (geom);

-- ============================================================
-- 5. Traffic Snapshots
-- ============================================================

CREATE TABLE IF NOT EXISTS traffic_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    edge_id         BIGINT REFERENCES road_segments(id),
    current_speed   DOUBLE PRECISION,   -- km/h
    free_flow_speed DOUBLE PRECISION,   -- km/h
    congestion      DOUBLE PRECISION,   -- ratio: 1 - (current/freeflow)
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_traffic_edge
    ON traffic_snapshots (edge_id, recorded_at DESC);

-- ============================================================
-- 6. Generate Bangalore Grid (100m × 100m)
-- ============================================================
-- Bangalore bounding box: ~12.85°N to ~13.15°N, ~77.45°E to ~77.78°E
-- At this latitude, 1° lat ≈ 111 km, 1° lon ≈ 102 km
-- 100m ≈ 0.0009° lat, 0.00098° lon

DO $$
DECLARE
    min_lat CONSTANT DOUBLE PRECISION := 12.85;
    max_lat CONSTANT DOUBLE PRECISION := 13.15;
    min_lon CONSTANT DOUBLE PRECISION := 77.45;
    max_lon CONSTANT DOUBLE PRECISION := 77.78;
    step_lat CONSTANT DOUBLE PRECISION := 0.0009;
    step_lon CONSTANT DOUBLE PRECISION := 0.00098;
    r INT := 0;
    c INT;
    lat DOUBLE PRECISION;
    lon DOUBLE PRECISION;
BEGIN
    -- Only generate if table is empty
    IF EXISTS (SELECT 1 FROM grid_cells LIMIT 1) THEN
        RAISE NOTICE 'Grid cells already exist, skipping generation.';
        RETURN;
    END IF;

    lat := min_lat;
    WHILE lat < max_lat LOOP
        c := 0;
        lon := min_lon;
        WHILE lon < max_lon LOOP
            INSERT INTO grid_cells (row_idx, col_idx, geom)
            VALUES (
                r, c,
                ST_SetSRID(ST_MakeEnvelope(
                    lon, lat,
                    lon + step_lon, lat + step_lat
                ), 4326)
            );
            lon := lon + step_lon;
            c := c + 1;
        END LOOP;
        lat := lat + step_lat;
        r := r + 1;
    END LOOP;

    RAISE NOTICE 'Generated % grid rows.', r;
END $$;

-- ============================================================
-- 7. Utility Views
-- ============================================================

CREATE OR REPLACE VIEW v_edge_costs AS
SELECT
    e.id AS edge_id,
    e.road_name,
    e.length_m,
    e.speed_kmh,
    e.length_m / GREATEST(e.speed_kmh / 3.6, 0.5) AS travel_time_s,
    COALESCE(g.aqi_value, 50.0) AS aqi_value,
    COALESCE(
        (SELECT SUM(b.severity_weight)
         FROM accident_blackspots b
         WHERE ST_DWithin(e.geom::geography, b.geom::geography, 200)),
        0.0
    ) AS risk_score
FROM road_segments e
LEFT JOIN grid_cells g ON ST_Intersects(e.geom, g.geom);
