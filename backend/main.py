"""
SafeMAPS — FastAPI Application Entry Point
Health & Safety Aware Routing for Bangalore
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import db
from routes.route import router as route_router
from routes.aqi import router as aqi_router
from routes.safety import router as safety_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifecycle — connect/disconnect DB pool."""
    await db.connect()
    yield
    await db.disconnect()


app = FastAPI(
    title="SafeMAPS API",
    description=(
        "Health & Safety Aware Routing Engine for Bangalore. "
        "Computes optimal routes minimizing a composite cost of "
        "travel time, AQI exposure, and accident risk."
    ),
    version="0.1.0",
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
app.include_router(aqi_router, prefix="/api/aqi", tags=["Air Quality"])
app.include_router(safety_router, prefix="/api/safety", tags=["Safety"])


# ─── Health Check ────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check():
    """Basic health check endpoint."""
    try:
        await db.fetchval("SELECT 1")
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    return {
        "status": "ok",
        "database": db_status,
        "version": "0.1.0",
    }
