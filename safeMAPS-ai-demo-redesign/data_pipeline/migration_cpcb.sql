-- ============================================================
-- SafeMAPS — CPCB AQI Schema Migration
-- Adds source tracking, 24h averages, and extra pollutant
-- columns to aqi_history for CPCB integration.
-- Run: psql $DATABASE_URL -f migration_cpcb.sql
-- ============================================================

-- 1. Add source column (waqi / cpcb) to aqi_history
ALTER TABLE aqi_history
    ADD COLUMN IF NOT EXISTS source VARCHAR(10) DEFAULT 'waqi';

-- 2. Add CPCB-specific pollutant columns not in WAQI
ALTER TABLE aqi_history
    ADD COLUMN IF NOT EXISTS so2          DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS o3           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pm25_24h_avg DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS no2          DOUBLE PRECISION;

-- 3. Index for fast source-filtered LSTM training queries
CREATE INDEX IF NOT EXISTS idx_aqi_history_source
    ON aqi_history (source, recorded_at DESC);

-- 4. Index for per-station latest-reading queries (prefer CPCB)
CREATE INDEX IF NOT EXISTS idx_aqi_history_station_source
    ON aqi_history (station_id, source, recorded_at DESC);

-- 5. aqi_stations: add cpcb_station_id for cross-referencing
ALTER TABLE aqi_stations
    ADD COLUMN IF NOT EXISTS cpcb_station_id VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS idx_aqi_stations_cpcb_id
    ON aqi_stations (cpcb_station_id)
    WHERE cpcb_station_id IS NOT NULL;

-- 6. Comment the new columns
COMMENT ON COLUMN aqi_history.source          IS 'Data source: waqi or cpcb';
COMMENT ON COLUMN aqi_history.so2             IS 'SO2 concentration µg/m³ (CPCB)';
COMMENT ON COLUMN aqi_history.o3              IS 'O3 concentration µg/m³ (CPCB)';
COMMENT ON COLUMN aqi_history.pm25_24h_avg    IS '24-hour rolling PM2.5 average µg/m³ (CPCB)';
COMMENT ON COLUMN aqi_history.no2             IS 'NO2 concentration µg/m³';
COMMENT ON COLUMN aqi_stations.cpcb_station_id IS 'Station ID as used in CPCB/data.gov.in feed';
