"""
SafeMAPS — FastAPI Application Entry Point  (v0.4.0 — Phase 6: Green Score)
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import db
from graph_cache import graph_cache
from scheduler import start_scheduler, stop_scheduler
from routes.route import router as route_router
from routes.aqi import router as aqi_router
from routes.safety import router as safety_router
from routes.user import router as user_router        # Phase 6
from routes.incidents import router as incident_router  # Phase 7

logger = logging.getLogger(__name__)


# ── Admin security dependency ─────────────────────────────────────────

async def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
):
    """
    FastAPI dependency that guards all /api/admin/* endpoints.

    Rules:
    - If ADMIN_API_KEY is not set in .env → 503 (admin disabled)
    - If X-Admin-Key header is missing or wrong → 401
    - If key matches → request proceeds
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Admin endpoints are disabled. "
                "Set ADMIN_API_KEY in your .env to enable them."
            ),
        )
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Admin-Key header.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    logger.info("Database pool connected.")

    node_count = await graph_cache.load(db)
    logger.info(f"Graph cache warmed: {node_count:,} nodes loaded.")

    if not settings.admin_api_key:
        logger.warning(
            "ADMIN_API_KEY is not set — admin endpoints are disabled. "
            "Add ADMIN_API_KEY=<secret> to your .env to enable them."
        )

    scheduler = start_scheduler()

    yield

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
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(route_router,    prefix="/api/route",     tags=["Routing"])
app.include_router(aqi_router,      prefix="/api/aqi",       tags=["Air Quality"])
app.include_router(safety_router,   prefix="/api/safety",    tags=["Safety"])
app.include_router(user_router,     prefix="/api/user",      tags=["Green Score"])
app.include_router(incident_router, prefix="/api/incidents", tags=["Live Incidents"])


@app.get("/health", tags=["System"])
async def health_check():
    try:
        await db.fetchval("SELECT 1")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status":    "ok",
        "version":   "0.4.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database":  db_status,
        "graph": {
            "loaded":      graph_cache.is_loaded,
            "nodes":       graph_cache.node_count,
            "edges":       graph_cache.edge_count,
            "age_seconds": round(graph_cache.age_seconds, 1),
        },
        "aqi_cache": {
            "edges_with_aqi": len(graph_cache.edge_aqi),
            "age_seconds":    round(graph_cache.aqi_age_seconds, 1),
        },
        "incident_cache": {
            "edges_with_incidents": graph_cache.incident_count,
            "age_seconds":         round(graph_cache.incident_age_seconds, 1),
        },
        "scheduler": {
            "aqi_interval_minutes":      15,
            "cpcb_interval_minutes":     15,
            "traffic_interval_minutes":   5,
            "lstm_interval_minutes":     30,
            "incident_interval_minutes": 10,
        },
    }


@app.post("/api/admin/refresh-graph", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def refresh_graph():
    node_count = await graph_cache.load(db)
    return {"status": "reloaded", "nodes": node_count, "edges": graph_cache.edge_count}


@app.post("/api/admin/refresh-aqi", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def refresh_aqi():
    await graph_cache.refresh_aqi_costs(db)
    return {
        "status":           "refreshed",
        "edges_with_aqi":   len(graph_cache.edge_aqi),
        "aqi_age_seconds":  round(graph_cache.aqi_age_seconds, 1),
    }


@app.post("/api/admin/run-aqi-scrape", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def run_aqi_scrape():
    from scheduler import run_aqi_cycle
    await run_aqi_cycle()
    return {"status": "complete", "edges_with_aqi": len(graph_cache.edge_aqi)}


@app.post("/api/admin/run-traffic-scrape", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def run_traffic_scrape():
    from scheduler import run_traffic_cycle
    await run_traffic_cycle()
    return {"status": "complete", "edges": graph_cache.edge_count}


@app.post("/api/admin/expire-incidents", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def expire_incidents():
    """Manually mark all stale incidents as inactive. Useful for demo resets."""
    result = await db.execute("""
        UPDATE live_incidents
        SET is_active = FALSE
        WHERE is_active = TRUE
          AND expires_at < NOW();
    """)
    expired = int(result.split()[-1])
    await graph_cache.refresh_incident_costs(db)
    return {
        "status":  "expired",
        "expired": expired,
        "active_edges": graph_cache.incident_count,
    }
