# SafeMAPS - BTP Accident Data Pipeline

## What This Does

SafeMAPS now supports real Bangalore Traffic Police crash counts released via
OpenCity as station-wise aggregate data. The pipeline converts those counts into
coarse accident-risk blackspots that the router can use through the existing
`accident_blackspots` table.

This is real BTP/OpenCity crash data, but it is not individual crash GPS data.
Each traffic police station jurisdiction becomes one representative blackspot.

## Files

| File | Purpose |
|------|---------|
| `_btp_cache/btp_2018_2020.csv` | Cached OpenCity/BTP crash counts |
| `_btp_cache/btp_2020_2022.csv` | Cached OpenCity/BTP crash counts |
| `_btp_cache/btp_2023.csv` | Cached OpenCity/BTP crash counts |
| `_btp_cache/btp_2024.csv` | Cached OpenCity/BTP crash counts |
| `_btp_cache/btp_2025.csv` | Cached OpenCity/BTP crash counts |
| `btp_station_geocoding.py` | Station name aliases and representative coordinates |
| `btp_station_importer.py` | Builds/imports real station-level blackspots |
| `data/btp_station_blackspots.csv` | Generated CSV loaded into PostGIS |
| `btp_accident_importer.py` | Separate RTI point-level importer for future GPS crash data |

## Build the CSV

```bash
cd data_pipeline
python btp_station_importer.py
```

This writes:

```text
data/btp_station_blackspots.csv
```

Columns:

```text
lat,lon,severity,severity_weight,total_accidents,fatal_accidents,description
```

`severity_weight` is a numeric 0-10 value. It is preserved by
`blackspot_mapper.py` and used by the backend graph cache when calculating edge
accident risk.

## Load Into the Database

Run this after `road_segments` has been loaded by `osm_loader.py`, because
blackspots are snapped to the nearest road edge:

```bash
cd data_pipeline
python btp_station_importer.py --load-db --clear
```

Equivalent two-step path:

```bash
cd data_pipeline
python btp_station_importer.py
python blackspot_mapper.py --csv data/btp_station_blackspots.csv --clear
```

(`--csv` is optional here — `blackspot_mapper.py` now defaults to this same
file. The synthetic `BUILT_IN_BLACKSPOTS` placeholder list that used to ship
as the no-`--csv` fallback has been removed now that real data is available.)

After importing, refresh the backend's in-memory graph cache:

```bash
curl -X POST http://localhost:8000/api/admin/refresh-graph
```

If your backend has `ADMIN_API_KEY` enabled, include the admin header.

## Severity Calculation

For each station:

```text
raw_score = (fatal_cases * 5 + killed_people * 3 + non_fatal_cases) / years_of_data
```

The raw scores are min-max scaled across all real BTP stations into a 0-10
`severity_weight`. This avoids saturating most of the city as "critical" while
still preserving the relative crash burden from the real data.

Severity labels are derived from that weight:

```text
critical >= 7
high     >= 4
moderate >= 2
low       < 2
```

## Known Limits

- Station-level only: one blackspot represents a whole traffic police station
  jurisdiction.
- Not street-level: exact dangerous intersections still require point-level
  accident records, such as the RTI data described in
  `docs/RTI_BTP_accident_data.md`.
- Historical signal: the data covers 2018-2025, so recent road changes may not
  be reflected.
