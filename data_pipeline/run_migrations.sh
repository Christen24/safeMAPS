#!/usr/bin/env bash
# ============================================================
# SafeMAPS — Run All Pending Migrations
# ============================================================
# Usage: bash data_pipeline/run_migrations.sh
# Requires: DATABASE_URL or POSTGRES_* env vars set in .env
#
# Migrations applied in order:
#   1. migration_cpcb.sql      — CPCB AQI source columns
#   2. migration_incidents.sql — live_incidents table
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load .env from project root if present
if [ -f "$SCRIPT_DIR/../.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/../.env" | xargs)
fi

# Build connection string
if [ -n "$DATABASE_URL" ]; then
    CONN="$DATABASE_URL"
else
    CONN="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@${POSTGRES_HOST}:${POSTGRES_PORT}/${POSTGRES_DB}"
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

run_migration "$SCRIPT_DIR/migration_cpcb.sql"
run_migration "$SCRIPT_DIR/migration_incidents.sql"

echo ""
echo "All migrations complete."
echo "Next: docker compose up -d (or restart backend)"
