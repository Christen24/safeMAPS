"""
SafeMAPS — AQI Forecasting LSTM

Trains a per-station LSTM that predicts AQI 30 minutes ahead based on
the last 24 hours of readings from aqi_history.

Architecture
─────────────
  Input  : sequences of shape (window=48, features=6)
           Features: [aqi, pm25_norm, hour_sin, hour_cos, dow_sin, dow_cos]
  LSTM   : hidden_size=64, num_layers=1, dropout=0.2
  Output : 1 value (predicted AQI, unnormalised in predict())

Why 48 steps at 15-min cadence = 12 hours lookback
  AQI in Bangalore shows strong 12-hour autocorrelation driven by
  morning and evening traffic + cooking cycles. 24 hours (96 steps)
  would capture the full daily pattern but requires ~7× more data to
  train well. 12 hours is a pragmatic compromise for early deployment.

Training data requirement
  Minimum: 7 days × 24h × 4 readings/h = 672 rows per station.
  Recommended: 30 days for stable validation loss.
  Check current row count:
    SELECT station_id, COUNT(*) FROM aqi_history GROUP BY station_id;

Usage
──────
  # Train all stations with enough data:
  python lstm_trainer.py --train

  # Train a single station:
  python lstm_trainer.py --train --station-id mock_7

  # Predict 30 min ahead for one station (prints result):
  python lstm_trainer.py --predict --station-id mock_7

  # Predict and write to aqi_predictions table:
  python lstm_trainer.py --predict --station-id mock_7 --save

  # Predict all stations and save (used by scheduler):
  python lstm_trainer.py --predict-all --save

Dependencies
─────────────
  pip install torch asyncpg numpy
  (torch CPU-only is fine — model is tiny, inference < 5ms)
"""

import sys
import asyncio
import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import asyncpg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from config import settings

# Directory where trained model weights are saved
MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

# Sequence length in number of readings (48 × 15 min = 12 hours)
WINDOW      = 48
# Predict this many steps ahead (2 × 15 min = 30 minutes)
HORIZON     = 2
HIDDEN_SIZE = 64
N_FEATURES  = 6       # aqi_norm, pm25_norm, hour_sin, hour_cos, dow_sin, dow_cos
EPOCHS      = 50
PATIENCE    = 5       # early stopping


# ── DB helpers ────────────────────────────────────────────────────────

async def _get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
    )


async def load_station_ids() -> list[str]:
    """Return all station IDs that have at least WINDOW + HORIZON rows."""
    conn = await _get_conn()
    try:
        rows = await conn.fetch("""
            SELECT station_id
            FROM aqi_history
            GROUP BY station_id
            HAVING COUNT(*) >= $1
            ORDER BY station_id;
        """, WINDOW + HORIZON + 100)  # +100 for a minimal val split
        return [r["station_id"] for r in rows]
    finally:
        await conn.close()


async def load_training_data(station_id: str, days: int = 30) -> list[dict]:
    """
    Query aqi_history for the last `days` of readings for one station.
    Returns a list of dicts ordered by recorded_at ASC.
    """
    conn = await _get_conn()
    try:
        rows = await conn.fetch("""
            SELECT
                aqi,
                COALESCE(pm25, aqi * 0.6)   AS pm25,
                hour_of_day,
                day_of_week,
                recorded_at
            FROM aqi_history
            WHERE station_id = $1
              AND recorded_at >= NOW() - ($2 || ' days')::INTERVAL
            ORDER BY recorded_at ASC;
        """, station_id, str(days))
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def load_last_window(station_id: str) -> list[dict] | None:
    """
    Fetch the most recent WINDOW readings for inference.
    Returns None if there aren't enough rows.
    """
    conn = await _get_conn()
    try:
        rows = await conn.fetch("""
            SELECT aqi,
                   COALESCE(pm25, aqi * 0.6) AS pm25,
                   hour_of_day,
                   day_of_week,
                   recorded_at
            FROM aqi_history
            WHERE station_id = $1
              AND aqi IS NOT NULL
            ORDER BY recorded_at DESC
            LIMIT $2;
        """, station_id, WINDOW)
        if len(rows) < WINDOW:
            return None
        return [dict(r) for r in reversed(rows)]   # chronological order
    finally:
        await conn.close()


async def get_station_meta(station_id: str) -> dict:
    """Fetch lat/lon/name for a station from aqi_history."""
    conn = await _get_conn()
    try:
        row = await conn.fetchrow("""
            SELECT station_name, lat, lon
            FROM aqi_history
            WHERE station_id = $1
            LIMIT 1;
        """, station_id)
        return dict(row) if row else {}
    finally:
        await conn.close()


# ── Feature engineering ───────────────────────────────────────────────

def _cyclic(val: float, period: float) -> tuple[float, float]:
    """Encode a cyclic value as (sin, cos) pair."""
    angle = 2 * math.pi * val / period
    return math.sin(angle), math.cos(angle)


def build_features(rows: list[dict], aqi_mean: float, aqi_std: float) -> np.ndarray:
    """
    Convert a list of row dicts into a (T, N_FEATURES) numpy array.

    Features per timestep:
      0: aqi_norm      — z-score normalised AQI
      1: pm25_norm     — z-score normalised PM2.5 (proxy for aqi if missing)
      2: hour_sin      — cyclic encoding of hour (period=24)
      3: hour_cos
      4: dow_sin       — cyclic encoding of day-of-week (period=7)
      5: dow_cos

    Cyclic encoding prevents the model treating hour 23 → hour 0 as a
    large discontinuous jump.
    """
    out = np.zeros((len(rows), N_FEATURES), dtype=np.float32)
    for i, r in enumerate(rows):
        aqi  = float(r["aqi"]  or 50.0)
        pm25 = float(r["pm25"] or aqi * 0.6)
        std  = max(aqi_std, 1.0)

        h_sin, h_cos = _cyclic(r["hour_of_day"], 24)
        d_sin, d_cos = _cyclic(r["day_of_week"],  7)

        out[i] = [
            (aqi  - aqi_mean) / std,
            (pm25 - aqi_mean * 0.6) / std,
            h_sin, h_cos,
            d_sin, d_cos,
        ]
    return out


def build_sequences(
    features: np.ndarray,
    aqi_values: np.ndarray,
    aqi_mean: float,
    aqi_std: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Sliding-window sequence builder.

    X shape: (N_samples, WINDOW, N_FEATURES)
    y shape: (N_samples,)  — normalised AQI HORIZON steps ahead
    """
    X, y = [], []
    for i in range(len(features) - WINDOW - HORIZON + 1):
        X.append(features[i : i + WINDOW])
        target_aqi = float(aqi_values[i + WINDOW + HORIZON - 1])
        y.append((target_aqi - aqi_mean) / max(aqi_std, 1.0))

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)


# ── PyTorch LSTM model ────────────────────────────────────────────────

def _import_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError:
        logger.error("PyTorch not installed. Run: pip install torch")
        sys.exit(1)


class AQIForecastLSTM:
    """
    Thin wrapper around a PyTorch LSTM so the rest of this file
    doesn't need to import torch at module level.
    """

    def __init__(self):
        torch, nn = _import_torch()
        self.torch = torch
        self.nn    = nn
        self._model = None
        self._mean  = 0.0
        self._std   = 1.0

    def _build(self):
        torch, nn = self.torch, self.nn

        class _Net(nn.Module):
            def __init__(self):
                super().__init__()
                self.lstm   = nn.LSTM(
                    input_size=N_FEATURES,
                    hidden_size=HIDDEN_SIZE,
                    num_layers=1,
                    batch_first=True,
                    dropout=0.0,   # dropout only makes sense with num_layers>1
                )
                self.drop   = nn.Dropout(0.2)
                self.linear = nn.Linear(HIDDEN_SIZE, 1)

            def forward(self, x):
                out, _ = self.lstm(x)
                last   = out[:, -1, :]   # take only the last timestep
                return self.linear(self.drop(last)).squeeze(-1)

        return _Net()

    def train(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val:   np.ndarray,
        y_val:   np.ndarray,
        aqi_mean: float,
        aqi_std:  float,
    ) -> float:
        """
        Train the LSTM. Returns final validation RMSE in AQI units.
        """
        torch = self.torch
        self._mean = aqi_mean
        self._std  = aqi_std
        self._model = self._build()

        Xt = torch.tensor(X_train)
        yt = torch.tensor(y_train)
        Xv = torch.tensor(X_val)
        yv = torch.tensor(y_val)

        optim    = torch.optim.Adam(self._model.parameters(), lr=1e-3)
        crit     = torch.nn.MSELoss()
        best_val = float("inf")
        no_improv = 0

        for epoch in range(1, EPOCHS + 1):
            self._model.train()
            optim.zero_grad()
            loss = crit(self._model(Xt), yt)
            loss.backward()
            optim.step()

            self._model.eval()
            with torch.no_grad():
                val_loss = crit(self._model(Xv), yv).item()

            if epoch % 10 == 0 or epoch == 1:
                logger.info(
                    f"  Epoch {epoch:3d}/{EPOCHS} — "
                    f"train_loss={loss.item():.4f}  val_loss={val_loss:.4f}"
                )

            if val_loss < best_val - 1e-4:
                best_val  = val_loss
                no_improv = 0
                # Save best weights in-memory
                self._best_state = {k: v.clone() for k, v in self._model.state_dict().items()}
            else:
                no_improv += 1
                if no_improv >= PATIENCE:
                    logger.info(f"  Early stop at epoch {epoch} (patience={PATIENCE}).")
                    break

        # Restore best weights
        if hasattr(self, "_best_state"):
            self._model.load_state_dict(self._best_state)

        val_rmse = math.sqrt(best_val) * max(aqi_std, 1.0)
        return val_rmse

    def predict(self, window: np.ndarray) -> float:
        """
        Run inference on a (WINDOW, N_FEATURES) numpy array.
        Returns a predicted AQI value in original units.
        """
        torch = self.torch
        assert self._model is not None, "Model not trained or loaded."
        self._model.eval()
        x = torch.tensor(window[None])   # add batch dim
        with torch.no_grad():
            norm_pred = self._model(x).item()
        return norm_pred * max(self._std, 1.0) + self._mean

    def save(self, station_id: str) -> Path:
        """Save model weights + normalisation stats."""
        torch = self.torch
        path = MODELS_DIR / f"{station_id}.pt"
        torch.save({
            "state_dict": self._model.state_dict(),
            "mean": self._mean,
            "std":  self._std,
        }, path)
        return path

    def load(self, station_id: str) -> bool:
        """Load model weights. Returns False if no saved model exists."""
        torch = self.torch
        path  = MODELS_DIR / f"{station_id}.pt"
        if not path.exists():
            return False
        checkpoint  = torch.load(path, map_location="cpu")
        self._model = self._build()
        self._model.load_state_dict(checkpoint["state_dict"])
        self._mean  = checkpoint["mean"]
        self._std   = checkpoint["std"]
        return True


# ── Training entry point ──────────────────────────────────────────────

async def train(station_id: str) -> float | None:
    """
    Full training pipeline for one station.
    Returns validation RMSE in AQI units, or None if not enough data.
    """
    logger.info(f"[{station_id}] Loading training data...")
    rows = await load_training_data(station_id, days=30)

    if len(rows) < WINDOW + HORIZON + 50:
        logger.warning(
            f"[{station_id}] Only {len(rows)} rows — need "
            f"{WINDOW + HORIZON + 50}. Collect more data first."
        )
        return None

    aqi_values = np.array([float(r["aqi"] or 50) for r in rows])
    aqi_mean   = float(aqi_values.mean())
    aqi_std    = float(aqi_values.std())

    features = build_features(rows, aqi_mean, aqi_std)
    X, y     = build_sequences(features, aqi_values, aqi_mean, aqi_std)

    # 80/20 train/val split (no shuffle — time series must stay ordered)
    split    = int(len(X) * 0.8)
    X_tr, X_v = X[:split], X[split:]
    y_tr, y_v = y[:split], y[split:]

    logger.info(
        f"[{station_id}] Training: {len(X_tr)} samples, "
        f"val: {len(X_v)} samples, "
        f"AQI mean={aqi_mean:.1f} std={aqi_std:.1f}"
    )

    lstm  = AQIForecastLSTM()
    rmse  = lstm.train(X_tr, y_tr, X_v, y_v, aqi_mean, aqi_std)
    path  = lstm.save(station_id)

    logger.info(f"[{station_id}] Saved to {path}. Val RMSE = {rmse:.1f} AQI units.")
    return rmse


# ── Prediction entry point ────────────────────────────────────────────

async def predict(station_id: str, minutes_ahead: int = 30, save: bool = False) -> float | None:
    """
    Predict AQI for a station `minutes_ahead` minutes from now.

    Returns the predicted AQI float, or None if no model or data exists.
    Optionally writes the result to aqi_predictions for the API to serve.
    """
    lstm = AQIForecastLSTM()
    if not lstm.load(station_id):
        logger.warning(f"[{station_id}] No trained model found at {MODELS_DIR}/{station_id}.pt")
        return None

    rows = await load_last_window(station_id)
    if rows is None:
        logger.warning(f"[{station_id}] Not enough recent data for inference (need {WINDOW} rows).")
        return None

    aqi_values = np.array([float(r["aqi"] or 50) for r in rows])
    features   = build_features(rows, lstm._mean, lstm._std)
    pred_aqi   = lstm.predict(features)
    pred_aqi   = max(0.0, round(pred_aqi, 1))

    confidence = _estimate_confidence(station_id)

    logger.info(f"[{station_id}] Predicted AQI in {minutes_ahead} min: {pred_aqi:.1f}")

    if save:
        meta         = await get_station_meta(station_id)
        predicted_for = datetime.now(timezone.utc) + timedelta(minutes=minutes_ahead)
        await _save_prediction(
            station_id, meta, pred_aqi, minutes_ahead, confidence, predicted_for
        )

    return pred_aqi


def _estimate_confidence(station_id: str) -> float:
    """
    Rough confidence score based on model file size / age as a proxy
    for training quality. Replace with proper val-loss tracking later.
    """
    path = MODELS_DIR / f"{station_id}.pt"
    if not path.exists():
        return 0.0
    age_hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600
    # Decay confidence if model is more than 7 days old
    return max(0.1, 1.0 - age_hours / (24 * 7))


async def _save_prediction(
    station_id: str,
    meta: dict,
    predicted_aqi: float,
    minutes_ahead: int,
    confidence: float,
    predicted_for: datetime,
) -> None:
    """Write prediction to aqi_predictions, replacing stale rows for this station."""
    conn = await _get_conn()
    try:
        # Delete stale predictions for this station + horizon combo
        await conn.execute("""
            DELETE FROM aqi_predictions
            WHERE station_id = $1
              AND minutes_ahead = $2
              AND created_at < NOW() - INTERVAL '35 minutes';
        """, station_id, minutes_ahead)

        await conn.execute("""
            INSERT INTO aqi_predictions
                (station_id, station_name, lat, lon,
                 predicted_aqi, minutes_ahead, confidence, predicted_for)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8);
        """,
            station_id,
            meta.get("station_name"),
            meta.get("lat"),
            meta.get("lon"),
            predicted_aqi,
            minutes_ahead,
            confidence,
            predicted_for,
        )
    finally:
        await conn.close()


async def predict_all(minutes_ahead: int = 30, save: bool = True) -> dict[str, float]:
    """
    Run predict() for every station that has a trained model.
    Used by the scheduler job every 30 minutes.
    Returns {station_id: predicted_aqi}.
    """
    results: dict[str, float] = {}
    for pt_file in MODELS_DIR.glob("*.pt"):
        station_id = pt_file.stem
        val = await predict(station_id, minutes_ahead=minutes_ahead, save=save)
        if val is not None:
            results[station_id] = val
    logger.info(f"predict_all: {len(results)} stations predicted.")
    return results


# ── CLI entry point ───────────────────────────────────────────────────

async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="SafeMAPS AQI LSTM trainer / predictor")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--train",       action="store_true", help="Train model(s)")
    mode.add_argument("--predict",     action="store_true", help="Predict for one station")
    mode.add_argument("--predict-all", action="store_true", dest="predict_all",
                      help="Predict for all trained stations and save to DB")

    parser.add_argument("--station-id", type=str,  default=None, help="Station ID")
    parser.add_argument("--minutes",    type=int,  default=30,   help="Minutes ahead to predict")
    parser.add_argument("--save",       action="store_true",     help="Write predictions to DB")
    args = parser.parse_args()

    if args.train:
        if args.station_id:
            await train(args.station_id)
        else:
            ids = await load_station_ids()
            logger.info(f"Training {len(ids)} stations with sufficient data...")
            for sid in ids:
                await train(sid)

    elif args.predict:
        if not args.station_id:
            parser.error("--predict requires --station-id")
        val = await predict(args.station_id, args.minutes, save=args.save)
        if val is not None:
            print(f"Predicted AQI for '{args.station_id}' in {args.minutes} min: {val:.1f}")

    elif args.predict_all:
        results = await predict_all(minutes_ahead=args.minutes, save=True)
        for sid, v in results.items():
            print(f"  {sid}: {v:.1f}")


if __name__ == "__main__":
    asyncio.run(main())
