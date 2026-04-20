"""
SafeMAPS — In-memory Road Graph Cache

This module is the most important Phase 1 fix.

Problem in the original code
─────────────────────────────
get_road_graph() in spatial_queries.py executed two large SELECT queries
(all nodes + all edges) and rebuilt Python dicts from scratch on every
single A* route request. For Bangalore's OSM network (~400k nodes,
~500k edges) that means:
  - ~2 DB round-trips per route call
  - ~500k dict insertions per route call
  - All of this inside the async event loop, blocking concurrent requests

This module fixes that by:
  1. Loading the graph ONCE at app startup (called from main.py lifespan)
  2. Holding the result in module-level dicts (fast O(1) lookup)
  3. Pre-computing edge AQI and risk arrays so A* never hits the DB
     for individual edges (done lazily after first route, refreshed hourly)
  4. Correctly handling oneway=True edges — the original code added every
     edge in both directions, routing cars the wrong way down one-ways

Usage
─────
from graph_cache import graph_cache

# In lifespan:
await graph_cache.load(db)

# In routing:
nodes      = graph_cache.nodes       # {node_id: (lat, lon)}
adjacency  = graph_cache.adjacency   # {node_id: [(nbr, edge_id, len_m, spd_kmh)]}
edge_data  = graph_cache.edge_data   # {edge_id: {geometry, length_m, speed_kmh}}
edge_aqi   = graph_cache.edge_aqi    # {edge_id: float}   — pre-fetched
edge_risk  = graph_cache.edge_risk   # {edge_id: float}   — pre-fetched
"""

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

    async def load(self, db) -> int:
        """
        Load (or reload) the full road graph from PostGIS.

        Returns the number of nodes loaded.

        This replaces the old get_road_graph() call that happened inside
        the A* search on every request. Now it runs once at startup and
        whenever an admin triggers a refresh.

        One-way fix
        ───────────
        The original code treated all roads as bidirectional by always
        appending a reverse edge. OSM uses oneway=True to mark roads
        where travel in reverse is prohibited. We now read that flag and
        only add the reverse direction when oneway is False.
        """
        t0 = time.monotonic()
        logger.info("Loading road graph from PostGIS...")

        # ── Step 1: Load nodes ────────────────────────────────────────
        node_rows = await db.fetch(
            "SELECT id, ST_Y(geom) AS lat, ST_X(geom) AS lon FROM road_nodes;"
        )
        nodes: dict[int, tuple[float, float]] = {
            row["id"]: (row["lat"], row["lon"]) for row in node_rows
        }

        if not nodes:
            logger.warning(
                "road_nodes table is empty. Run the OSM loader first:\n"
                "  cd data_pipeline && python osm_loader.py"
            )

        # ── Step 2: Load edges + build adjacency ─────────────────────
        edge_rows = await db.fetch("""
            SELECT
                id,
                source_node,
                target_node,
                length_m,
                speed_kmh,
                oneway,
                ST_AsGeoJSON(geom) AS geometry
            FROM road_segments;
        """)

        import json

        adjacency: dict[int, list[tuple]] = {}
        edge_data: dict[int, dict] = {}

        for row in edge_rows:
            eid = row["id"]
            src = row["source_node"]
            tgt = row["target_node"]
            length_m = float(row["length_m"] or 0)
            speed_kmh = float(row["speed_kmh"] or 30)
            oneway = bool(row["oneway"])  # Phase 1 fix: respect oneway flag

            # Forward direction (always)
            adjacency.setdefault(src, []).append((tgt, eid, length_m, speed_kmh))

            # Reverse direction — only for bidirectional roads
            if not oneway:
                adjacency.setdefault(tgt, []).append((src, eid, length_m, speed_kmh))

            # Store edge metadata
            geom_str = row["geometry"]
            geom = json.loads(geom_str) if geom_str else {"type": "LineString", "coordinates": []}
            edge_data[eid] = {
                "geometry": geom,
                "length_m": length_m,
                "speed_kmh": speed_kmh,
            }

        elapsed = time.monotonic() - t0
        logger.info(
            f"Graph loaded in {elapsed:.1f}s: "
            f"{len(nodes):,} nodes, {len(edge_data):,} edges, "
            f"{sum(len(v) for v in adjacency.values()):,} adjacency entries"
        )

        # ── Step 3: Commit to module state ───────────────────────────
        self.nodes = nodes
        self.adjacency = adjacency
        self.edge_data = edge_data
        self._loaded_at = time.monotonic()

        # Reset per-edge caches so they get re-fetched from fresh DB data
        self.edge_aqi = {}
        self.edge_risk = {}

        # ── Step 4: Pre-fetch AQI + risk for all edges ───────────────
        # This runs after the graph is live so the app can start serving
        # requests immediately with fallback values, while this fills in.
        await self._prefetch_edge_costs(db)

        return len(nodes)

    async def _prefetch_edge_costs(self, db) -> None:
        """
        Pre-load AQI and risk values for every edge into memory.

        This replaces the per-edge DB calls that the original A* made
        inside the search loop. Instead of N DB round-trips (one per edge
        visited), we do 2 bulk queries at cache load time.

        Falls back gracefully if tables are empty (pre-data-pipeline state).
        """
        t0 = time.monotonic()
        logger.info("Pre-fetching edge AQI values...")

        try:
            aqi_rows = await db.fetch("""
                SELECT
                    e.id AS edge_id,
                    COALESCE(AVG(g.aqi_value), 50.0) AS avg_aqi
                FROM road_segments e
                LEFT JOIN grid_cells g ON ST_Intersects(e.geom, g.geom)
                GROUP BY e.id;
            """)
            self.edge_aqi = {row["edge_id"]: float(row["avg_aqi"]) for row in aqi_rows}
            logger.info(f"Loaded AQI for {len(self.edge_aqi):,} edges.")
        except Exception as exc:
            logger.warning(f"AQI prefetch failed (grid_cells empty?): {exc}")
            self.edge_aqi = {}

        try:
            risk_rows = await db.fetch("""
                SELECT
                    e.id AS edge_id,
                    COALESCE(
                        SUM(
                            b.severity_weight /
                            GREATEST(ST_Distance(e.geom::geography, b.geom::geography), 1.0)
                        ),
                        0.0
                    ) AS risk_score
                FROM road_segments e
                LEFT JOIN accident_blackspots b
                    ON ST_DWithin(e.geom::geography, b.geom::geography, 200)
                GROUP BY e.id;
            """)
            self.edge_risk = {row["edge_id"]: float(row["risk_score"]) for row in risk_rows}
            logger.info(f"Loaded risk scores for {len(self.edge_risk):,} edges.")
        except Exception as exc:
            logger.warning(f"Risk prefetch failed (accident_blackspots empty?): {exc}")
            self.edge_risk = {}

        elapsed = time.monotonic() - t0
        logger.info(f"Edge cost prefetch complete in {elapsed:.1f}s.")

    def get_aqi(self, edge_id: int) -> float:
        """Return cached AQI for an edge, defaulting to 50 (moderate)."""
        return self.edge_aqi.get(edge_id, 50.0)

    def get_risk(self, edge_id: int) -> float:
        """Return cached risk score for an edge, defaulting to 0."""
        return self.edge_risk.get(edge_id, 0.0)


# Module-level singleton — import this everywhere
graph_cache = GraphCache()
