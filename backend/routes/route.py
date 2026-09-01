"""
Route computation API endpoints.

Bug 4 fix: uses explicit use_custom_weights flag instead of fragile float comparison.
Bug 5 fix: /compare runs all 4 A* searches concurrently via asyncio.gather.
"""

import asyncio
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException

from models import RouteRequest, RouteResponse, CompareRoutesResponse, RouteProfile
from routing import find_route, get_profile_weights

router = APIRouter()
BANGALORE_TZ = timezone(timedelta(hours=5, minutes=30))


def _parse_hour(departure_time: str | None) -> int | None:
    """Parse ISO-8601 input and return the Bangalore local hour."""
    if not departure_time:
        return None

    try:
        normalized = departure_time.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="departure_time must be an ISO-8601 datetime string.",
        ) from exc

    if parsed.tzinfo is None:
        return parsed.hour
    return parsed.astimezone(BANGALORE_TZ).hour


@router.post("", response_model=RouteResponse)
async def compute_route(request: RouteRequest):
    """
    Compute a health-and-safety-aware route between two points.
    Uses the MCP server as the single source of truth for routing.
    """
    from mcp_client import mcp_client
    
    args = {
        "origin_lat": request.origin.lat,
        "origin_lon": request.origin.lon,
        "dest_lat": request.destination.lat,
        "dest_lon": request.destination.lon,
        "profile": request.profile,
    }
    if request.departure_time:
        args["departure_time"] = request.departure_time

    try:
        res = await mcp_client.call_tool("get_safe_route", args)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to communicate with MCP routing server: {exc}"
        )

    if "error" in res:
        err = res["error"]
        if "No route found" in err:
            raise HTTPException(status_code=404, detail=err)
        raise HTTPException(status_code=422, detail=err)

    return res


@router.get("/compare", response_model=CompareRoutesResponse)
async def compare_routes(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
    departure_time: str | None = None,
):
    """
    Compare routes across all profiles (fastest, safest, healthiest, balanced).
    Uses the MCP server as the single source of truth for routing.
    """
    from mcp_client import mcp_client
    
    args = {
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
        "dest_lat": dest_lat,
        "dest_lon": dest_lon,
    }
    if departure_time:
        args["departure_time"] = departure_time

    # FastMCP tool returns a dict with "routes" or "error"
    try:
        res = await mcp_client.call_tool("compare_route_profiles", args)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to communicate with MCP routing server: {exc}"
        )

    if "error" in res:
        # The original MCP tool returns string error messages
        err = res["error"]
        if "No road found" in err:
            raise HTTPException(status_code=422, detail=err)
        raise HTTPException(status_code=404, detail=err)

    if "routes" not in res or not res["routes"]:
        raise HTTPException(
            status_code=404,
            detail="No routes found between the points."
        )
        
    return CompareRoutesResponse(routes=res["routes"])
