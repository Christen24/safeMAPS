# SafeMAPS Deployment Phase 1 Results

Generated on branch `deployment-optimized`.

## Scope

Phase 1 attempted only one optimization:

- remove full per-edge GeoJSON geometry from `graph_cache.edge_data` at startup
- fetch final route edge geometries on demand with one batched SQL query

No routing weights, profiles, MCP tool schemas, frontend files, PostGIS schema, or routing algorithms were changed.

## Files Changed

- `backend/graph_cache.py`
- `backend/routing.py`

## Implementation Approach

`graph_cache.load()` no longer selects `ST_AsGeoJSON(geom)` from `road_segments` during startup and no longer stores a `geometry` key in `edge_data`.

A new `GraphCache.fetch_edge_geometries(db, edge_ids)` helper fetches only the selected route edge geometries:

- de-duplicates edge IDs before querying
- uses one batched SQL query with `WHERE id = ANY($1::bigint[])`
- returns an `edge_id -> GeoJSON geometry` mapping

`routing.find_route()` now:

- computes the path exactly as before using cached scalar edge metadata
- fetches geometries after `path_steps` are known
- preserves route ordering by iterating the original ordered `path_steps`
- keeps the existing `_orient_edge_coords()` direction handling
- builds the same route-level GeoJSON LineString shape

## Build And Health Results

Syntax/import check:

- `python -m compileall backend` passed

Backend tests:

- no backend test suite was found in the repository

Frontend build:

- not run because no frontend files were touched

Docker:

- `docker compose -f infrastructure/docker-compose.yml up -d --build backend mcp` completed
- `safemaps-mcp` became healthy
- `safemaps-backend` became healthy

Graph load:

- MCP loaded `559,602` nodes
- MCP loaded `352,579` edges
- backend `/health` reported graph loaded with `559,602` nodes and `352,579` edges

## Memory Results

Old baseline MCP memory:

- `1.288 GiB`

New measured MCP memory:

- `754.7 MiB`

MCP memory saved:

- approximately `564.2 MiB`
- approximately `42.8%`

Old baseline backend memory:

- `1.150 GiB`

New measured backend memory:

- initial: `752.8 MiB`
- later snapshot: `736.7 MiB`

Backend memory saved:

- approximately `440.9 MiB` using the later snapshot
- approximately `37.4%`

## Baseline Route Validation

Endpoint tested:

`GET /api/route/compare`

Each baseline case returned HTTP 200 and included route geometries.

However, route correctness validation did not pass because current route metrics differ from `docs/deployment_baseline.md`.

### Case A - Koramangala -> Whitefield

Coordinates:

- origin `(12.9352, 77.6245)`
- destination `(12.9698, 77.7499)`

New response times:

- run 1: `2705 ms`
- run 2: `2708 ms`

Observed mismatches against baseline:

- `fastest`: distance changed from `17.831687687238784` to `17.84164689942706`; coord count changed from `512` to `531`
- `healthiest`: travel time changed from `55.80417634946648` to `55.82299649214113`; total cost changed from `10.516874591284934` to `10.520616653550869`
- `balanced`: distance changed from `18.814136163950753` to `18.738585406911316`; coord count changed from `667` to `676`
- `safest`: matched baseline metrics and coord count

### Case B - MG Road -> Indiranagar

Coordinates:

- origin `(12.9757, 77.6086)`
- destination `(12.9784, 77.6408)`

New response times:

- run 1: `283 ms`
- run 2: `338 ms`

Observed mismatches against baseline:

- `fastest`: travel time changed from `9.145233537293931` to `9.155274740782202`; total cost changed accordingly
- `balanced`: travel time changed from `10.068167564920337` to `10.078208768408608`; total cost changed accordingly
- `safest`: matched baseline metrics and coord count
- `healthiest`: matched baseline metrics and coord count

### Case C - Yelahanka -> JP Nagar

Coordinates:

- origin `(13.1007, 77.5963)`
- destination `(12.9085, 77.5857)`

New response times:

- run 1: `5564 ms`
- run 2: `5411 ms`

Observed mismatches against baseline:

- `fastest`: distance changed from `25.105700814904466` to `25.39973234023773`; coord count changed from `781` to `797`
- `safest`: travel time changed from `69.90360140105109` to `69.97213016337501`; total cost changed from `22.751502601544736` to `22.760612999268165`; hotspot value is now `0` instead of baseline `null`
- `balanced`: travel time changed from `47.765873994349725` to `47.77762542658785`; total cost changed accordingly
- `healthiest`: matched baseline metrics and coord count

## Geometry Validation

All tested `/api/route/compare` responses included route-level LineString geometries.

Observed route geometry start/end coordinates aligned with the snapped road endpoints for each case, for example:

- Case A starts at `[77.6244413, 12.9351903]` and ends at `[77.7499176, 12.9698296]`
- Case B starts at `[77.6086167, 12.9757904]` and ends at `[77.6408126, 12.9784216]`
- Case C starts at `[77.5963862, 13.1010392]` and ends at `[77.5856759, 12.9084765]`

Geometry direction appears plausible from endpoint inspection, but full geometry correctness is not accepted because route metric validation failed.

## MCP Validation

MCP health passed:

- `{"status":"ok","graph_loaded":true}`

MCP tool-level validation was not run because `/api/route/compare` correctness diverged from the baseline and the task's stop conditions require stopping instead of expanding scope.

## Frontend Validation

Primary frontend route rendering and AI Demo validation were not run because backend route correctness diverged from the baseline and the task's stop conditions require stopping.

## Result

Memory improved meaningfully, but Phase 1 is not accepted as complete because route correctness comparison failed against `docs/deployment_baseline.md`.

The backend logs also show TomTom traffic updates starting during the validation window, so current persisted/live speed data may be a contributing factor. This was not investigated further in this task because that would expand beyond the Phase 1 geometry-cache optimization.

## Regression Or Limitation

Stop condition reached:

- routes changed unexpectedly relative to the baseline

Do not promote or commit this optimization until the correctness mismatch is resolved or explained with a controlled baseline using fixed traffic/speed state.
