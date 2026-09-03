"""
SafeMAPS — CSR Graph Cache (Phase 3)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Replaces the dict-based graph with a Compressed Sparse Row (CSR)
NumPy array representation.

Why this works
──────────────
Phase 1/2 measured ~650–750 MB RSS per process. The raw data is only ~15 MB.
The rest is CPython per-object overhead:
  - dict[int, tuple]  : 559,602 node entries
  - dict[int, dict]   : 352,579 edge dicts, each 5 keys incl. 2 strings
  - two full adjacency dicts × ~700k list-of-tuple entries
  - three 352k-entry cost dicts (AQI, risk, incident)

CSR representation:
  - node lat/lon      : 2 × float32[N]              ~4.5 MB
  - node id↔idx map   : int64[N] + searchsorted      ~4.5 MB
  - forward CSR       : indptr[N+1] + 4 arrays[E]    ~10 MB
  - reverse CSR       : same shape                   ~10 MB
  - cost overlays     : 3 × float32[E]               ~4 MB
  Total: ~33 MB resident for the full Bangalore graph

Road name / road type are deferred to on-demand DB fetch (same pattern
as geometry in Phase 1) — they are only needed for the final assembled
route response and the time-of-day multiplier lookup, not during search.

The A* and bidirectional A* implementations receive integer CSR slices
instead of Python list-of-tuple iterations. Inner loops stay in CPython
but operate on much smaller working sets (no per-edge dict lookups).

Scheduler integration
──────────────────────
refresh_aqi_costs   → writes into self.aqi_costs (float32 array, indexed by compact edge index)
update_speeds       → writes into self.speed_data (float32 array)
refresh_incident_costs → writes into self.incident_costs (float32 array, sparse via dict)

The atomic swap on cost overlays works as before: build new array,
reassign the attribute name, GC collects old.
"""

import gc
import logging
import time
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

ROAD_TYPES = [
    "motorway", "motorway_link", "trunk", "trunk_link",
    "primary", "primary_link", "secondary", "secondary_link",
    "tertiary", "tertiary_link", "unclassified", "residential",
    "living_street", "service", "pedestrian", "track", "path", 
    "cycleway", "footway", "steps", "road", "school_zone", 
    "school", "school_zone_link", "unknown"
]
ROAD_TYPE_TO_CODE = {rt: i for i, rt in enumerate(ROAD_TYPES)}
UNKNOWN_CODE = ROAD_TYPE_TO_CODE["unknown"]


class GraphCache:
    """Singleton holding the in-memory road graph as CSR arrays."""

    def __init__(self):
        # ── Node arrays ─────────────────────────────────────────────
        # node_ids[i] = DB id, node_lat[i]/node_lon[i] = coords
        # Sorted by node_ids so we can use np.searchsorted for O(log N) lookup
        self.node_ids: Optional[np.ndarray] = None     # int64[N]
        self.node_lat: Optional[np.ndarray] = None     # float32[N]
        self.node_lon: Optional[np.ndarray] = None     # float32[N]

        # ── Forward CSR ──────────────────────────────────────────────
        # indptr[i]:indptr[i+1] is the slice of nbr_idx/edge_idx/... for node i
        self.fwd_indptr:  Optional[np.ndarray] = None  # int32[N+1]
        self.fwd_nbr:     Optional[np.ndarray] = None  # int32[E_total]   neighbour compact index
        self.fwd_eid:     Optional[np.ndarray] = None  # int32[E_total]   compact edge index
        self.fwd_length:  Optional[np.ndarray] = None  # float32[E_total] metres

        # ── Reverse CSR (for bidirectional A*) ───────────────────────
        self.rev_indptr:  Optional[np.ndarray] = None  # int32[N+1]
        self.rev_nbr:     Optional[np.ndarray] = None  # int32[E_total]
        self.rev_eid:     Optional[np.ndarray] = None  # int32[E_total]
        self.rev_length:  Optional[np.ndarray] = None  # float32[E_total]

        # ── Edge id mappings ─────────────────────────────────────────
        # edge_db_ids[i] = DB edge id for compact index i  (sorted)
        # compact index = np.searchsorted(edge_db_ids, db_id)
        self.edge_db_ids: Optional[np.ndarray] = None  # int64[E_unique]
        # base speed per unique edge (for congestion ratio)
        self.base_speed:  Optional[np.ndarray] = None  # float32[E_unique]
        self.current_speed: Optional[np.ndarray] = None # float32[E_unique]
        self.road_type_code: Optional[np.ndarray] = None # int8[E_unique]

        # ── Cost overlays (indexed by compact edge index) ────────────
        self.aqi_costs:      Optional[np.ndarray] = None  # float32[E_unique]
        self.risk_costs:     Optional[np.ndarray] = None  # float32[E_unique]
        # Incident costs: sparse — only affected edges are non-zero.
        # Stored as a plain float32 array; most entries are 0.0.
        self.incident_costs: Optional[np.ndarray] = None  # float32[E_unique]

        self._loaded_at: Optional[float] = None
        self._aqi_refreshed_at: Optional[float] = None
        self._incident_refreshed_at: Optional[float] = None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def node_count(self) -> int:
        return len(self.node_ids) if self.node_ids is not None else 0

    @property
    def edge_count(self) -> int:
        return len(self.edge_db_ids) if self.edge_db_ids is not None else 0

    @property
    def age_seconds(self) -> float:
        return -1.0 if self._loaded_at is None else time.monotonic() - self._loaded_at

    @property
    def aqi_age_seconds(self) -> float:
        return -1.0 if self._aqi_refreshed_at is None else time.monotonic() - self._aqi_refreshed_at

    @property
    def incident_age_seconds(self) -> float:
        return -1.0 if self._incident_refreshed_at is None else time.monotonic() - self._incident_refreshed_at

    @property
    def incident_count(self) -> int:
        if self.incident_costs is None:
            return 0
        return int(np.count_nonzero(self.incident_costs))

    @property
    def aqi_count(self) -> int:
        if self.aqi_costs is None:
            return 0
        return int(np.count_nonzero(self.aqi_costs != 50.0))

    # ── Node lookup helpers ───────────────────────────────────────────

    def node_idx(self, db_id: int) -> int:
        """Return compact index for a DB node id, or -1 if not found."""
        if self.node_ids is None:
            return -1
        idx = int(np.searchsorted(self.node_ids, db_id))
        if idx < len(self.node_ids) and self.node_ids[idx] == db_id:
            return idx
        return -1

    def edge_idx(self, db_id: int) -> int:
        """Return compact index for a DB edge id, or -1 if not found."""
        if self.edge_db_ids is None:
            return -1
        idx = int(np.searchsorted(self.edge_db_ids, db_id))
        if idx < len(self.edge_db_ids) and self.edge_db_ids[idx] == db_id:
            return idx
        return -1

    def get_road_type(self, compact_eid: int) -> str:
        if self.road_type_code is None or compact_eid < 0 or compact_eid >= len(self.road_type_code):
            return "unknown"
        code = self.road_type_code[compact_eid]
        if 0 <= code < len(ROAD_TYPES):
            return ROAD_TYPES[code]
        return "unknown"

    # ── Full graph load ───────────────────────────────────────────────

    async def load(self, db) -> int:
        """
        Load the full road graph from PostGIS into CSR arrays.
        Runs once at startup; also callable from admin endpoint.
        Returns the number of nodes loaded.
        """
        t0 = time.monotonic()
        logger.info("Loading road graph from PostGIS into CSR arrays...")

        # ── 1. Nodes ─────────────────────────────────────────────────
        node_rows = await db.fetch(
            "SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM road_nodes;"
        )
        if not node_rows:
            logger.warning(
                "road_nodes is empty — run: cd data_pipeline && python osm_loader.py"
            )
            return 0

        # Build sorted node arrays
        db_ids_list = [row["id"]  for row in node_rows]
        lat_list    = [row["lat"] for row in node_rows]
        lon_list    = [row["lon"] for row in node_rows]

        sort_order  = np.argsort(db_ids_list, kind="stable")
        node_ids    = np.array(db_ids_list, dtype=np.int64)[sort_order]
        node_lat    = np.array(lat_list,    dtype=np.float32)[sort_order]
        node_lon    = np.array(lon_list,    dtype=np.float32)[sort_order]
        # id → compact index lookup dictionary for O(1) bulk mapping during build
        id_to_idx   = {int(nid): int(i) for i, nid in enumerate(node_ids)}

        N = len(node_ids)
        logger.info(f"  Nodes: {N:,}")

        # ── 2. Edges ─────────────────────────────────────────────────
        fwd_src_list: list[int] = []
        fwd_tgt_list: list[int] = []
        fwd_eid_list: list[int] = []
        fwd_len_list: list[float] = []

        edge_db_id_list: list[int]   = []
        base_speed_list: list[float] = []
        road_type_list: list[int]    = []
        # Map DB edge id → compact edge index (built incrementally)
        edge_id_to_compact: dict[int, int] = {}

        async with db._require_pool().acquire() as conn:
            async with conn.transaction():
                async for row in conn.cursor("""
                    SELECT
                        id,
                        source_node,
                        target_node,
                        length_m,
                        speed_kmh,
                        road_type,
                        oneway
                    FROM road_segments;
                """):
                    db_eid  = int(row["id"])
                    src_did = int(row["source_node"])
                    tgt_did = int(row["target_node"])
                    length  = float(row["length_m"]  or 0)
                    speed   = float(row["speed_kmh"] or 30)
                    rtype   = row["road_type"] or "unknown"
                    oneway  = bool(row["oneway"])

                    src_idx = id_to_idx.get(src_did, -1)
                    tgt_idx = id_to_idx.get(tgt_did, -1)
                    if src_idx < 0 or tgt_idx < 0:
                        continue  # dangling edge — skip

                    # Assign compact edge index (each DB edge gets exactly one)
                    if db_eid not in edge_id_to_compact:
                        compact_eid = len(edge_db_id_list)
                        edge_id_to_compact[db_eid] = compact_eid
                        edge_db_id_list.append(db_eid)
                        base_speed_list.append(speed)
                        road_type_list.append(ROAD_TYPE_TO_CODE.get(rtype, UNKNOWN_CODE))
                    else:
                        compact_eid = edge_id_to_compact[db_eid]

                    fwd_src_list.append(src_idx)
                    fwd_tgt_list.append(tgt_idx)
                    fwd_eid_list.append(compact_eid)
                    fwd_len_list.append(length)

                    if not oneway:
                        # Add the reverse direction to the forward adjacency
                        fwd_src_list.append(tgt_idx)
                        fwd_tgt_list.append(src_idx)
                        fwd_eid_list.append(compact_eid)
                        fwd_len_list.append(length)

        E = len(edge_db_id_list)
        logger.info(f"  Unique edges: {E:,}")

        # ── 3. Build CSR arrays ───────────────────────────────────────
        def _build_csr_from_flat(src_arr, tgt_arr, eid_arr, length_arr) -> tuple:
            sort_idx = np.argsort(src_arr, kind="stable")
            sorted_src = src_arr[sort_idx]
            sorted_tgt = tgt_arr[sort_idx]
            sorted_eid = eid_arr[sort_idx]
            sorted_len = length_arr[sort_idx]
            
            degrees = np.bincount(sorted_src, minlength=N)
            indptr = np.zeros(N + 1, dtype=np.int32)
            indptr[1:] = np.cumsum(degrees)
            
            return indptr, sorted_tgt, sorted_eid, sorted_len

        logger.info("  Converting flat lists to arrays...")
        fwd_src_arr = np.array(fwd_src_list, dtype=np.int32)
        fwd_tgt_arr = np.array(fwd_tgt_list, dtype=np.int32)
        fwd_eid_arr = np.array(fwd_eid_list, dtype=np.int32)
        fwd_len_arr = np.array(fwd_len_list, dtype=np.float32)
        
        # Free python lists early
        del fwd_src_list, fwd_tgt_list, fwd_eid_list, fwd_len_list

        logger.info("  Building forward CSR...")
        fwd_indptr, fwd_nbr, fwd_eid, fwd_length = _build_csr_from_flat(
            fwd_src_arr, fwd_tgt_arr, fwd_eid_arr, fwd_len_arr
        )

        logger.info("  Building reverse CSR...")
        rev_indptr, rev_nbr, rev_eid, rev_length = _build_csr_from_flat(
            fwd_tgt_arr, fwd_src_arr, fwd_eid_arr, fwd_len_arr
        )
        
        del fwd_src_arr, fwd_tgt_arr, fwd_eid_arr, fwd_len_arr

        # ── 4. Edge id → compact index lookup array ───────────────────
        # Sort by DB edge id so we can use searchsorted
        edge_db_ids_arr = np.array(edge_db_id_list, dtype=np.int64)
        base_speed_arr  = np.array(base_speed_list, dtype=np.float32)
        road_type_arr   = np.array(road_type_list, dtype=np.int8)

        edge_sort = np.argsort(edge_db_ids_arr, kind="stable")
        edge_db_ids_sorted = edge_db_ids_arr[edge_sort]
        base_speed_sorted  = base_speed_arr[edge_sort]
        current_speed_sorted = np.copy(base_speed_sorted)
        road_type_sorted   = road_type_arr[edge_sort]

        # Remap compact indices in CSR arrays to match sorted order
        # old_compact_idx → new_compact_idx
        remap = np.empty(E, dtype=np.int32)
        remap[edge_sort] = np.arange(E, dtype=np.int32)
        fwd_eid = remap[fwd_eid]
        rev_eid = remap[rev_eid]

        # ── 5. Cost overlays — default values ────────────────────────
        aqi_costs      = np.full(E, 50.0, dtype=np.float32)
        risk_costs     = np.zeros(E, dtype=np.float32)
        incident_costs = np.zeros(E, dtype=np.float32)

        # ── 6. Release build temporaries and commit atomically ────────
        del node_rows
        del id_to_idx, edge_id_to_compact
        del edge_db_ids_arr, base_speed_arr, edge_sort
        gc.collect()

        self.node_ids    = node_ids
        self.node_lat    = node_lat
        self.node_lon    = node_lon
        self.fwd_indptr  = fwd_indptr
        self.fwd_nbr     = fwd_nbr
        self.fwd_eid     = fwd_eid
        self.fwd_length  = fwd_length
        self.rev_indptr  = rev_indptr
        self.rev_nbr     = rev_nbr
        self.rev_eid     = rev_eid
        self.rev_length  = rev_length
        self.edge_db_ids = edge_db_ids_sorted
        self.base_speed  = base_speed_sorted
        self.current_speed = current_speed_sorted
        self.road_type_code = road_type_sorted
        self.aqi_costs   = aqi_costs
        self.risk_costs  = risk_costs
        self.incident_costs = incident_costs
        self._loaded_at  = time.monotonic()

        elapsed = time.monotonic() - t0
        logger.info(
            f"Graph loaded in {elapsed:.1f}s: {N:,} nodes, {E:,} edges. "
            f"Fwd CSR: {len(fwd_nbr):,} entries, Rev CSR: {len(rev_nbr):,} entries."
        )

        await self._prefetch_edge_costs(db)
        gc.collect()
        return N

    # ── On-demand geometry fetch (unchanged from Phase 1) ─────────────

    async def fetch_edge_geometries(self, db, edge_db_ids: list[int]) -> dict[int, dict]:
        """
        Fetch GeoJSON geometries for the final route edges only.
        Phase 1 design: startup graph excludes geometry; fetched lazily per route.
        """
        import json
        unique_ids = list(dict.fromkeys(edge_db_ids))
        if not unique_ids:
            return {}
        rows = await db.fetch("""
            SELECT id, ST_AsGeoJSON(geom) AS geometry
            FROM road_segments
            WHERE id = ANY($1::bigint[]);
        """, unique_ids)
        result: dict[int, dict] = {}
        for row in rows:
            geom_str = row["geometry"]
            result[row["id"]] = (
                json.loads(geom_str) if geom_str
                else {"type": "LineString", "coordinates": []}
            )
        return result

    # ── On-demand road_name / road_type fetch ─────────────────────────

    async def fetch_edge_metadata(self, db, edge_db_ids: list[int]) -> dict[int, dict]:
        """
        Fetch road_name and road_type for the final route edges only.
        Deferred from startup to avoid holding 352k string objects in RAM.
        """
        unique_ids = list(dict.fromkeys(edge_db_ids))
        if not unique_ids:
            return {}
        rows = await db.fetch("""
            SELECT id, road_name, road_type
            FROM road_segments
            WHERE id = ANY($1::bigint[]);
        """, unique_ids)
        return {row["id"]: {"road_name": row["road_name"], "road_type": row["road_type"]}
                for row in rows}

    # ── Cost prefetch (called once after load) ─────────────────────────

    async def _prefetch_edge_costs(self, db) -> None:
        t0 = time.monotonic()
        logger.info("Pre-fetching edge AQI and risk costs...")

        # AQI
        try:
            aqi_rows = await db.fetch("""
                SELECT e.id AS edge_id, COALESCE(AVG(g.aqi_value), 50.0) AS avg_aqi
                FROM road_segments e
                LEFT JOIN grid_cells g ON ST_Intersects(e.geom, g.geom)
                GROUP BY e.id;
            """)
            new_aqi = np.full(self.edge_count, 50.0, dtype=np.float32)
            for row in aqi_rows:
                idx = self.edge_idx(int(row["edge_id"]))
                if idx >= 0:
                    new_aqi[idx] = float(row["avg_aqi"])
            self.aqi_costs = new_aqi
            logger.info(f"  AQI loaded for {len(aqi_rows):,} edges.")
        except Exception as exc:
            logger.warning(f"  AQI prefetch failed: {exc}")

        # Risk
        try:
            risk_rows = await db.fetch("""
                SELECT e.id AS edge_id,
                    COALESCE(SUM(b.severity_weight /
                        GREATEST(ST_Distance(e.geom::geography, b.geom::geography), 1.0)
                    ), 0.0) AS risk_score
                FROM road_segments e
                LEFT JOIN accident_blackspots b
                    ON ST_DWithin(e.geom::geography, b.geom::geography, 200)
                GROUP BY e.id;
            """)
            new_risk = np.zeros(self.edge_count, dtype=np.float32)
            for row in risk_rows:
                idx = self.edge_idx(int(row["edge_id"]))
                if idx >= 0:
                    new_risk[idx] = float(row["risk_score"])
            self.risk_costs = new_risk
            logger.info(f"  Risk loaded for {len(risk_rows):,} edges.")
        except Exception as exc:
            logger.warning(f"  Risk prefetch failed: {exc}")

        self._aqi_refreshed_at = time.monotonic()
        logger.info(f"  Edge cost prefetch complete in {time.monotonic() - t0:.1f}s.")

    # ── AQI refresh (called by scheduler every 15 min) ────────────────

    async def refresh_aqi_costs(self, db) -> None:
        t0 = time.monotonic()
        logger.info("[cache] Refreshing edge AQI costs...")
        try:
            aqi_rows = await db.fetch("""
                SELECT e.id AS edge_id, COALESCE(AVG(g.aqi_value), 50.0) AS avg_aqi
                FROM road_segments e
                LEFT JOIN grid_cells g ON ST_Intersects(e.geom, g.geom)
                GROUP BY e.id;
            """)
            new_aqi = np.full(self.edge_count, 50.0, dtype=np.float32)
            for row in aqi_rows:
                idx = self.edge_idx(int(row["edge_id"]))
                if idx >= 0:
                    new_aqi[idx] = float(row["avg_aqi"])
            self.aqi_costs = new_aqi  # atomic swap
            self._aqi_refreshed_at = time.monotonic()
            logger.info(f"[cache] AQI refresh complete in {time.monotonic() - t0:.1f}s.")
        except Exception as exc:
            logger.warning(f"[cache] AQI refresh failed — keeping previous values. Error: {exc}")

    # ── Incident refresh (called by scheduler every 10 min) ───────────

    async def refresh_incident_costs(self, db) -> None:
        t0 = time.monotonic()
        logger.info("[cache] Refreshing edge incident costs...")
        try:
            rows = await db.fetch("""
                SELECT e.id AS edge_id,
                    COALESCE(SUM(
                        CASE li.severity WHEN 3 THEN 10.0 WHEN 2 THEN 6.0 ELSE 2.0 END
                    ), 0.0) AS incident_cost
                FROM road_segments e
                JOIN live_incidents li
                    ON ST_DWithin(e.geom::geography, li.geom::geography, 200)
                WHERE li.is_active = TRUE AND li.expires_at > NOW()
                GROUP BY e.id;
            """)
            new_inc = np.zeros(self.edge_count, dtype=np.float32)
            for row in rows:
                idx = self.edge_idx(int(row["edge_id"]))
                if idx >= 0:
                    new_inc[idx] = float(min(row["incident_cost"], 10.0))
            self.incident_costs = new_inc  # atomic swap
            self._incident_refreshed_at = time.monotonic()
            logger.info(
                f"[cache] Incident refresh complete in {time.monotonic() - t0:.1f}s "
                f"({self.incident_count} edges affected)."
            )
        except Exception as exc:
            logger.warning(f"[cache] Incident refresh failed — keeping previous values. Error: {exc}")

    # ── Speed patch (called by scheduler every 5 min) ─────────────────

    def update_speeds(self, edge_speeds: dict[int, float]) -> None:
        """
        Patch current_speed in-place with fresh speed_kmh values.
        edge_speeds: {DB_edge_id: speed_kmh}
        """
        if not edge_speeds or self.current_speed is None:
            return
        patched = 0
        for db_eid, new_speed in edge_speeds.items():
            cidx = self.edge_idx(db_eid)
            if cidx < 0:
                continue
            self.current_speed[cidx] = np.float32(new_speed)
            patched += 1
        logger.info(f"[cache] Speed patch: {patched} edges updated.")

    # ── Cost accessors (return Python float for compatibility) ─────────

    def get_aqi(self, compact_eid: int) -> float:
        if self.aqi_costs is None or compact_eid < 0 or compact_eid >= len(self.aqi_costs):
            return 50.0
        return float(self.aqi_costs[compact_eid])

    def get_risk(self, compact_eid: int) -> float:
        if self.risk_costs is None or compact_eid < 0 or compact_eid >= len(self.risk_costs):
            return 0.0
        return float(self.risk_costs[compact_eid])

    def get_incident(self, compact_eid: int) -> float:
        if self.incident_costs is None or compact_eid < 0 or compact_eid >= len(self.incident_costs):
            return 0.0
        return float(self.incident_costs[compact_eid])

    def get_congestion(self, compact_eid: int) -> float:
        """
        Congestion ratio: 0.0 = free-flowing, 1.0 = gridlock.
        Computed as 1 - (current_speed / base_speed).
        """
        if self.current_speed is None or compact_eid < 0:
            return 0.0
        base = float(self.base_speed[compact_eid]) if self.base_speed is not None else 0.0
        if base <= 0:
            return 0.0
        current = float(self.current_speed[compact_eid])
        return max(0.0, min(1.0, 1.0 - current / base))


# Module-level singleton
graph_cache = GraphCache()
