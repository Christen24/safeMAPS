#!/usr/bin/env bash
# ============================================================
# SafeMAPS — Run All Pending Migrations
# ============================================================
# Usage: bash data_pipeline/run_migrations.sh
# Requires: POSTGRES_* env vars set in .env (project root)
#
# NOTE on ports:
#   POSTGRES_PORT in .env should be 5433 — the HOST-MAPPED port
#   (docker-compose maps 5433→5432 for PostGIS).
#   Inside the Docker network the backend uses port 5432 via
#   PgBouncer — that is set directly in docker-compose.yml and
#   should NOT match this script.
#
# Migrations applied in order:
#   1. database_seeder.sql   — base tables (road_nodes, road_segments,
#                               grid_cells, aqi_stations, accident_blackspots)
#   2. migration_phase5.sql  — aqi_history, aqi_predictions
#   3. migration_phase6.sql  — trip_history, green_score_cache
#   4. migration_cpcb.sql    — CPCB AQI source columns (ALTER TABLE)
#   5. migration_incidents.sql — live_incidents table
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env from project root if present
if [ -f "$SCRIPT_DIR/../.env" ]; then
    # shellcheck disable=SC2046
    export $(grep -v '^#' "$SCRIPT_DIR/../.env" | xargs)
fi

# Build connection string — use port 5433 (host-mapped port for direct access)
if [ -n "$DATABASE_URL" ]; then
    CONN="$DATABASE_URL"
else
    # Explicitly use 5433: the host-side port that maps to PostGIS inside Docker
    HOST_PORT="${POSTGRES_PORT:-5433}"
    CONN="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST:-localhost}:${HOST_PORT}/${POSTGRES_DB:-healthroute}"
fi

run_migration() {
    local file="$1"
    local name="$(basename "$file")"
    echo "──────────────────────────────────────"
    echo "  Applying: $name"
    echo "──────────────────────────────────────"
    psql "$CONN" -f "$file"
    echo "  ✓ $name applied"
}

echo "SafeMAPS Migration Runner"
echo "Target: $CONN"
echo ""

# 1. Base schema — must run first; everything else depends on these tables
run_migration "$SCRIPT_DIR/database_seeder.sql"

# 2. LSTM feature tables
run_migration "$SCRIPT_DIR/migration_phase5.sql"

# 3. Green Score / trip history tables
run_migration "$SCRIPT_DIR/migration_phase6.sql"

# 4. CPCB columns (ALTER TABLE on aqi_history — must exist from phase5 first)
run_migration "$SCRIPT_DIR/migration_cpcb.sql"

# 5. Live incidents table
run_migration "$SCRIPT_DIR/migration_incidents.sql"

echo ""
echo "All migrations complete."
echo "Next: docker compose up -d (or restart backend)"
