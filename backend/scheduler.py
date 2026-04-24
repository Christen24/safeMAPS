"""
SafeMAPS — Background Scheduler

Phase 5 addition: Job 3 — LSTM prediction refresh every 30 minutes.
Calls lstm_trainer.predict_all() which runs inference for every station
that has a trained model and writes results to aqi_predictions.
The /api/aqi/predict endpoint reads from that table (< 10ms latency)
instead of running inference on every HTTP request.

Job summary
────────────
  aqi_scrape       — every 15 min (first run: 2 min after startup)
  traffic_scrape   — every 5 min  (first run: 1 min after startup)
  lstm_predict     — every 30 min (first run: 5 min after startup)
                     Skipped gracefully if no trained models exist yet.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logger = logging.getLogger(__name__)


# ── Job 1: AQI scrape ─────────────────────────────────────────────────

async def run_aqi_cycle() -> None:
    from database import db
    from graph_cache import graph_cache

    logger.info("[scheduler] AQI scrape cycle starting...")
    try:
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from aqi_scraper import scrape_once
        await scrape_once()
        await graph_cache.refresh_aqi_costs(db)
        logger.info("[scheduler] AQI cycle complete — edge costs updated.")
    except Exception as exc:
        logger.warning(f"[scheduler] AQI cycle failed: {exc}", exc_info=True)


# ── Job 2: Traffic scrape ─────────────────────────────────────────────

async def run_traffic_cycle() -> None:
    from database import db
    from graph_cache import graph_cache

    logger.info("[scheduler] Traffic scrape cycle starting...")
    try:
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from traffic_ingestion import scrape_traffic
        updated_speeds = await scrape_traffic()
        if updated_speeds:
            graph_cache.update_speeds(updated_speeds)
            logger.info(
                f"[scheduler] Traffic cycle complete — "
                f"{len(updated_speeds)} edge speeds updated."
            )
        else:
            logger.info("[scheduler] Traffic cycle complete — no speed updates.")
    except Exception as exc:
        logger.warning(f"[scheduler] Traffic cycle failed: {exc}", exc_info=True)


# ── Job 3: LSTM prediction refresh ────────────────────────────────────

async def run_lstm_predict_cycle() -> None:
    """
    Run LSTM inference for all trained stations and write results to
    aqi_predictions. Skips gracefully if no .pt model files exist yet
    (first 7+ days while data accumulates).

    This runs every 30 minutes so that /api/aqi/predict can serve from
    the table cache without ever blocking on PyTorch inference in the
    HTTP request path.
    """
    logger.info("[scheduler] LSTM predict cycle starting...")
    try:
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from lstm_trainer import predict_all, MODELS_DIR

        model_files = list(MODELS_DIR.glob("*.pt"))
        if not model_files:
            logger.info(
                "[scheduler] No trained LSTM models found — skipping predict cycle. "
                "Run: python data_pipeline/lstm_trainer.py --train"
            )
            return

        results = await predict_all(minutes_ahead=30, save=True)
        logger.info(
            f"[scheduler] LSTM predict cycle complete — "
            f"{len(results)} stations updated in aqi_predictions."
        )
    except Exception as exc:
        logger.warning(f"[scheduler] LSTM predict cycle failed: {exc}", exc_info=True)


# ── Scheduler lifecycle ───────────────────────────────────────────────

def _on_job_event(event) -> None:
    if event.exception:
        logger.error(
            f"[scheduler] Job '{event.job_id}' raised: {event.exception}"
        )
    else:
        logger.debug(f"[scheduler] Job '{event.job_id}' executed OK.")


def start_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        job_defaults={
            "coalesce": True,
            "max_instances": 1,
            "misfire_grace_time": 60,
        }
    )
    scheduler.add_listener(_on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    scheduler.add_job(
        run_aqi_cycle,
        trigger="interval", minutes=15,
        id="aqi_scrape",
        name="AQI scrape + grid update + cache refresh",
        next_run_time=_now_plus(minutes=2),
    )

    scheduler.add_job(
        run_traffic_cycle,
        trigger="interval", minutes=5,
        id="traffic_scrape",
        name="Traffic speed scrape + cache sync",
        next_run_time=_now_plus(minutes=1),
    )

    # Phase 5: LSTM prediction refresh
    scheduler.add_job(
        run_lstm_predict_cycle,
        trigger="interval", minutes=30,
        id="lstm_predict",
        name="LSTM AQI forecast refresh",
        next_run_time=_now_plus(minutes=5),
    )

    scheduler.start()
    logger.info(
        "[scheduler] Started. "
        "AQI: every 15 min. "
        "Traffic: every 5 min. "
        "LSTM: every 30 min (first in ~5 min)."
    )
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped.")


def _now_plus(minutes: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
