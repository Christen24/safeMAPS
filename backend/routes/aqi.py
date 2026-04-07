"""
Air Quality Index (AQI) API endpoints.
"""

from fastapi import APIRouter, Query

from spatial_queries import get_aqi_heatmap

router = APIRouter()


@router.get("/heatmap")
async def aqi_heatmap(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
):
    """
    Get AQI grid cells within a bounding box for heatmap overlay.

    Returns a GeoJSON FeatureCollection of grid cells with AQI values.
    Each cell is a ~100m × 100m polygon with an interpolated AQI reading.
    """
    cells = await get_aqi_heatmap(min_lat, max_lat, min_lon, max_lon)

    features = []
    for cell in cells:
        features.append({
            "type": "Feature",
            "properties": {
                "cell_id": cell["id"],
                "aqi": cell["aqi_value"],
                "center_lat": cell["center_lat"],
                "center_lon": cell["center_lon"],
            },
            "geometry": cell["geometry"],
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "bbox": [min_lon, min_lat, max_lon, max_lat],
            "cell_count": len(features),
        },
    }
