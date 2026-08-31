-- ============================================================
-- SafeMAPS — Live Incidents Schema Migration
-- Creates live_incidents table with PostGIS geometry,
-- spatial index, and automatic expiry support.
-- Run: psql $DATABASE_URL -f migration_incidents.sql
-- ============================================================

-- 1. Create live_incidents table
CREATE TABLE IF NOT EXISTS live_incidents (
    id            BIGSERIAL PRIMARY KEY,
    source        VARCHAR(16)  NOT NULL,  -- osm / waze / twitter
    incident_type VARCHAR(32)  NOT NULL,  -- accident / closure / waterlogging / construction / hazard
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    geom          GEOMETRY(Point, 4326) NOT NULL,
    severity      SMALLINT     NOT NULL DEFAULT 1 CHECK (severity BETWEEN 1 AND 3),
    description   TEXT,
    reported_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW() + INTERVAL '2 hours',
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    external_id   VARCHAR(128),           -- Waze/OSM node ID for dedup
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- 2. Spatial index — used by routing to find incidents within 200m of edges
CREATE INDEX IF NOT EXISTS idx_live_incidents_geom
    ON live_incidents USING GIST (geom)
    WHERE is_active = TRUE;

-- 3. Time-based index — used by expiry cleanup queries
CREATE INDEX IF NOT EXISTS idx_live_incidents_expires
    ON live_incidents (expires_at)
    WHERE is_active = TRUE;

-- 4. Dedup index — prevents inserting the same external incident twice
CREATE UNIQUE INDEX IF NOT EXISTS idx_live_incidents_external_id
    ON live_incidents (source, external_id)
    WHERE external_id IS NOT NULL;

-- 5. Source + type index — used by the /api/incidents/active filter
CREATE INDEX IF NOT EXISTS idx_live_incidents_source_type
    ON live_incidents (source, incident_type, is_active);

-- Comments
COMMENT ON TABLE live_incidents IS
    'Live road incidents from OSM Overpass, Waze CCP, and BTP Twitter feed.';
COMMENT ON COLUMN live_incidents.source IS
    'Data source: osm, waze, or twitter';
COMMENT ON COLUMN live_incidents.incident_type IS
    'Incident category: accident, closure, waterlogging, construction, hazard';
COMMENT ON COLUMN live_incidents.severity IS
    '1=low, 2=medium, 3=high — used as edge cost multiplier in routing';
COMMENT ON COLUMN live_incidents.expires_at IS
    'Auto-expiry: set to reported_at + 2h by default. '
    'Waze incidents may have shorter TTL from the feed.';
COMMENT ON COLUMN live_incidents.external_id IS
    'Original ID from source feed, used for upsert deduplication.';
