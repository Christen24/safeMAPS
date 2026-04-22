"""
SafeMAPS — FastAPI Application Entry Point

Phase 2 addition: APScheduler runs inside the lifespan, firing two jobs
automatically without any external cron or process manager:

  · AQI scrape    — every 15 min → updates grid cells → refreshes edge_aqi
  · Traffic scrape — every 5 min  → updates road speeds → patches adjacency

The scheduler shares the existing asyncpg pool (via the db singleton).
No new connections are opened by the scheduler itself.

Startup order
─────────────
1. Connect DB pool
2. Load road graph into memory (graph_cache.load)
3. Start scheduler — first jobs fire 1–2 min later to let the
   server fully warm up before hitting external APIs
4. Yield (app serves requests)

Shutdown order
──────────────
1. Stop scheduler (wait=False — don't block shutdown)
2. Close DB pool
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import db
from graph_cache import graph_cache
from scheduler import start_scheduler, stop_scheduler
from routes.route import router as route_router
from routes.aqi import router as aqi_router
from routes.safety import router as safety_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────
    await db.connect()
    logger.info("Database pool connected.")

    node_count = await graph_cache.load(db)
    logger.info(f"Graph cache warmed: {node_count:,} nodes loaded.")

    # Start the background scheduler — AQI every 15 min, traffic every 5 min
    scheduler = start_scheduler()

    # ── App runs here ─────────────────────────────────────────────────
    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    stop_scheduler(scheduler)
    await db.disconnect()
    logger.info("Shutdown complete.")


app = FastAPI(
    title="SafeMAPS API",
    description=(
        "Health & Safety Aware Routing Engine for Bangalore. "
        "Computes optimal routes minimising a composite cost of "
        "travel time, AQI exposure, and accident risk."
    ),
    version="0.3.0",
    lifespan=lifespan,
)

# ─── CORS ─────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ──────────────────────────────────────────────────────────
app.include_router(route_router,   prefix="/api/route",  tags=["Routing"])
app.include_router(aqi_router,     prefix="/api/aqi",    tags=["Air Quality"])
app.include_router(safety_router,  prefix="/api/safety", tags=["Safety"])


# ─── Health check ─────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check():
    """
    Returns DB connectivity, graph cache status, and scheduler timing.

    The 'aqi_age_seconds' and 'graph_age_seconds' fields tell you
    how stale the in-memory data is — useful for diagnosing routes
    that aren't reflecting the latest AQI readings.
    """
    try:
        await db.fetchval("SELECT 1")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "ok",
        "version": "0.3.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": db_status,
        "graph": {
            "loaded": graph_cache.is_loaded,
            "nodes": graph_cache.node_count,
            "edges": graph_cache.edge_count,
            "age_seconds": round(graph_cache.age_seconds, 1),
        },
        "aqi_cache": {
            "edges_with_aqi": len(graph_cache.edge_aqi),
            "age_seconds": round(graph_cache.aqi_age_seconds, 1),
        },
        "scheduler": {
            "aqi_interval_minutes": 15,
            "traffic_interval_minutes": 5,
            "note": "Check logs for last run times",
        },
    }


# ─── Admin endpoints ──────────────────────────────────────────────────

@app.post("/api/admin/refresh-graph", tags=["Admin"])
async def refresh_graph():
    """
    Manually trigger a full graph reload from PostGIS.

    Use this after running osm_loader.py to import new road data.
    This reloads nodes, edges, AQI costs, and risk scores — it takes
    several seconds and temporarily blocks route requests that arrive
    during the reload. For AQI-only refresh, use /refresh-aqi instead.

    Protect this endpoint with a reverse-proxy auth rule in production.
    """
    node_count = await graph_cache.load(db)
    return {
        "status": "reloaded",
        "nodes": node_count,
        "edges": graph_cache.edge_count,
    }


@app.post("/api/admin/refresh-aqi", tags=["Admin"])
async def refresh_aqi():
    """
    Manually trigger an AQI edge-cost refresh without reloading the graph.

    This is the same operation the scheduler runs every 15 minutes.
    Use it to force-refresh after running aqi_scraper.py manually.
    Completes in ~2–5 seconds (one spatial join query).
    """
    await graph_cache.refresh_aqi_costs(db)
    return {
        "status": "refreshed",
        "edges_with_aqi": len(graph_cache.edge_aqi),
        "aqi_age_seconds": round(graph_cache.aqi_age_seconds, 1),
    }


@app.post("/api/admin/run-aqi-scrape", tags=["Admin"])
async def run_aqi_scrape():
    """
    Manually trigger a full AQI scrape cycle (fetch → interpolate → refresh).

    Equivalent to running: python data_pipeline/aqi_scraper.py --once
    followed by a cache refresh. Useful for testing the pipeline without
    waiting for the scheduler's next fire.
    """
    from scheduler import run_aqi_cycle
    await run_aqi_cycle()
    return {
        "status": "complete",
        "edges_with_aqi": len(graph_cache.edge_aqi),
        "aqi_age_seconds": round(graph_cache.aqi_age_seconds, 1),
    }


@app.post("/api/admin/run-traffic-scrape", tags=["Admin"])
async def run_traffic_scrape():
    """
    Manually trigger a traffic scrape cycle (fetch speeds → patch cache).

    Equivalent to running: python data_pipeline/traffic_ingestion.py --once
    followed by a speed update in the in-memory graph.
    """
    from scheduler import run_traffic_cycle
    await run_traffic_cycle()
    return {
        "status": "complete",
        "edges": graph_cache.edge_count,
    }
