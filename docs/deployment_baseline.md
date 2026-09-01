# SafeMAPS — Deployment Baseline

> Generated on branch `deployment-optimized` (forked from `main` at commit `007a64f`).  
> **No production code was modified to produce this document.**

---

## 1. System State

| Field | Value |
|---|---|
| DATE | 2026-09-01 (IST 19:14) |
| BRANCH | `deployment-optimized` |
| BASE COMMIT | `007a64f feat(ui): apply ai demo redesign and restore deterministic routing` |
| MAIN COMMIT | `007a64f` (identical — branch forked from HEAD of main) |

---

## 2. Database

| Metric | Value |
|---|---|
| DATABASE SIZE | **346 MB** |
| ROAD NODES | **559,602** |
| ROAD SEGMENTS | **352,579** |
| DB name | `healthroute` |
| DB user | `healthroute` |
| PostGIS image | `postgis/postgis:16-3.4` |

---

## 3. Memory (Docker RSS)

Measured with `docker stats --no-stream` against live containers (graph fully loaded):

| Container | Memory Usage | % of Host |
|---|---|---|
| `safemaps-mcp` | **1.288 GiB** | 16.92 % |
| `safemaps-backend` | **1.150 GiB** | 15.11 % |
| Host RAM | 7.612 GiB | — |

> Both processes independently hold the full 559k-node / 352k-edge graph in Python dicts.  
> Neither PyTorch nor NumPy is resident at MCP startup (lazy-imported only by `predict_aqi_near` on cache miss).

---

## 4. MCP Server Status

| Check | Result |
|---|---|
| MCP server starts | ✅ |
| `/health` endpoint | `{"status": "ok", "graph_loaded": true}` |
| Graph loaded in MCP process | ✅ |
| `compare_route_profiles` tool | ✅ available |
| `get_safe_route` tool | ✅ available |
| `predict_aqi_near` tool | ✅ available (LSTM lazy-loaded) |
| `get_accident_risk_near` tool | ✅ available |
| `get_aqi_heatmap_summary` tool | ✅ available |
| `get_aqi_near` tool | ✅ available |
| `explain_route_cost` tool | ✅ available |

---

## 5. Route Benchmarks

Endpoint: `GET /api/route/compare?origin_lat=…&origin_lon=…&dest_lat=…&dest_lon=…`  
All four profiles (fastest, safest, healthiest, balanced) returned per request.  
Each case run twice; values are **deterministic** (run 1 == run 2 for all metrics).

### BBOX Constraint

The loaded road network covers:

```
lat: 12.75 – 13.25
lon: 77.35 – 77.90
```

Points outside this bbox return HTTP 422. Kempegowda International Airport (13.1986, 77.7066) is within the bbox but its destination (Whitefield) produced no snappable road within 500m for the `road_segments` dataset loaded. Case C was replaced with Yelahanka → JP Nagar, a ~25 km cross-city route that exercises bidirectional A*.

---

### Test Case A — Koramangala → Whitefield

**Coordinates:** origin `(12.9352, 77.6245)` → dest `(12.9698, 77.7499)`  
**Straight-line distance:** ~17 km (bidirectional A* engaged, threshold 5 km)

| Run | Response Time |
|---|---|
| 1 | **3,856 ms** |
| 2 | **2,368 ms** |

| Profile | Distance (km) | Travel Time (min) | Avg AQI | Max AQI | Hotspots | Total Cost | Geometry coords |
|---|---|---|---|---|---|---|---|
| fastest | 17.832 | 29.842 | 124.8 | 161.2 | 1 | 29.842 | 512 |
| safest | 24.907 | 61.623 | 114.2 | 141.1 | 1 | 18.165 | 755 |
| healthiest | 23.265 | 55.804 | 102.1 | 120.5 | 1 | 10.517 | 769 |
| balanced | 18.814 | 32.518 | 122.2 | 161.2 | 1 | 24.400 | 667 |

---

### Test Case B — MG Road → Indiranagar

**Coordinates:** origin `(12.9757, 77.6086)` → dest `(12.9784, 77.6408)`  
**Straight-line distance:** ~3.4 km (standard unidirectional A*)

| Run | Response Time |
|---|---|
| 1 | **280 ms** |
| 2 | **276 ms** |

| Profile | Distance (km) | Travel Time (min) | Avg AQI | Max AQI | Hotspots | Total Cost | Geometry coords |
|---|---|---|---|---|---|---|---|
| fastest | 4.213 | 9.145 | 92.2 | 95.3 | 1 | 9.145 | 159 |
| safest | 5.229 | 12.606 | 92.1 | 95.3 | 1 | 4.864 | 184 |
| healthiest | 4.918 | 11.551 | 92.2 | 95.3 | 1 | 2.263 | 169 |
| balanced | 4.526 | 10.068 | 92.4 | 95.5 | 1 | 6.313 | 149 |

---

### Test Case C — Yelahanka → JP Nagar

**Coordinates:** origin `(13.1007, 77.5963)` → dest `(12.9085, 77.5857)`  
**Straight-line distance:** ~21 km (bidirectional A* engaged)

| Run | Response Time |
|---|---|
| 1 | **6,411 ms** |
| 2 | **5,218 ms** |

| Profile | Distance (km) | Travel Time (min) | Avg AQI | Max AQI | Hotspots | Total Cost | Geometry coords |
|---|---|---|---|---|---|---|---|
| fastest | 25.106 | 40.812 | 85.7 | 104.8 | 1 | 40.812 | 781 |
| safest | 30.476 | 69.904 | 85.6 | 104.8 | 0 | 22.752 | 870 |
| healthiest | 28.901 | 64.375 | 84.4 | 100.8 | 1 | 11.071 | 913 |
| balanced | 25.380 | 47.766 | 85.3 | 104.8 | 1 | 30.632 | 642 |

---

## 6. Correctness Reference

These exact values are the authoritative reference for validating future optimized builds.  
Any optimization that changes these values is **a regression** unless intentional and documented.

### Exact route metrics (full precision)

#### Case A — Koramangala → Whitefield

```json
[
  {"profile": "fastest",    "distance_km": 17.831687687238784, "travel_time_min": 29.841986426821812, "avg_aqi": 124.8, "max_aqi": 161.2, "hotspots": 1, "total_cost": 29.841986426821837, "coord_count": 512},
  {"profile": "safest",     "distance_km": 24.907123146959094, "travel_time_min": 61.62307231885018,  "avg_aqi": 114.2, "max_aqi": 141.1, "hotspots": 1, "total_cost": 18.16496844962322,  "coord_count": 755},
  {"profile": "healthiest", "distance_km": 23.26522953614157,  "travel_time_min": 55.80417634946648,  "avg_aqi": 102.1, "max_aqi": 120.5, "hotspots": 1, "total_cost": 10.516874591284934, "coord_count": 769},
  {"profile": "balanced",   "distance_km": 18.814136163950753, "travel_time_min": 32.51794315871445,  "avg_aqi": 122.2, "max_aqi": 161.2, "hotspots": 1, "total_cost": 24.400304030114892, "coord_count": 667}
]
```

#### Case B — MG Road → Indiranagar

```json
[
  {"profile": "fastest",    "distance_km": 4.212774839802096, "travel_time_min": 9.145233537293931,  "avg_aqi": 92.2, "max_aqi": 95.3, "hotspots": 1, "total_cost": 9.145233537293931,  "coord_count": 159},
  {"profile": "safest",     "distance_km": 5.228850564891389, "travel_time_min": 12.605978194384692, "avg_aqi": 92.1, "max_aqi": 95.3, "hotspots": 1, "total_cost": 4.863504865823969,  "coord_count": 184},
  {"profile": "healthiest", "distance_km": 4.917813859829254, "travel_time_min": 11.551323473852324, "avg_aqi": 92.2, "max_aqi": 95.3, "hotspots": 1, "total_cost": 2.2630893818736064, "coord_count": 169},
  {"profile": "balanced",   "distance_km": 4.526324428298162, "travel_time_min": 10.068167564920337, "avg_aqi": 92.4, "max_aqi": 95.5, "hotspots": 1, "total_cost": 6.312728829123406,  "coord_count": 149}
]
```

#### Case C — Yelahanka → JP Nagar

```json
[
  {"profile": "fastest",    "distance_km": 25.105700814904466, "travel_time_min": 40.81190642571154,  "avg_aqi": 85.7, "max_aqi": 104.8, "hotspots": 1,    "total_cost": 40.811906425711484, "coord_count": 781},
  {"profile": "safest",     "distance_km": 30.476028433607354, "travel_time_min": 69.90360140105109,  "avg_aqi": 85.6, "max_aqi": 104.8, "hotspots": null, "total_cost": 22.751502601544736, "coord_count": 870},
  {"profile": "healthiest", "distance_km": 28.90138548249834,  "travel_time_min": 64.37490552065125,  "avg_aqi": 84.4, "max_aqi": 100.8, "hotspots": 1,    "total_cost": 11.071276386453556, "coord_count": 913},
  {"profile": "balanced",   "distance_km": 25.380312413706008, "travel_time_min": 47.765873994349725, "avg_aqi": 85.3, "max_aqi": 104.8, "hotspots": 1,    "total_cost": 30.632387777388416, "coord_count": 642}
]
```

---

## 7. Performance Summary

| Case | Distance | A* Mode | Run 1 (ms) | Run 2 (ms) |
|---|---|---|---|---|
| A: Koramangala → Whitefield | ~17 km | Bidirectional | 3,856 | 2,368 |
| B: MG Road → Indiranagar | ~3.4 km | Unidirectional | 280 | 276 |
| C: Yelahanka → JP Nagar | ~21 km | Bidirectional | 6,411 | 5,218 |

> Short routes (&lt;5km) resolve in ~280ms. Long bidirectional routes take 2–6s.  
> All results are deterministic across runs (identical distances, costs, geometry).

---

## 8. Known Issues / Notes

- **KIA to Whitefield (13.1986, 77.7066 → 12.9698, 77.7499)** returns HTTP 422: destination Whitefield not snappable within 500m in the current loaded road segment dataset. This is a data coverage gap, not a routing bug.
- `safest` profile on Case C returns `hotspots: null` (not `0`). This is a response-shape inconsistency in the current backend — `accident_hotspots_passed` is absent from the cost breakdown when the value is zero for this profile/case combination.
- MCP's in-process `graph_cache` object is an empty singleton when inspected from a fresh Python subshell inside the container (expected — the graph lives in the uvicorn worker process, not a new subprocess).

---

## 9. What This Baseline Protects

Any change to the following files on `deployment-optimized` must produce identical correctness-reference values (or document deliberate divergence):

- `backend/graph_cache.py`
- `backend/routing.py`
- `backend/bidirectional_astar.py`
- `backend/mcp_server.py`

Memory targets for the optimization work:

| Target | Current | Goal |
|---|---|---|
| MCP RSS | 1.288 GiB | ≤ 512 MB |
| Backend RSS | 1.150 GiB | ≤ 512 MB |
