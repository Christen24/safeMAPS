# SafeMAPS — BTP Accident Data Pipeline

## What this does

Downloads real Bangalore Traffic Police (BTP) crash data from OpenCity,
geocodes each police station jurisdiction to GPS coordinates, and imports
weighted blackspot data into the `accident_blackspots` table that SafeMAPS
uses for health-aware routing.

## Data sources (all free, public domain)

| File | Source | What it contains |
|------|--------|-----------------|
| `btp_2018_2020.csv` | OpenCity / BTP | Station-wise fatal + non-fatal crashes, 2018–2020 |
| `btp_2020_2022.csv` | OpenCity / BTP | Station-wise fatal + non-fatal crashes, 2020–2022 |
| `btp_2023.csv`      | OpenCity / BTP | Station-wise fatal + non-fatal crashes, 2023 |
| `btp_2024.csv`      | OpenCity / BTP | Station-wise total + fatal crashes, 2024 |
| `btp_2025.csv`      | OpenCity / BTP | Station-wise total + fatal crashes, 2025 |
| `btp_jurisdictions_2022.kml` | OpenCity / KSRSAC | Polygon boundaries of each BTP station jurisdiction |

All files are cached in `data_pipeline/_btp_cache/` after first download.

## CSV column structure (2023 format)

```
Zone, Sub-division, Station,
2023 - Fatal Cases, 2023 - Killed People,
2023 - Non-Fatal, 2023 - Injured People,
2023 - Total Cases
```

Subtotal rows (Zone Total, Sub-division Total, Grand Total) are skipped.

## How severity is calculated

```
raw_score = (fatal_cases × 5 + killed_people × 3 + non_fatal × 1)
            / years_of_data

severity_weight = min(raw_score / 10, 10.0)   # 0–10 scale
```

Severity tiers:
- **Critical** (weight ≥ 7): International Airport, Yalahanka, Kengeri, Whitefield
- **High** (weight ≥ 4): Peenya, Kamakshipalya, KR Puram, Electronic City
- **Moderate** (weight ≥ 2): Most inner-city stations
- **Low** (weight < 2): Lower-risk areas

## How geocoding works

1. **KML polygon centroids (preferred)** — the jurisdiction KML gives the
   exact boundary polygon for each station. We compute the centroid of that
   polygon as the station's representative coordinate. This places the
   blackspot in the centre of the high-risk area, not just at the station building.

2. **Nominatim fallback** — stations not found in the KML (name mismatches,
   newer stations added after 2022) are geocoded via OpenStreetMap Nominatim
   with the query `"{name} Traffic Police Station, Bangalore, India"`.

## Installation

```bash
cd data_pipeline
pip install httpx asyncpg shapely
```

## Usage

```bash
# Full run — downloads, parses, prints summary, imports to DB:
python btp_accident_importer.py

# Dry run — see what would be imported without touching the DB:
python btp_accident_importer.py --dry-run

# Append to existing blackspots (don't clear the table first):
python btp_accident_importer.py --keep-existing

# Re-run after updating data (cache is local, re-download by deleting cache):
rm -rf data_pipeline/_btp_cache/
python btp_accident_importer.py
```

## After import

The router uses `severity_weight` from `accident_blackspots` when computing
edge risk scores. After import, call the admin endpoint to refresh the
in-memory graph cache immediately (otherwise the next scheduler cycle does it):

```bash
curl -X POST http://localhost:8000/api/admin/refresh-graph
```

## Known limitations

- **Station-level, not spot-level**: BTP publishes aggregates per police station
  jurisdiction (each covers several km²), not individual crash GPS coordinates.
  The blackspot is placed at the polygon centroid, which represents the whole
  jurisdiction's risk, not a single dangerous intersection.

- **No street-level precision**: For precise hotspot mapping you need individual
  crash records with coordinates. These can be obtained via RTI request to BTP.

- **Temporal**: Data reflects 2018–2025 historical patterns. New infrastructure
  (flyovers, underpasses) may have changed risk profiles since then.

## Data quality notes from OpenCity

- 2024 and 2025 CSVs do not include "Killed People" and "Injured People"
  columns — only Total Crashes and Fatal Crashes. The pipeline handles this.
- Whitefield 2023 data includes Mahadevapura and Bellandur stations.
- Some station names differ between CSV and KML (e.g. "Int. Aiport" vs
  "International Airport"). Manual overrides handle the known mismatches.
