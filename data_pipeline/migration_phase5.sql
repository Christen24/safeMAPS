-- ============================================================
-- SafeMAPS — Phase 5 Schema Migration
-- Predictive AQI Layer (LSTM)
--
-- Run after database_seeder.sql:
--   psql -h localhost -U healthroute -d healthroute -f migration_phase5.sql
--
-- Adds two tables:
--   aqi_history     — time-series of every AQI reading, enriched with
--                     temporal features for LSTM training
--   aqi_predictions — model output cache; refreshed every 30 min by
--                     the scheduler so /api/aqi/predict serves instantly
-- ============================================================

-- ── 1. aqi_history ────────────────────────────────────────────────────
-- One row per station per scrape cycle (~15 min cadence).
-- Temporal features (hour_of_day, day_of_week, is_weekend) are
-- denormalised here at insert time so the LSTM trainer never needs
-- to recompute them during training.

CREATE TABLE IF NOT EXISTS aqi_history (
    id              BIGSERIAL PRIMARY KEY,

    -- Station identity (mirrors aqi_stations.station_uid)
    station_id      TEXT        NOT NULL,
    station_name    TEXT,
    lat             DOUBLE PRECISION NOT NULL,
    lon             DOUBLE PRECISION NOT NULL,

    -- Air quality measurements
    aqi             DOUBLE PRECISION NOT NULL,
    pm25            DOUBLE PRECISION,           -- µg/m³
    pm10            DOUBLE PRECISION,
    no2             DOUBLE PRECISION,
    wind_speed      DOUBLE PRECISION,           -- m/s  (from weather API if available)
    temperature     DOUBLE PRECISION,           -- °C

    -- Temporal features — pre-computed at insert time
    hour_of_day     SMALLINT    NOT NULL CHECK (hour_of_day BETWEEN 0 AND 23),
    day_of_week     SMALLINT    NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    -- 0=Monday … 6=Sunday (Python weekday() convention)
    is_weekend      BOOLEAN     NOT NULL,

    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Primary lookup: model training reads per-station ordered by time
CREATE INDEX IF NOT EXISTS idx_aqi_history_station_time
    ON aqi_history (station_id, recorded_at DESC);

-- Allows fast "last 24 readings" queries for inference
CREATE INDEX IF NOT EXISTS idx_aqi_history_recent
    ON aqi_history (recorded_at DESC);


-- ── 2. aqi_predictions ────────────────────────────────────────────────
-- Model output cache. The scheduler writes here every 30 min.
-- The /api/aqi/predict endpoint reads from this table so inference
-- never happens in the request path (latency stays <10ms).

CREATE TABLE IF NOT EXISTS aqi_predictions (
    id              BIGSERIAL PRIMARY KEY,

    station_id      TEXT        NOT NULL,
    station_name    TEXT,
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,

    -- Forecast values
    predicted_aqi   DOUBLE PRECISION NOT NULL,
    minutes_ahead   INT         NOT NULL DEFAULT 30,
    confidence      DOUBLE PRECISION,           -- 0–1, derived from val loss

    -- When this prediction applies and when it was generated
    predicted_for   TIMESTAMPTZ NOT NULL,       -- NOW() + minutes_ahead
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Scheduler uses this to find and replace stale predictions
CREATE INDEX IF NOT EXISTS idx_aqi_pred_station_for
    ON aqi_predictions (station_id, predicted_for DESC);

-- Cleanup: auto-delete predictions older than 2 hours
-- (run periodically via scheduler or pg_cron)
-- DELETE FROM aqi_predictions WHERE predicted_for < NOW() - INTERVAL '2 hours';
