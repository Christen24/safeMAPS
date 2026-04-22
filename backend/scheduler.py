"""
SafeMAPS — Background Scheduler

Runs two recurring jobs inside the FastAPI process using APScheduler:

  Job 1 · AQI scrape   — every 15 minutes
    1. Fetch latest readings from WAQI stations inside Bangalore bbox
    2. Interpolate AQI values across the 100m grid (bulk UPDATE)
    3. Refresh edge_aqi in graph_cache so the next route request sees
       the new values without reloading the entire graph

  Job 2 · Traffic scrape — every 5 minutes
    1. Fetch live speeds from TomTom for sampled road segments
    2. Write updated speed_kmh to road_segments in PostGIS
    3. Call graph_cache.update_speeds() to sync the in-memory dict
       so A* uses current congestion without a full graph reload

Both jobs are async, run inside the event loop, and share the
existing asyncpg pool (via the `db` singleton) so they never open
their own connections.

Design decisions
─────────────────
- AsyncIOScheduler: runs coroutines directly in the FastAPI event loop,
  no separate thread pool needed
- misfire_grace_time=60: if a job is missed (e.g. during startup),
  it has 60s to still fire instead of being skipped silently
- coalesce=True: if multiple executions were missed, run only once
- max_instances=1: prevents overlapping runs if a job takes longer
  than its interval (important for AQI which can take ~30s)
- Each job catches all exceptions internally — a failed scrape cycle
  logs a warning but never crashes the FastAPI process

Usage (from main.py lifespan)
───────────────────────────────
    from scheduler import start_scheduler, stop_scheduler
    scheduler = start_scheduler()
    yield
    stop_scheduler(scheduler)
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED

logger = logging.getLogger(__name__)


# ── Job 1: AQI scrape ─────────────────────────────────────────────────

async def run_aqi_cycle() -> None:
    """
    Fetch AQI data, update grid cells, then refresh the in-memory
    edge cost cache so routes immediately reflect new air quality.

    Import is deferred inside the function so this module can be
    imported at any point without triggering side effects from the
    pipeline scripts (which do sys.path manipulation on import).
    """
    from database import db
    from graph_cache import graph_cache

    logger.info("[scheduler] AQI scrape cycle starting...")

    try:
        # Pull in the scraper logic.  scrape_once() handles both the
        # real WAQI API path and the mock fallback when no token is set.
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from aqi_scraper import scrape_once
        await scrape_once()

        # After the grid is updated in PostGIS, refresh the in-memory
        # edge → AQI mapping so the next A* call sees fresh values.
        # This is a targeted operation — it does NOT reload the full graph.
        await graph_cache.refresh_aqi_costs(db)
        logger.info("[scheduler] AQI cycle complete — edge costs updated.")

    except Exception as exc:
        # Never let a failed scrape bring down the process.
        logger.warning(f"[scheduler] AQI cycle failed: {exc}", exc_info=True)


# ── Job 2: Traffic scrape ─────────────────────────────────────────────

async def run_traffic_cycle() -> None:
    """
    Fetch live traffic speeds, write to PostGIS, then sync the
    in-memory adjacency list so A* uses current speeds.
    """
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

        # scrape_traffic() returns a dict {edge_id: new_speed_kmh}
        # for every segment it updated. We pass that straight to
        # graph_cache so the adjacency list reflects live congestion.
        updated_speeds = await scrape_traffic()

        if updated_speeds:
            graph_cache.update_speeds(updated_speeds)
            logger.info(
                f"[scheduler] Traffic cycle complete — "
                f"{len(updated_speeds)} edge speeds updated in cache."
            )
        else:
            logger.info("[scheduler] Traffic cycle complete — no speed updates.")

    except Exception as exc:
        logger.warning(f"[scheduler] Traffic cycle failed: {exc}", exc_info=True)


# ── Scheduler lifecycle ───────────────────────────────────────────────

def _on_job_event(event) -> None:
    """Log APScheduler job events for observability."""
    if event.exception:
        logger.error(
            f"[scheduler] Job '{event.job_id}' raised an exception: "
            f"{event.exception}"
        )
    else:
        logger.debug(f"[scheduler] Job '{event.job_id}' executed successfully.")


def start_scheduler() -> AsyncIOScheduler:
    """
    Create and start the APScheduler instance.

    Called from main.py lifespan before `yield`.
    Returns the scheduler so the lifespan can stop it on shutdown.

    Schedule summary:
      aqi_scrape    — interval: 15 min, first run: 2 min after startup
                      (give the server time to fully warm up before
                      hitting the WAQI API)
      traffic_scrape — interval: 5 min, first run: 1 min after startup
    """
    scheduler = AsyncIOScheduler(
        job_defaults={
            "coalesce": True,          # if missed, run once not N times
            "max_instances": 1,        # no overlapping runs
            "misfire_grace_time": 60,  # allow up to 60s late execution
        }
    )

    # Listen for job errors so they surface in logs
    scheduler.add_listener(_on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)

    # ── AQI job ──────────────────────────────────────────────────────
    scheduler.add_job(
        run_aqi_cycle,
        trigger="interval",
        minutes=15,
        id="aqi_scrape",
        name="AQI scrape + grid update + cache refresh",
        # First run 2 minutes after startup so the server is fully ready
        next_run_time=_now_plus(minutes=2),
    )

    # ── Traffic job ───────────────────────────────────────────────────
    scheduler.add_job(
        run_traffic_cycle,
        trigger="interval",
        minutes=5,
        id="traffic_scrape",
        name="Traffic speed scrape + cache sync",
        next_run_time=_now_plus(minutes=1),
    )

    scheduler.start()

    logger.info(
        "[scheduler] Started. "
        "AQI: every 15 min (first in ~2 min). "
        "Traffic: every 5 min (first in ~1 min)."
    )
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    """Gracefully shut down the scheduler. Called from main.py lifespan."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped.")


# ── Helpers ───────────────────────────────────────────────────────────

def _now_plus(minutes: int):
    """Return a datetime `minutes` from now, used for next_run_time."""
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
