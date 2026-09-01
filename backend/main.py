"""
SafeMAPS — FastAPI Application Entry Point  (v0.5.0 — Phase 11: BiDir A* + PWA + PgBouncer)
"""

import asyncio
import time
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, JSONResponse

from config import settings
from database import db, DatabaseUnavailable
from graph_cache import graph_cache
from metrics import metrics
from scheduler import start_scheduler, stop_scheduler
from routes.route import router as route_router
from routes.aqi import router as aqi_router
from routes.safety import router as safety_router
from routes.user import router as user_router        # Phase 6
from routes.incidents import router as incident_router  # Phase 7
from routes.ai import router as ai_router

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
    import httpx
    # Expose a shared internal httpx client on the app for proxying to MCP
    app.state.http_client = httpx.AsyncClient(timeout=10.0)
    
    yield
    
    await app.state.http_client.aclose()
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


@app.exception_handler(DatabaseUnavailable)
async def database_unavailable_handler(request: Request, exc: DatabaseUnavailable):
    """
    Turns a not-connected DB pool into a clean 503 with a JSON `detail`
    body instead of Starlette's default plain-text 500 — so the frontend
    can actually show the user (and us) what's wrong instead of a
    generic "Internal Server Error" that isn't even valid JSON.
    """
    logger.error(f"{request.method} {request.url.path} failed: {exc}")
    return JSONResponse(status_code=503, content={"detail": str(exc)})

app.include_router(route_router,    prefix="/api/route",     tags=["Routing"])
app.include_router(aqi_router,      prefix="/api/aqi",       tags=["Air Quality"])
app.include_router(safety_router,   prefix="/api/safety",    tags=["Safety"])
app.include_router(user_router,     prefix="/api/user",      tags=["Green Score"])
app.include_router(incident_router, prefix="/api/incidents", tags=["Live Incidents"])
app.include_router(ai_router,       prefix="/api/ai",        tags=["AI Demo"])


@app.get("/health", tags=["System"])
async def health_check():
    import httpx
    mcp_url = settings.mcp_server_url
    if mcp_url.endswith("/mcp"):
        mcp_base = mcp_url[:-4]
    else:
        mcp_base = mcp_url
        
    try:
        res = await app.state.http_client.get(f"{mcp_base}/internal/status")
        graph_status = res.json() if res.status_code == 200 else {}
    except Exception:
        graph_status = {}

    return {
        "status":    "ok",
        "version":   "0.5.0",  # Phase 6: CPCB + Live Incidents
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database":  "managed by mcp",
        "graph": {
            "loaded":      graph_status.get("loaded", False),
            "nodes":       graph_status.get("nodes", 0),
            "edges":       graph_status.get("edges", 0),
            "age_seconds": graph_status.get("age_seconds", 0),
        },
        "aqi_cache": {
            "edges_with_aqi": graph_status.get("edges_with_aqi", 0),
            "age_seconds":    graph_status.get("aqi_age_seconds", 0),
        },
        "incident_cache": {
            "edges_with_incidents": graph_status.get("edges_with_incidents", 0),
            "age_seconds":         graph_status.get("incident_age_seconds", 0),
        },
        "scheduler": {
            "status": "managed by mcp",
        },
    }


@app.get("/metrics", tags=["System"], response_class=PlainTextResponse)
async def prometheus_metrics():
    """Prometheus-compatible metrics endpoint. Scrape at /metrics."""
    return metrics.to_prometheus()


def get_mcp_base():
    mcp_url = settings.mcp_server_url
    return mcp_url[:-4] if mcp_url.endswith("/mcp") else mcp_url

@app.post("/api/admin/refresh-graph", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def refresh_graph():
    res = await app.state.http_client.post(f"{get_mcp_base()}/internal/refresh-graph", timeout=60.0)
    return res.json()

@app.post("/api/admin/refresh-aqi", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def refresh_aqi():
    res = await app.state.http_client.post(f"{get_mcp_base()}/internal/refresh-aqi", timeout=60.0)
    return res.json()

@app.post("/api/admin/run-aqi-scrape", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def run_aqi_scrape():
    res = await app.state.http_client.post(f"{get_mcp_base()}/internal/run-aqi-scrape", timeout=60.0)
    return res.json()

@app.post("/api/admin/run-traffic-scrape", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def run_traffic_scrape():
    res = await app.state.http_client.post(f"{get_mcp_base()}/internal/run-traffic-scrape", timeout=60.0)
    return res.json()

@app.post("/api/admin/expire-incidents", tags=["Admin"], dependencies=[Depends(require_admin_key)])
async def expire_incidents():
    res = await app.state.http_client.post(f"{get_mcp_base()}/internal/expire-incidents", timeout=60.0)
    return res.json()
