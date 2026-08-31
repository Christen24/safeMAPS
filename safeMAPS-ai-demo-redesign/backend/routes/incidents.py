"""
SafeMAPS — Live Incidents API Router

Endpoints:
    GET /api/incidents/active        — all active incidents as GeoJSON FeatureCollection
    GET /api/incidents/active?type=  — filtered by incident_type (accident, closure, ...)
    GET /api/incidents/active?source=— filtered by source (osm, waze, twitter)
    POST /api/admin/expire-incidents — manually expire stale incidents (admin only)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query

from database import db
from graph_cache import graph_cache
from models import IncidentLayerResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Severity → icon color for frontend rendering
SEVERITY_COLOR = {1: "#f59e0b", 2: "#f97316", 3: "#ef4444"}
INCIDENT_ICON  = {
    "accident":     "accident",
    "closure":      "closure",
    "waterlogging": "water",
    "construction": "construction",
    "hazard":       "hazard",
}


@router.get("/active", response_model=IncidentLayerResponse)
async def get_active_incidents(
    incident_type: Optional[str] = Query(
        default=None,
        alias="type",
        description="Filter by incident type: accident, closure, waterlogging, construction, hazard",
    ),
    source: Optional[str] = Query(
        default=None,
        description="Filter by source: osm, waze, twitter",
    ),
    limit: int = Query(default=500, ge=1, le=2000),
) -> IncidentLayerResponse:
    """
    Return all currently active incidents as a GeoJSON FeatureCollection.

    Each feature includes:
        geometry: Point (lon, lat)
        properties: id, source, incident_type, severity, description,
                    reported_at, expires_at, color, icon
    """
    # Build WHERE clause dynamically
    conditions = ["is_active = TRUE", "expires_at > NOW()"]
    params: list = []

    if incident_type:
        params.append(incident_type.lower())
        conditions.append(f"incident_type = ${len(params)}")

    if source:
        params.append(source.lower())
        conditions.append(f"source = ${len(params)}")

    where = " AND ".join(conditions)
    params.append(limit)

    rows = await db.fetch(f"""
        SELECT
            id, source, incident_type, severity,
            description, lat, lon,
            reported_at AT TIME ZONE 'UTC' AS reported_at,
            expires_at  AT TIME ZONE 'UTC' AS expires_at
        FROM live_incidents
        WHERE {where}
        ORDER BY severity DESC, reported_at DESC
        LIMIT ${len(params)};
    """, *params)

    features = []
    for row in rows:
        sev   = int(row["severity"])
        itype = row["incident_type"] or "hazard"
        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [row["lon"], row["lat"]],
            },
            "properties": {
                "id":            row["id"],
                "source":        row["source"],
                "incident_type": itype,
                "severity":      sev,
                "description":   row["description"] or "",
                "reported_at":   row["reported_at"].isoformat() if row["reported_at"] else "",
                "expires_at":    row["expires_at"].isoformat()  if row["expires_at"]  else "",
                "color":         SEVERITY_COLOR.get(sev, "#f59e0b"),
                "icon":          INCIDENT_ICON.get(itype, "hazard"),
            },
        })

    return IncidentLayerResponse(
        type="FeatureCollection",
        features=features,
        total=len(features),
        as_of=datetime.now(timezone.utc).isoformat(),
        cache_age_seconds=round(graph_cache.incident_age_seconds, 1),
    )
