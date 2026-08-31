#!/bin/bash
set -e

# Enable PostGIS extensions (pgrouting is optional — SafeMAPS uses its own A* engine)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS postgis;
    CREATE EXTENSION IF NOT EXISTS postgis_topology;
    CREATE EXTENSION IF NOT EXISTS pg_trgm;

    -- Verify extensions
    SELECT PostGIS_Full_Version();
EOSQL

# Try pgrouting but don't fail if unavailable
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS pgrouting;" 2>/dev/null || echo "WARNING: pgrouting not available — skipping (not required for SafeMAPS A* engine)"

echo "PostGIS extensions initialized successfully."
