"""
Route computation API endpoints.
"""

from fastapi import APIRouter, HTTPException

from models import RouteRequest, RouteResponse, CompareRoutesResponse, RouteProfile
from routing import find_route, get_profile_weights

router = APIRouter()


@router.post("", response_model=RouteResponse)
async def compute_route(request: RouteRequest):
    """
    Compute a health-and-safety-aware route between two points.

    The route minimizes: C_e = α·T_e + β·∫AQI(t)dt + γ·R_e

    You can either:
    - Use a preset profile (fastest, safest, healthiest, balanced)
    - Provide custom α, β, γ weights
    """
    # Use profile weights or custom weights
    if request.alpha == 0.4 and request.beta == 0.3 and request.gamma == 0.3:
        alpha, beta, gamma = get_profile_weights(request.profile)
    else:
        alpha, beta, gamma = request.alpha, request.beta, request.gamma

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
    Returns up to 4 different route options.
    """
    routes = []

    for profile in RouteProfile:
        alpha, beta, gamma = get_profile_weights(profile)
        route = await find_route(
            origin_lat=origin_lat,
            origin_lon=origin_lon,
            dest_lat=dest_lat,
            dest_lon=dest_lon,
            alpha=alpha,
            beta=beta,
            gamma=gamma,
            profile=profile,
        )
        if route:
            routes.append(route)

    if not routes:
        raise HTTPException(
            status_code=404,
            detail="No routes found between the given points.",
        )

    return CompareRoutesResponse(routes=routes)
