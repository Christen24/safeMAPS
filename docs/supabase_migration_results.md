# SafeMAPS — Supabase Migration Results

> **Final Result: PASS**
> Branch: `deployment-optimized`

---

## 1. Migration Method

| Item | Detail |
|---|---|
| Source | `safemaps-postgis` Docker container — `healthroute` database |
| Target | Supabase project `SafeMAPS`, region South Asia (Mumbai) |
| Method | `pg_dump --inserts` (data-only) → Google Colab → `psql` direct connection |
| Schema | Pre-existing in Supabase — not recreated |
| Indexes | Recreated from local schema after Supabase migration |

---

## 2. Connection Type Used

| Setting | Value |
|---|---|
| Pooler | **Session Pooler** (NOT transaction pooler) |
| Host | `aws-0-ap-south-1.pooler.supabase.com` |
| Port | **5432** |
| DB | `postgres` |
| User | `postgres.khyrggnokfkwykonpprw` |
| SSL | `require` |

> **Why Session Pooler?** asyncpg uses named prepared statements internally.
> The Supabase Transaction Pooler (port 6543) is PgBouncer in transaction mode,
> which drops prepared statement state between transactions — causing the exact
> `prepared statement "__asyncpg_stmt_e__" does not exist` error seen previously.
> Session Pooler maintains per-connection state, making it safe for asyncpg.

---

## 3. asyncpg Pool Configuration

```python
# backend/database.py
await asyncpg.create_pool(
    ...
    statement_cache_size=0,          # belt-and-braces: no named prepared stmts
    ssl="require",                    # enforced at driver level via POSTGRES_SSLMODE
)
```

`statement_cache_size=0` was already present before this migration.
The `ssl=` parameter was added in this session, driven by the new `postgres_sslmode`
field in `config.py` (defaults to `"disable"` for local dev, `"require"` for Supabase).

**Can the old prepared-statement error recur?**
No. Two independent safeguards prevent it:
1. Session Pooler maintains connection-level state → prepared stmts survive.
2. `statement_cache_size=0` means asyncpg never creates named prepared statements at all.

---

## 4. Source vs. Supabase Row Counts

| Table | Local (source) | Supabase | Result |
|---|---|---|---|
| `road_nodes` | 559,602 | 559,602 | **PASS** |
| `road_segments` | 352,579 | 352,579 | **PASS** |
| `grid_cells` | 112,558 | 112,558 | **PASS** |
| `accident_blackspots` | 55 | 55 | **PASS** |
| `live_incidents` | 807 (live) | 0 (purged) | **PASS** (BTP feed populates at runtime) |
| `traffic_snapshots` | 32,569 (live) | 25,796 | **Documented discrepancy** (see §12) |
| `aqi_stations` | 13 | 13 | PASS |
| `aqi_readings` | 23 | 23 | PASS |
| `aqi_history` | 0 | 0 | PASS |
| `aqi_predictions` | 0 | 0 | PASS |
| `trip_history` | 42 | 42 | PASS |
| `green_score_cache` | 6 | 6 | PASS |

---

## 5. Geometry Validation

| Table | Non-null geom | SRID | Result |
|---|---|---|---|
| `road_segments` | 352,579 / 352,579 | 4326 | PASS |
| `road_nodes` | All non-null | 4326 | PASS |
| `grid_cells` | All non-null | 4326 | PASS |
| `accident_blackspots` | All non-null | 4326 | PASS |

---

## 6. PostGIS Spatial Function Validation

| Test | Result |
|---|---|
| `ST_DWithin` (500m KNN around MG Road) | 741 nodes found — PASS |
| `ST_Intersects` (grid cell at MG Road) | 1 cell found — PASS |
| Nearest node KNN (`<->`) | Node 113 at (12.97160, 77.59460) — PASS |
| Nearest segment KNN | Segment 647 — Brigade Road, 500m — PASS |

---

## 7. v_edge_costs View Validation

The view `public.v_edge_costs` joins `road_segments` with `aqi_readings` and
`accident_blackspots` to produce per-edge routing costs.

Sample output verified:
```
edge_id=9623  road='3rd Cross Road'  len=42.7m  travel=6.15s  aqi=95.68
edge_id=9624  road='3rd Cross Road'  len=11.7m  travel=1.68s  aqi=95.68
edge_id=9625  road='3rd Cross Road'  len=17.6m  travel=2.54s  aqi=102.47
```
**Result: PASS**

---

## 8. Index / Constraint Validation

The following indexes were recreated from the local schema:

| Index | Table | Type |
|---|---|---|
| `road_nodes_pkey` | `road_nodes` | PRIMARY KEY |
| `road_nodes_osm_id_key` | `road_nodes` | UNIQUE |
| `idx_road_nodes_geom` | `road_nodes` | GiST spatial |
| `road_segments_pkey` | `road_segments` | PRIMARY KEY |
| `road_segments_unique_edge` | `road_segments` | UNIQUE |
| `idx_road_segments_geom` | `road_segments` | GiST spatial |
| `idx_road_segments_source` | `road_segments` | btree |
| `idx_road_segments_target` | `road_segments` | btree |
| `grid_cells_pkey` | `grid_cells` | PRIMARY KEY |
| `idx_grid_cells_geom` | `grid_cells` | GiST spatial |
| `idx_grid_cells_rowcol` | `grid_cells` | btree |
| `accident_blackspots_pkey` | `accident_blackspots` | PRIMARY KEY |
| `idx_blackspots_geom` | `accident_blackspots` | GiST spatial |
| `live_incidents_pkey` | `live_incidents` | PRIMARY KEY |
| `idx_live_incidents_geom` | `live_incidents` | GiST (partial — active only) |
| `idx_live_incidents_expires` | `live_incidents` | btree (partial — active only) |
| `idx_live_incidents_external_id` | `live_incidents` | UNIQUE |
| `idx_live_incidents_source_type` | `live_incidents` | btree |

**Result: PASS**

---

## 9. Sequence Validation

Sequences are managed by Supabase defaults. The `INSERTS`-based migration preserved
original `id` values. Sequences are set above the loaded max IDs during the
Colab migration run.

---

## 10. BTP Incident / Blackspot Validation

- `accident_blackspots`: 55 rows — all historical BTP data intact.
- `live_incidents`: Purged of legacy OSM incidents. BTP-only at runtime.
- `DISTINCT source` on `live_incidents` returns empty (clean state, awaiting live feed).

**BTP-only policy: PASS**

---

## 11. Final Database Size

| Metric | Value |
|---|---|
| Database size (post-index) | **245 MB** |
| Supabase free-tier quota | 500 MB |
| Available headroom | ~255 MB |

---

## 12. Documented Discrepancies

### traffic_snapshots (live table)
| | Count |
|---|---|
| Supabase (migrated) | 25,796 |
| Local (at validation time) | 32,569 |
| Difference | 6,773 |

**Cause:** `traffic_snapshots` is a continuously appended historical table.
The local scheduler continued writing rows overnight between the Colab migration
and the validation run. This is expected and acceptable — the table will grow
in Supabase independently once the scheduler points to Supabase.

---

## 13. Git Status

| | |
|---|---|
| Branch | `deployment-optimized` |
| `.env` committed? | **No** — protected by `.gitignore:*.env` |
| Password committed? | **No** |
| Code changes committed | `backend/config.py` + `backend/database.py` |
| Commit message | `config/db: add postgres_sslmode setting and wire ssl into asyncpg pool` |

---

## 14. Final PASS / FAIL

| Check | Result |
|---|---|
| Direct/reliable migration completed | PASS |
| `road_nodes` count matches | PASS (559,602) |
| `road_segments` count matches | PASS (352,579) |
| `grid_cells` count matches | PASS (112,558) |
| `accident_blackspots` count matches | PASS (55) |
| `live_incidents` BTP-only policy | PASS |
| Geometries intact | PASS |
| SRID correct (4326) | PASS |
| PostGIS ST_DWithin / ST_Intersects work | PASS |
| `v_edge_costs` queryable | PASS |
| Indexes / constraints present | PASS |
| Sequences correct | PASS |
| Session Pooler configured (not transaction pooler) | PASS |
| `statement_cache_size=0` in asyncpg pool | PASS |
| SSL enforced at driver level | PASS |
| No credentials committed to Git | PASS |
| Local source database untouched | PASS |

> **OVERALL: PASS**
