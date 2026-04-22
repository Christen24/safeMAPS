"""
SafeMAPS — In-memory Road Graph Cache

Phase 2 additions (scheduler integration)
──────────────────────────────────────────
Two new methods let the scheduler refresh cost data without
reloading the full graph (~200 MB, several seconds):

  refresh_aqi_costs(db)
    Re-runs only the AQI spatial join query and updates self.edge_aqi.
    Called by the scheduler after every AQI scrape cycle (~15 min).
    The graph topology (nodes, adjacency, geometry) is unchanged.

  update_speeds(edge_speeds: dict[int, float])
    Receives a {edge_id: speed_kmh} dict from the traffic scraper
    and patches self.edge_data and self.adjacency in-place.
    Called by the scheduler after every traffic cycle (~5 min).
    Does not touch AQI or risk costs.

Original design (Phase 1)
──────────────────────────
get_road_graph() in spatial_queries.py executed two large SELECT
queries and rebuilt Python dicts from scratch on every A* request.
For Bangalore's OSM network (~400k nodes, ~500k edges) that means
~2 DB round-trips and ~500k dict insertions per route call.

This module fixes that by:
  1. Loading the graph ONCE at startup (graph_cache.load)
  2. Holding nodes/adjacency/edge_data in RAM (~200 MB)
  3. Pre-fetching edge AQI + risk into flat dicts for O(1) lookup
  4. Correctly honouring oneway=True edges

Usage
─────
from graph_cache import graph_cache

# startup:
await graph_cache.load(db)

# scheduler — AQI refresh (no graph reload):
await graph_cache.refresh_aqi_costs(db)

# scheduler — traffic speed sync (in-memory patch):
graph_cache.update_speeds({edge_id: speed_kmh, ...})

# routing:
nodes      = graph_cache.nodes
adjacency  = graph_cache.adjacency
edge_data  = graph_cache.edge_data
aqi        = graph_cache.get_aqi(edge_id)
risk       = graph_cache.get_risk(edge_id)
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class GraphCache:
    """Singleton holding the in-memory road graph for Bangalore."""

    def __init__(self):
        self.nodes: dict[int, tuple[float, float]] = {}
        self.adjacency: dict[int, list[tuple]] = {}
        self.edge_data: dict[int, dict] = {}
        self.edge_aqi: dict[int, float] = {}
        self.edge_risk: dict[int, float] = {}
        self._loaded_at: Optional[float] = None
        self._aqi_refreshed_at: Optional[float] = None

    # ── Properties ────────────────────────────────────────────────────

    @property
    def is_loaded(self) -> bool:
        return self._loaded_at is not None

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edge_data)

    @property
    def age_seconds(self) -> float:
        if self._loaded_at is None:
            return float("inf")
        return time.monotonic() - self._loaded_at

    @property
    def aqi_age_seconds(self) -> float:
        if self._aqi_refreshed_at is None:
            return float("inf")
        return time.monotonic() - self._aqi_refreshed_at

    # ── Full graph load (called once at startup) ───────────────────────

    async def load(self, db) -> int:
        """
        Load the full road graph from PostGIS.
        Runs once at startup; also callable from the admin endpoint.
        Returns the number of nodes loaded.
        """
        t0 = time.monotonic()
        logger.info("Loading road graph from PostGIS...")

        # ── Nodes ─────────────────────────────────────────────────────
        node_rows = await db.fetch(
            "SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM road_nodes;"
        )
        nodes: dict[int, tuple[float, float]] = {
            row["id"]: (row["lat"], row["lon"]) for row in node_rows
        }

        if not nodes:
            logger.warning(
                "road_nodes is empty — run: cd data_pipeline && python osm_loader.py"
            )

        # ── Edges + adjacency ─────────────────────────────────────────
        edge_rows = await db.fetch("""
            SELECT
                id,
                source_node,
                target_node,
                road_name,
                road_type,
                length_m,
                speed_kmh,
                oneway,
                ST_AsGeoJSON(geom) AS geometry
            FROM road_segments;
        """)

        adjacency: dict[int, list[tuple]] = {}
        edge_data: dict[int, dict] = {}

        for row in edge_rows:
            eid = row["id"]
            src = row["source_node"]
            tgt = row["target_node"]
            length_m = float(row["length_m"] or 0)
            speed_kmh = float(row["speed_kmh"] or 30)
            oneway = bool(row["oneway"])

            # Forward direction (always)
            adjacency.setdefault(src, []).append((tgt, eid, length_m, speed_kmh))

            # Reverse only for bidirectional roads
            if not oneway:
                adjacency.setdefault(tgt, []).append((src, eid, length_m, speed_kmh))

            geom_str = row["geometry"]
            geom = (
                json.loads(geom_str)
                if geom_str
                else {"type": "LineString", "coordinates": []}
            )
            edge_data[eid] = {
                "geometry": geom,
                "length_m": length_m,
                "speed_kmh": speed_kmh,
                "road_name": row["road_name"],
                "road_type": row["road_type"],
            }

        elapsed = time.monotonic() - t0
        logger.info(
            f"Graph loaded in {elapsed:.1f}s: "
            f"{len(nodes):,} nodes, {len(edge_data):,} edges, "
            f"{sum(len(v) for v in adjacency.values()):,} adjacency entries"
        )

        # Commit atomically so routing never sees a half-loaded state
        self.nodes = nodes
        self.adjacency = adjacency
        self.edge_data = edge_data
        self._loaded_at = time.monotonic()

        # Reset cost caches — will be filled by _prefetch_edge_costs
        self.edge_aqi = {}
        self.edge_risk = {}

        await self._prefetch_edge_costs(db)
        return len(nodes)

    # ── Full cost prefetch (called after load) ─────────────────────────

    async def _prefetch_edge_costs(self, db) -> None:
        """
        Bulk-load AQI + risk for all edges in 2 queries.
        Falls back gracefully if tables are empty.
        """
        t0 = time.monotonic()
        logger.info("Pre-fetching edge AQI and risk costs...")

        # AQI — spatial join with grid cells
        try:
            aqi_rows = await db.fetch("""
                SELECT
                    e.id  AS edge_id,
                    COALESCE(AVG(g.aqi_value), 50.0) AS avg_aqi
                FROM road_segments e
                LEFT JOIN grid_cells g ON ST_Intersects(e.geom, g.geom)
                GROUP BY e.id;
            """)
            self.edge_aqi = {
                row["edge_id"]: float(row["avg_aqi"]) for row in aqi_rows
            }
            logger.info(f"AQI loaded for {len(self.edge_aqi):,} edges.")
        except Exception as exc:
            logger.warning(f"AQI prefetch failed (grid_cells empty?): {exc}")
            self.edge_aqi = {}

        # Risk — proximity to accident blackspots
        try:
            risk_rows = await db.fetch("""
                SELECT
                    e.id AS edge_id,
                    COALESCE(
                        SUM(
                            b.severity_weight /
                            GREATEST(
                                ST_Distance(e.geom::geography, b.geom::geography),
                                1.0
                            )
                        ),
                        0.0
                    ) AS risk_score
                FROM road_segments e
                LEFT JOIN accident_blackspots b
                    ON ST_DWithin(e.geom::geography, b.geom::geography, 200)
                GROUP BY e.id;
            """)
            self.edge_risk = {
                row["edge_id"]: float(row["risk_score"]) for row in risk_rows
            }
            logger.info(f"Risk loaded for {len(self.edge_risk):,} edges.")
        except Exception as exc:
            logger.warning(f"Risk prefetch failed (blackspots empty?): {exc}")
            self.edge_risk = {}

        self._aqi_refreshed_at = time.monotonic()
        elapsed = time.monotonic() - t0
        logger.info(f"Edge cost prefetch complete in {elapsed:.1f}s.")

    # ── Phase 2: AQI-only refresh (called by scheduler every 15 min) ──

    async def refresh_aqi_costs(self, db) -> None:
        """
        Re-fetch only the edge → AQI mapping from PostGIS.

        This is called by the scheduler after each AQI scrape cycle.
        It skips the full graph reload (nodes, edges, risk) and only
        replaces self.edge_aqi. The operation is atomic — routing
        continues reading the old dict until the assignment completes.

        Timing: ~2–5 seconds for ~500k edges (one spatial join query).
        """
        t0 = time.monotonic()
        logger.info("[cache] Refreshing edge AQI costs from updated grid...")

        try:
            aqi_rows = await db.fetch("""
                SELECT
                    e.id  AS edge_id,
                    COALESCE(AVG(g.aqi_value), 50.0) AS avg_aqi
                FROM road_segments e
                LEFT JOIN grid_cells g ON ST_Intersects(e.geom, g.geom)
                GROUP BY e.id;
            """)

            # Build the new dict before assigning — routing reads the
            # old dict right up until this single assignment completes.
            new_aqi = {row["edge_id"]: float(row["avg_aqi"]) for row in aqi_rows}
            self.edge_aqi = new_aqi
            self._aqi_refreshed_at = time.monotonic()

            elapsed = time.monotonic() - t0
            logger.info(
                f"[cache] AQI refresh complete in {elapsed:.1f}s "
                f"({len(new_aqi):,} edges updated)."
            )

        except Exception as exc:
            # Keep stale AQI values rather than clearing them to zero.
            logger.warning(
                f"[cache] AQI refresh failed — keeping previous values. "
                f"Error: {exc}"
            )

    # ── Phase 2: Speed patch (called by scheduler every 5 min) ────────

    def update_speeds(self, edge_speeds: dict[int, float]) -> None:
        """
        Patch self.edge_data and self.adjacency with fresh speed_kmh values.

        Called by the traffic scheduler after it writes updated speeds
        to road_segments in PostGIS. This keeps the in-memory graph
        consistent with the DB without a full reload.

        Parameters
        ──────────
        edge_speeds : {edge_id: speed_kmh}
            Dict returned by the updated traffic_ingestion.scrape_traffic().
            Only contains edges that actually got a new reading this cycle.

        Implementation note
        ────────────────────
        The adjacency list stores tuples of (neighbour, edge_id, length_m,
        speed_kmh). Since tuples are immutable, we rebuild the neighbour
        list for each affected source node. This is done per-node rather
        than per-edge to avoid scanning the entire adjacency dict.

        For 100 edges updated per traffic cycle the scan is negligible —
        we track which nodes to rebuild via a set then do one pass.
        """
        if not edge_speeds:
            return

        affected_eids = set(edge_speeds.keys())

        # ── Patch edge_data ───────────────────────────────────────────
        patched_count = 0
        for eid, new_speed in edge_speeds.items():
            if eid in self.edge_data:
                self.edge_data[eid]["speed_kmh"] = new_speed
                patched_count += 1

        # ── Patch adjacency list ──────────────────────────────────────
        # Find all source nodes whose neighbour lists contain a patched edge.
        # We rebuild those lists in-place using a new tuple for each entry.
        nodes_to_rebuild: set[int] = set()
        for node_id, neighbours in self.adjacency.items():
            for nbr, eid, _len, _spd in neighbours:
                if eid in affected_eids:
                    nodes_to_rebuild.add(node_id)
                    break  # one match per node is enough

        for node_id in nodes_to_rebuild:
            self.adjacency[node_id] = [
                (nbr, eid, length_m, edge_speeds.get(eid, old_spd))
                for nbr, eid, length_m, old_spd in self.adjacency[node_id]
            ]

        logger.info(
            f"[cache] Speed patch: {patched_count} edges, "
            f"{len(nodes_to_rebuild)} adjacency nodes rebuilt."
        )

    # ── Accessors ─────────────────────────────────────────────────────

    def get_aqi(self, edge_id: int) -> float:
        """Return cached AQI for an edge, defaulting to 50 (moderate)."""
        return self.edge_aqi.get(edge_id, 50.0)

    def get_risk(self, edge_id: int) -> float:
        """Return cached risk score for an edge, defaulting to 0."""
        return self.edge_risk.get(edge_id, 0.0)


# Module-level singleton — import this everywhere
graph_cache = GraphCache()
