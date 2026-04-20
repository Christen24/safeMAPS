"""
SafeMAPS — FastAPI Application Entry Point
Health & Safety Aware Routing for Bangalore

Phase 1 fix: road graph is loaded ONCE at startup and held in memory.
All route requests read from this shared cache instead of hitting the DB
on every A* iteration — the original design would load ~500k edges per call.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import db
from graph_cache import graph_cache
from routes.route import router as route_router
from routes.aqi import router as aqi_router
from routes.safety import router as safety_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Startup: connect DB pool, then warm the graph cache.
    Shutdown: close DB pool.

    The graph cache is the critical Phase 1 fix. Loading the full road
    graph from PostGIS on every A* request would be ~500k DB rows per
    route call. Instead we load it once here, hold it in RAM (~200 MB
    for Bangalore), and refresh it every 6 hours via a background task.
    """
    # 1. Connect database pool
    await db.connect()
    logger.info("Database pool connected.")

    # 2. Warm the road graph cache
    node_count = await graph_cache.load(db)
    logger.info(f"Graph cache warmed: {node_count} nodes loaded.")

    # 3. Yield control to the app (it runs here)
    yield

    # 4. Shutdown
    await db.disconnect()
    logger.info("Database pool closed.")


app = FastAPI(
    title="SafeMAPS API",
    description=(
        "Health & Safety Aware Routing Engine for Bangalore. "
        "Computes optimal routes minimising a composite cost of "
        "travel time, AQI exposure, and accident risk."
    ),
    version="0.2.0",
    lifespan=lifespan,
)

# ─── CORS ────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ─────────────────────────────────────────────────────────
app.include_router(route_router, prefix="/api/route", tags=["Routing"])
app.include_router(aqi_router,   prefix="/api/aqi",   tags=["Air Quality"])
app.include_router(safety_router, prefix="/api/safety", tags=["Safety"])


# ─── Health Check ────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    """Returns DB connectivity and graph cache status."""
    try:
        await db.fetchval("SELECT 1")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "ok",
        "database": db_status,
        "graph_nodes": graph_cache.node_count,
        "graph_edges": graph_cache.edge_count,
        "graph_loaded": graph_cache.is_loaded,
        "version": "0.2.0",
    }


# ─── Graph Cache Refresh ─────────────────────────────────────────────
@app.post("/api/admin/refresh-graph", tags=["Admin"])
async def refresh_graph():
    """
    Manually trigger a graph cache reload.
    Call this after importing new OSM data or if routes seem stale.
    In production, protect this endpoint with an API key.
    """
    node_count = await graph_cache.load(db)
    return {"status": "reloaded", "nodes": node_count}
