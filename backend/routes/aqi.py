"""
SafeMAPS — Air Quality API Endpoints

Phase 5 additions
──────────────────
GET /api/aqi/predict
    Returns the cached 30-minute AQI forecast for a station from the
    aqi_predictions table. Runs inference on-the-fly as a fallback if
    the scheduler hasn't populated the table yet.

GET /api/aqi/stations
    Returns all known stations with their latest AQI reading and
    whether a trained LSTM model exists for them.
"""

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, HTTPException

from database import db
from spatial_queries import get_aqi_heatmap

router = APIRouter()

# Path to trained model weights — used to check model existence
_MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "data_pipeline" / "models"


# ── Existing endpoint ─────────────────────────────────────────────────

@router.get("/heatmap")
async def aqi_heatmap(
    min_lat: float = Query(..., ge=-90,  le=90),
    max_lat: float = Query(..., ge=-90,  le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
):
    """
    GeoJSON FeatureCollection of 100m grid cells with interpolated AQI.
    """
    cells = await get_aqi_heatmap(min_lat, max_lat, min_lon, max_lon)

    features = [
        {
            "type": "Feature",
            "properties": {
                "cell_id":    cell["id"],
                "aqi":        cell["aqi_value"],
                "center_lat": cell["center_lat"],
                "center_lon": cell["center_lon"],
            },
            "geometry": cell["geometry"],
        }
        for cell in cells
    ]

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "bbox":       [min_lon, min_lat, max_lon, max_lat],
            "cell_count": len(features),
        },
    }


# ── Phase 5: Prediction endpoint ──────────────────────────────────────

@router.get("/predict")
async def predict_aqi(
    station_id:    str = Query(..., description="Station ID from /api/aqi/stations"),
    minutes_ahead: int = Query(30,  ge=15, le=120,
                               description="How many minutes ahead to forecast (15–120)"),
):
    """
    Return a predicted AQI for a station N minutes from now.

    Primary path: reads from aqi_predictions table (written by scheduler
    every 30 min, latency < 5ms).

    Fallback path: if no fresh prediction exists in the table, runs the
    LSTM inference inline (adds ~50ms). Returns 404 if no trained model
    exists for the requested station.

    Response
    ─────────
    {
      "station_id":    "mock_7",
      "station_name":  "Silk Board",
      "predicted_aqi": 165.3,
      "minutes_ahead": 30,
      "predicted_for": "2026-04-24T09:30:00Z",
      "confidence":    0.87,
      "source":        "cache"   // or "inference"
    }
    """
    # ── Try the predictions cache first ──────────────────────────────
    freshness_window = max(minutes_ahead + 5, 35)  # minutes
    row = await db.fetchrow("""
        SELECT
            station_id, station_name, lat, lon,
            predicted_aqi, minutes_ahead, confidence, predicted_for, created_at
        FROM aqi_predictions
        WHERE station_id    = $1
          AND minutes_ahead = $2
          AND created_at   >= NOW() - ($3 || ' minutes')::INTERVAL
        ORDER BY created_at DESC
        LIMIT 1;
    """, station_id, minutes_ahead, str(freshness_window))

    if row:
        return {
            "station_id":    row["station_id"],
            "station_name":  row["station_name"],
            "lat":           row["lat"],
            "lon":           row["lon"],
            "predicted_aqi": round(float(row["predicted_aqi"]), 1),
            "minutes_ahead": row["minutes_ahead"],
            "predicted_for": row["predicted_for"].isoformat(),
            "confidence":    round(float(row["confidence"] or 0), 3),
            "source":        "cache",
        }

    # ── Fallback: inline inference ────────────────────────────────────
    import sys
    pipeline_dir = Path(__file__).resolve().parent.parent.parent / "data_pipeline"
    if str(pipeline_dir) not in sys.path:
        sys.path.insert(0, str(pipeline_dir))

    try:
        from lstm_trainer import predict
        pred_aqi = await predict(station_id, minutes_ahead=minutes_ahead, save=True)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Inference failed: {exc}",
        )

    if pred_aqi is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No trained LSTM model for station '{station_id}'. "
                "Run: python data_pipeline/lstm_trainer.py --train --station-id "
                f"{station_id}"
            ),
        )

    meta = await db.fetchrow("""
        SELECT station_name, lat, lon
        FROM aqi_history
        WHERE station_id = $1
        LIMIT 1;
    """, station_id)

    return {
        "station_id":    station_id,
        "station_name":  meta["station_name"] if meta else None,
        "lat":           float(meta["lat"]) if meta else None,
        "lon":           float(meta["lon"]) if meta else None,
        "predicted_aqi": round(pred_aqi, 1),
        "minutes_ahead": minutes_ahead,
        "predicted_for": (
            datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
        ).isoformat(),
        "confidence":    None,
        "source":        "inference",
    }


# ── Phase 5: Stations list ────────────────────────────────────────────

@router.get("/stations")
async def list_stations():
    """
    Return all known AQI stations with their latest reading and LSTM status.

    Clients use this to populate station pickers and to know which
    stations support /api/aqi/predict.

    Response: list of station objects:
    {
      "station_id":   "mock_7",
      "station_name": "Silk Board",
      "lat": 12.917, "lon": 77.623,
      "latest_aqi":   172.0,
      "latest_at":    "2026-04-24T08:45:00Z",
      "has_model":    true
    }
    """
    rows = await db.fetch("""
        SELECT DISTINCT ON (station_id)
            station_id,
            station_name,
            lat,
            lon,
            aqi          AS latest_aqi,
            recorded_at  AS latest_at
        FROM aqi_history
        WHERE aqi IS NOT NULL
        ORDER BY station_id, recorded_at DESC;
    """)

    stations = []
    for r in rows:
        model_path = _MODELS_DIR / f"{r['station_id']}.pt"
        stations.append({
            "station_id":   r["station_id"],
            "station_name": r["station_name"],
            "lat":          float(r["lat"]),
            "lon":          float(r["lon"]),
            "latest_aqi":   round(float(r["latest_aqi"]), 1),
            "latest_at":    r["latest_at"].isoformat(),
            "has_model":    model_path.exists(),
        })

    return {
        "stations":    stations,
        "total":       len(stations),
        "models_dir":  str(_MODELS_DIR),
    }
