"""
Safety / Accident Blackspot API endpoints.
"""

from fastapi import APIRouter, Query

from spatial_queries import get_blackspots_in_bbox

router = APIRouter()


@router.get("/blackspots")
async def get_blackspots(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
):
    """
    Get accident blackspot locations within a bounding box.

    Returns a GeoJSON FeatureCollection with severity and accident counts.
    """
    blackspots = await get_blackspots_in_bbox(min_lat, max_lat, min_lon, max_lon)

    features = []
    for b in blackspots:
        features.append({
            "type": "Feature",
            "properties": {
                "id": b["id"],
                "severity": b["severity"],
                "total_accidents": b["total_accidents"],
                "fatal_accidents": b["fatal_accidents"],
                "description": b.get("description"),
            },
            "geometry": {
                "type": "Point",
                "coordinates": [b["lon"], b["lat"]],
            },
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "bbox": [min_lon, min_lat, max_lon, max_lat],
            "blackspot_count": len(features),
        },
    }
