"""
SafeMAPS — User / Green Score API Endpoints

Three endpoints:

  POST /api/user/trips
    Called by the frontend immediately after a route is computed and
    the user accepts it. Stores the trip + baseline in trip_history,
    then recomputes and caches the monthly Green Score.

  GET /api/user/green-score
    Returns the current month's Green Score and stat breakdown for a
    session. Reads from green_score_cache (< 5ms). Falls back to a
    live aggregate if the cache is cold.

  GET /api/user/trips
    Returns the last 30 trips for a session, ordered newest first.
    Used by the GreenScore.jsx history list.

Identity model
──────────────
No auth is required. The frontend generates a random UUID on first
load, stores it in localStorage as `safemaps_session_id`, and sends
it in the X-Session-ID header on every request. This is intentionally
simple — it's enough for a per-device Green Score without requiring
login.

Green Score formula
────────────────────
    raw = (
        0.5 × (aqi_integral_saved / baseline_aqi_integral)
      + 0.3 × (hotspots_avoided / max(baseline_hotspots, 1))
      + 0.2 × min(1, time_delta_min / baseline_time_min)  # time penalty
    ) × 100

Clipped to [0, 100]. A score of 100 means every trip this month
avoided all pollution and risk compared to taking the fastest route.
A score of 0 means no benefit was achieved (fastest routes taken).
"""

from datetime import datetime, timezone, date
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from database import db
from routing import find_route, get_profile_weights
from models import RouteProfile

router = APIRouter()


# ── Request / response models ─────────────────────────────────────────

class TripRecord(BaseModel):
    """Sent by the frontend after a route is accepted."""
    origin_lat:           float
    origin_lon:           float
    dest_lat:             float
    dest_lon:             float
    profile:              str
    distance_km:          float
    travel_time_min:      float
    avg_aqi:              float
    aqi_exposure_integral: float
    hotspots_passed:      int = 0


class GreenScoreResponse(BaseModel):
    session_id:       str
    month:            str           # "YYYY-MM"
    green_score:      float         # 0–100
    total_trips:      int
    total_km:         float
    aqi_saved_total:  float
    pm25_ug_saved:    float
    hotspots_avoided: int
    time_delta_min:   float
    grade:            str           # "Excellent" / "Good" / "Fair" / "Getting started"
    tip:              str


class TripSummary(BaseModel):
    id:                 int
    profile:            str
    distance_km:        float
    travel_time_min:    float
    avg_aqi:            float
    aqi_integral_saved: float
    hotspots_avoided:   int
    pm25_ug_avoided:    float
    green_score_delta:  float       # contribution to monthly score
    created_at:         str


# ── Helpers ───────────────────────────────────────────────────────────

def _validate_session(session_id: Optional[str]) -> str:
    if not session_id or len(session_id) < 8:
        raise HTTPException(
            status_code=400,
            detail="X-Session-ID header is required (min 8 chars). "
                   "Generate a UUID on first app load and persist it.",
        )
    return session_id.strip()


def _compute_green_score(
    aqi_saved: float,
    baseline_aqi: float,
    hotspots_avoided: int,
    baseline_hotspots: int,
    time_delta: float,
    baseline_time: float,
) -> float:
    """
    Compute a single-trip Green Score contribution (0–100).

    Weights:
      50% — AQI exposure reduction vs fastest baseline
      30% — accident hotspot avoidance
      20% — time penalty/bonus (faster than baseline = bonus)
    """
    if baseline_aqi <= 0:
        aqi_component = 0.0
    else:
        aqi_component = min(1.0, max(0.0, aqi_saved / baseline_aqi))

    if baseline_hotspots <= 0:
        hotspot_component = 1.0   # no hotspots on either route = full marks
    else:
        hotspot_component = min(1.0, max(0.0, hotspots_avoided / baseline_hotspots))

    if baseline_time <= 0:
        time_component = 0.0
    else:
        time_component = min(1.0, max(-0.5, time_delta / baseline_time))

    raw = (
        0.50 * aqi_component
        + 0.30 * hotspot_component
        + 0.20 * time_component
    ) * 100

    return round(max(0.0, min(100.0, raw)), 1)


def _grade_and_tip(score: float, trips: int) -> tuple[str, str]:
    """Return a human-readable grade label and an actionable tip."""
    if trips == 0:
        return "Getting started", "Compute your first route to earn a Green Score!"
    if score >= 80:
        return "Excellent 🌿", "You're a SafeMAPS champion — keep choosing healthy routes!"
    if score >= 60:
        return "Good 👍", "Try the Healthiest profile to push your score above 80."
    if score >= 40:
        return "Fair 🟡", "Avoid peak-hour routes through Silk Board to cut AQI exposure."
    return "Needs work 🔴", "Switch from Fastest to Balanced profile to start improving your score."


async def _recompute_cache(session_id: str, month_start: date) -> dict:
    """
    Aggregate trip_history for this session + month and upsert
    into green_score_cache. Returns the cache row as a dict.
    """
    agg = await db.fetchrow("""
        SELECT
            COUNT(*)                    AS total_trips,
            COALESCE(SUM(distance_km),            0) AS total_km,
            COALESCE(SUM(aqi_integral_saved),     0) AS aqi_saved_total,
            COALESCE(SUM(pm25_ug_avoided),        0) AS pm25_ug_saved,
            COALESCE(SUM(hotspots_avoided),       0) AS hotspots_avoided,
            COALESCE(SUM(time_delta_min),         0) AS time_delta_min,
            COALESCE(SUM(baseline_aqi_integral),  0) AS total_baseline_aqi,
            COALESCE(SUM(baseline_hotspots),      0) AS total_baseline_hotspots,
            COALESCE(SUM(baseline_time_min),      0) AS total_baseline_time
        FROM trip_history
        WHERE session_id = $1
          AND created_at >= $2
          AND created_at <  $2 + INTERVAL '1 month';
    """, session_id, datetime(month_start.year, month_start.month, 1, tzinfo=timezone.utc))

    score = _compute_green_score(
        aqi_saved        = float(agg["aqi_saved_total"]),
        baseline_aqi     = float(agg["total_baseline_aqi"]),
        hotspots_avoided = int(agg["hotspots_avoided"]),
        baseline_hotspots= int(agg["total_baseline_hotspots"]),
        time_delta       = float(agg["time_delta_min"]),
        baseline_time    = float(agg["total_baseline_time"]),
    )

    await db.execute("""
        INSERT INTO green_score_cache
            (session_id, month, total_trips, total_km,
             aqi_saved_total, pm25_ug_saved, hotspots_avoided,
             time_delta_min, green_score, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
        ON CONFLICT (session_id) DO UPDATE SET
            month            = EXCLUDED.month,
            total_trips      = EXCLUDED.total_trips,
            total_km         = EXCLUDED.total_km,
            aqi_saved_total  = EXCLUDED.aqi_saved_total,
            pm25_ug_saved    = EXCLUDED.pm25_ug_saved,
            hotspots_avoided = EXCLUDED.hotspots_avoided,
            time_delta_min   = EXCLUDED.time_delta_min,
            green_score      = EXCLUDED.green_score,
            updated_at       = NOW();
    """,
        session_id, month_start,
        int(agg["total_trips"]), float(agg["total_km"]),
        float(agg["aqi_saved_total"]), float(agg["pm25_ug_saved"]),
        int(agg["hotspots_avoided"]), float(agg["time_delta_min"]),
        score,
    )

    return {
        "green_score":      score,
        "total_trips":      int(agg["total_trips"]),
        "total_km":         round(float(agg["total_km"]), 2),
        "aqi_saved_total":  round(float(agg["aqi_saved_total"]), 1),
        "pm25_ug_saved":    round(float(agg["pm25_ug_saved"]), 1),
        "hotspots_avoided": int(agg["hotspots_avoided"]),
        "time_delta_min":   round(float(agg["time_delta_min"]), 1),
    }


# ── POST /api/user/trips ──────────────────────────────────────────────

@router.post("/trips", status_code=201)
async def record_trip(
    trip: TripRecord,
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-ID"),
):
    """
    Record a completed trip and update the session's Green Score.

    Called by the frontend when a user computes and accepts a route.
    Internally runs a second A* search with the fastest profile to
    establish the baseline for Green Score comparison.

    Returns the trip ID and the updated Green Score.
    """
    session_id = _validate_session(x_session_id)

    # ── Get fastest-route baseline ────────────────────────────────────
    # Re-run A* with the fastest profile to compute what the user
    # would have experienced taking the most direct route.
    baseline_time    = trip.travel_time_min
    baseline_aqi     = trip.aqi_exposure_integral
    baseline_hotspots = trip.hotspots_passed

    try:
        fastest = await find_route(
            origin_lat=trip.origin_lat,
            origin_lon=trip.origin_lon,
            dest_lat=trip.dest_lat,
            dest_lon=trip.dest_lon,
            *get_profile_weights(RouteProfile.FASTEST),
            profile=RouteProfile.FASTEST,
        )
        if fastest:
            cb = fastest.cost_breakdown
            baseline_time     = cb.travel_time_minutes
            baseline_aqi      = cb.aqi_exposure_cost * 500.0  # reverse normalise
            baseline_hotspots = cb.accident_hotspots_passed
    except Exception:
        # Baseline computation failed — use the trip's own values
        # (score will be 0 for this trip but won't crash)
        pass

    # ── Insert trip ───────────────────────────────────────────────────
    trip_id = await db.fetchval("""
        INSERT INTO trip_history (
            session_id,
            origin_lat, origin_lon, dest_lat, dest_lon,
            profile,
            distance_km, travel_time_min, avg_aqi,
            aqi_exposure_integral, hotspots_passed,
            baseline_time_min, baseline_aqi_integral, baseline_hotspots
        ) VALUES (
            $1,
            $2, $3, $4, $5,
            $6,
            $7, $8, $9,
            $10, $11,
            $12, $13, $14
        )
        RETURNING id;
    """,
        session_id,
        trip.origin_lat, trip.origin_lon, trip.dest_lat, trip.dest_lon,
        trip.profile,
        trip.distance_km, trip.travel_time_min, trip.avg_aqi,
        trip.aqi_exposure_integral, trip.hotspots_passed,
        baseline_time, baseline_aqi, baseline_hotspots,
    )

    # ── Recompute monthly Green Score cache ───────────────────────────
    now_utc     = datetime.now(timezone.utc)
    month_start = date(now_utc.year, now_utc.month, 1)
    cache       = await _recompute_cache(session_id, month_start)

    return {
        "trip_id":     trip_id,
        "green_score": cache["green_score"],
        "month":       month_start.strftime("%Y-%m"),
    }


# ── GET /api/user/green-score ─────────────────────────────────────────

@router.get("/green-score", response_model=GreenScoreResponse)
async def get_green_score(
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-ID"),
):
    """
    Return the current month's Green Score for the session.

    Primary path: reads from green_score_cache (fast).
    Fallback: live aggregate if cache is missing or from a previous month.
    """
    session_id = _validate_session(x_session_id)

    now_utc     = datetime.now(timezone.utc)
    month_start = date(now_utc.year, now_utc.month, 1)

    # Try cache first
    cached = await db.fetchrow("""
        SELECT *
        FROM green_score_cache
        WHERE session_id = $1
          AND month = $2;
    """, session_id, month_start)

    if cached:
        stats = dict(cached)
    else:
        # Cold cache — compute live
        stats = await _recompute_cache(session_id, month_start)

    score  = float(stats["green_score"])
    trips  = int(stats["total_trips"])
    grade, tip = _grade_and_tip(score, trips)

    return GreenScoreResponse(
        session_id       = session_id,
        month            = month_start.strftime("%Y-%m"),
        green_score      = score,
        total_trips      = trips,
        total_km         = round(float(stats["total_km"]), 2),
        aqi_saved_total  = round(float(stats["aqi_saved_total"]), 1),
        pm25_ug_saved    = round(float(stats["pm25_ug_saved"]), 1),
        hotspots_avoided = int(stats["hotspots_avoided"]),
        time_delta_min   = round(float(stats["time_delta_min"]), 1),
        grade            = grade,
        tip              = tip,
    )


# ── GET /api/user/trips ───────────────────────────────────────────────

@router.get("/trips")
async def get_trip_history(
    x_session_id: Optional[str] = Header(default=None, alias="X-Session-ID"),
    limit: int = Query(default=30, ge=1, le=100),
):
    """
    Return the last N trips for a session, newest first.
    """
    session_id = _validate_session(x_session_id)

    rows = await db.fetch("""
        SELECT
            id, profile,
            distance_km, travel_time_min, avg_aqi,
            aqi_integral_saved, hotspots_avoided,
            pm25_ug_avoided,
            created_at
        FROM trip_history
        WHERE session_id = $1
        ORDER BY created_at DESC
        LIMIT $2;
    """, session_id, limit)

    trips = []
    for r in rows:
        # Per-trip score contribution (normalised to 0–100 scale)
        contribution = round(
            min(100.0, max(0.0,
                float(r["aqi_integral_saved"]) * 0.05
                + float(r["hotspots_avoided"]) * 2.0
            )),
            1,
        )
        trips.append(TripSummary(
            id                 = r["id"],
            profile            = r["profile"],
            distance_km        = round(float(r["distance_km"]), 2),
            travel_time_min    = round(float(r["travel_time_min"]), 1),
            avg_aqi            = round(float(r["avg_aqi"]), 1),
            aqi_integral_saved = round(float(r["aqi_integral_saved"]), 1),
            hotspots_avoided   = int(r["hotspots_avoided"]),
            pm25_ug_avoided    = round(float(r["pm25_ug_avoided"]), 1),
            green_score_delta  = contribution,
            created_at         = r["created_at"].isoformat(),
        ))

    return {"trips": trips, "total": len(trips)}
