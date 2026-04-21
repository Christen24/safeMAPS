"""
Pydantic models for SafeMAPS API requests and responses.
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


# ─── Enums ───────────────────────────────────────────────────────────

class RouteProfile(str, Enum):
    """Predefined routing profiles."""
    FASTEST = "fastest"
    SAFEST = "safest"
    HEALTHIEST = "healthiest"
    BALANCED = "balanced"


# ─── Request Models ──────────────────────────────────────────────────

class Coordinate(BaseModel):
    """A geographic coordinate."""
    lat: float = Field(..., ge=-90, le=90, description="Latitude")
    lon: float = Field(..., ge=-180, le=180, description="Longitude")


class RouteRequest(BaseModel):
    """Request body for computing a route."""
    origin: Coordinate
    destination: Coordinate
    profile: RouteProfile = RouteProfile.BALANCED
    alpha: float = Field(default=0.4, ge=0, le=1, description="Weight for travel time")
    beta: float = Field(default=0.3, ge=0, le=1, description="Weight for AQI exposure")
    gamma: float = Field(default=0.3, ge=0, le=1, description="Weight for accident risk")
    use_custom_weights: bool = Field(default=False, description="If true, use alpha/beta/gamma directly instead of profile presets")


class BoundingBoxRequest(BaseModel):
    """Bounding box for spatial queries."""
    min_lat: float = Field(..., ge=-90, le=90)
    max_lat: float = Field(..., ge=-90, le=90)
    min_lon: float = Field(..., ge=-180, le=180)
    max_lon: float = Field(..., ge=-180, le=180)


# ─── Response Models ─────────────────────────────────────────────────

class CostBreakdown(BaseModel):
    """Breakdown of cost components for a route."""
    total_cost: float
    travel_time_cost: float
    aqi_exposure_cost: float
    accident_risk_cost: float
    travel_time_minutes: float
    distance_km: float
    avg_aqi: float
    max_aqi: float
    accident_hotspots_passed: int


class SegmentInfo(BaseModel):
    """Metadata for a single route segment."""
    edge_id: int
    road_name: Optional[str] = None
    length_m: float
    travel_time_s: float
    aqi_value: float
    risk_score: float
    segment_cost: float
    geometry: dict  # GeoJSON LineString


class RouteResponse(BaseModel):
    """Response from the routing endpoint."""
    route_id: str
    profile: RouteProfile
    cost_breakdown: CostBreakdown
    geometry: dict  # GeoJSON LineString (full route)
    segments: list[SegmentInfo]
    weights_used: dict  # {"alpha": ..., "beta": ..., "gamma": ...}


class CompareRoutesResponse(BaseModel):
    """Response comparing multiple route profiles."""
    routes: list[RouteResponse]


# ─── Data Models ─────────────────────────────────────────────────────

class AQIReading(BaseModel):
    """An AQI measurement at a location."""
    station_id: str
    station_name: Optional[str] = None
    lat: float
    lon: float
    aqi: float
    pm25: Optional[float] = None
    pm10: Optional[float] = None
    no2: Optional[float] = None
    timestamp: str


class GridCellAQI(BaseModel):
    """AQI value for a grid cell (used for heatmap)."""
    cell_id: int
    center_lat: float
    center_lon: float
    aqi: float
    geometry: dict  # GeoJSON Polygon


class AccidentBlackspot(BaseModel):
    """An accident blackspot location."""
    id: int
    lat: float
    lon: float
    severity: str
    total_accidents: int
    fatal_accidents: int
    description: Optional[str] = None


class HealthStatus(BaseModel):
    """Health check response."""
    status: str = "ok"
    database: str = "connected"
    version: str = "0.1.0"
