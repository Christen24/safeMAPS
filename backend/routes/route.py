"""
Route computation API endpoints.

Bug 4 fix: uses explicit use_custom_weights flag instead of fragile float comparison.
Bug 5 fix: /compare runs all 4 A* searches concurrently via asyncio.gather.
"""

import asyncio

from fastapi import APIRouter, HTTPException

from models import RouteRequest, RouteResponse, CompareRoutesResponse, RouteProfile
from routing import find_route, get_profile_weights

router = APIRouter()


@router.post("", response_model=RouteResponse)
async def compute_route(request: RouteRequest):
    """
    Compute a health-and-safety-aware route between two points.

    The route minimizes: C_e = α·T_e + β·∫AQI(t)dt + γ·R_e

    - Set use_custom_weights=true to use slider values directly
    - Set use_custom_weights=false (default) to use profile presets
    """
    if request.use_custom_weights:
        alpha, beta, gamma = request.alpha, request.beta, request.gamma
    else:
        alpha, beta, gamma = get_profile_weights(request.profile)

    route = await find_route(
        origin_lat=request.origin.lat,
        origin_lon=request.origin.lon,
        dest_lat=request.destination.lat,
        dest_lon=request.destination.lon,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        profile=request.profile,
    )

    if not route:
        raise HTTPException(
            status_code=404,
            detail="No route found between the given points. "
                   "Ensure both points are within Bangalore's road network.",
        )

    return route


@router.get("/compare", response_model=CompareRoutesResponse)
async def compare_routes(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
):
    """
    Compare routes across all profiles (fastest, safest, healthiest, balanced).
    Bug 5 fix: runs all 4 A* searches concurrently instead of sequentially.
    """
    results = await asyncio.gather(*[
        find_route(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            alpha=a, beta=b, gamma=g,
            profile=p,
        )
        for p in RouteProfile
        for a, b, g in [get_profile_weights(p)]
    ], return_exceptions=True)

    routes = [r for r in results if r and not isinstance(r, Exception)]

    if not routes:
        raise HTTPException(
            status_code=404,
            detail="No routes found between the given points.",
        )

    return CompareRoutesResponse(routes=routes)
