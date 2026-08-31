# SafeMAPS Data Pipeline Architecture

**Phase 6 — Multi-Source AQI + Live Incident Pipelines**

---

## Architecture Overview

```
                ┌────────────────────────────────────────────────┐
                │           SafeMAPS Scheduler (5 Jobs)          │
                │                                                │
                │  Job 1: WAQI+CPCB  ─every 15min────────────── │─┐
                │  Job 2: Traffic    ─every  5min────────────── │ │
                │  Job 3: LSTM       ─every 30min────────────── │ │
                │  Job 4: CPCB-only  ─every 15min (+7min)─────  │ │
                │  Job 5: Incidents  ─every 10min────────────── │─┤
                └────────────────────────────────────────────────┘ │
                         │                                         │
              ┌──────────┴──────────┐             ┌───────────────┘
              ▼                     ▼             ▼
     ┌─────────────────┐  ┌────────────────┐  ┌───────────────────────┐
     │ AQI Scraper     │  │ Traffic Ingest │  │ Incident Scraper      │
     │                 │  │                │  │                       │
     │ • WAQI API      │  │ • TomTom API   │  │ • OSM Overpass        │
     │ • CPCB data.gov │  │ • (mock mode)  │  │ • Waze CCP (optional) │
     │                 │  │                │  │ • BTP Twitter (opt.)  │
     └────────┬────────┘  └───────┬────────┘  └───────────┬───────────┘
              │                   │                        │
              ▼                   ▼                        ▼
     ┌─────────────────────────────────────────────────────────────────┐
     │                       PostGIS Database                          │
     │                                                                 │
     │  aqi_stations        aqi_readings      aqi_history (LSTM)       │
     │  grid_cells          road_segments     aqi_predictions          │
     │  accident_blackspots                   live_incidents           │
     └───────────────────────────┬─────────────────────────────────────┘
                                 │
                                 ▼
                      ┌─────────────────┐
                      │   Graph Cache   │   (in-memory, ~200MB)
                      │                 │
                      │ edge_aqi        │   updated every 15min
                      │ edge_risk       │   updated at graph load
                      │ edge_incident   │   updated every 10min
                      └────────┬────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │   A* Routing Engine │
                    │                     │
                    │  C_e = α·T + β·AQI  │
                    │       + γ·(R + I)   │
                    └─────────────────────┘
```

---

## AQI Pipeline

### Sources

| Source | Frequency | Key Required | Coverage |
|--------|-----------|-------------|---------|
| WAQI   | 15 min    | `WAQI_API_TOKEN` | ~12 stations |
| CPCB (data.gov.in) | 15 min | `CPCB_API_KEY` | ~8 stations, 15-min freshness |

### CPCB Integration Notes
- **Endpoint:** `https://api.data.gov.in/resource/3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69`
- **Filter:** `State=Karnataka` → spatial filter to Bangalore bbox
- **AQI calculation:** CPCB breakpoint methodology (dominant pollutant)
- **Merge strategy:** CPCB preferred for recency; WAQI fills gaps
- **Dedup:** Spatial match within 500m

### Schema Changes (migration_cpcb.sql)
```sql
aqi_history.source          VARCHAR(10)  -- 'waqi' | 'cpcb' | 'merged'
aqi_history.so2             DOUBLE PRECISION
aqi_history.o3              DOUBLE PRECISION
aqi_history.pm25_24h_avg    DOUBLE PRECISION
aqi_stations.cpcb_station_id VARCHAR(64)
```

---

## Live Incident Pipeline

### Sources

| Source | Key Required | Data Type | TTL |
|--------|-------------|-----------|-----|
| OSM Overpass | None | hazard/closure/construction nodes | 2h |
| Waze CCP | `WAZE_CCP_URL` | GeoJSON alerts | Feed TTL |
| BTP Twitter | `X_BEARER_TOKEN` | @BlrCityTraffic tweets + Nominatim geocoding | 2h |

### Deduplication
- Incidents within **100m** of each other are merged
- Highest severity wins in a merge
- `external_id` prevents re-inserting the same Waze/OSM incident

### Routing Impact (edge cost formula)
```
C_e = α·T_e + β·(AQI_e/500)·T_min + γ·(R_e + I_e)

I_e = severity 1 → +2.0 cost units  (low: construction, minor hazard)
      severity 2 → +6.0 cost units  (medium: accident, closure)
      severity 3 → +10.0 cost units (high: road closure, major accident)
```

### Schema (migration_incidents.sql)
```sql
live_incidents:
  id, source, incident_type, lat, lon, geom,
  severity (1-3), description,
  reported_at, expires_at, is_active,
  external_id (unique per source)
```

### API
```
GET /api/incidents/active           → all active incidents as GeoJSON
GET /api/incidents/active?type=accident → filtered
GET /api/incidents/active?source=osm    → filtered
POST /api/admin/expire-incidents    → manual stale cleanup (admin key)
```

---

## RTI — BTP Historical Data

**Status:** Template ready (`docs/RTI_BTP_accident_data.md`)

File to: **Commissioner of Police, Bengaluru City**  
Requesting: GPS accident coordinates 2022–2024 (fatal/grievous/minor)  
Expected wait: **30 days** (statutory under RTI Act 2005)

Once received:
```bash
python data_pipeline/btp_accident_importer.py \
    --file data/btp_accidents_2022_2024.csv --clear
curl -X POST /api/admin/refresh-graph -H "X-Admin-Key: $ADMIN_API_KEY"
```

---

## Running Migrations

```bash
# Run all Phase 6 migrations at once
bash data_pipeline/run_migrations.sh

# Or individually
psql $DATABASE_URL -f data_pipeline/migration_cpcb.sql
psql $DATABASE_URL -f data_pipeline/migration_incidents.sql
```

---

## Environment Variables

| Variable | Required | Source |
|----------|---------|--------|
| `WAQI_API_TOKEN` | Optional | https://aqicn.org/api/ |
| `CPCB_API_KEY` | Optional | https://data.gov.in |
| `WAZE_CCP_URL` | Optional | https://developers.google.com/waze |
| `X_BEARER_TOKEN` | Optional | https://developer.twitter.com |
| `ADMIN_API_KEY` | Required | `python -c "import secrets; print(secrets.token_hex(32))"` |

OSM Overpass requires no key and always runs as the baseline incident source.
