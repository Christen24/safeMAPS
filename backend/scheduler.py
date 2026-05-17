"""
SafeMAPS — Background Scheduler

Job summary
────────────
  Job 1: aqi_scrape      — every 15 min (WAQI+merge+grid, +2min startup)
  Job 2: traffic_scrape  — every  5 min (+1min startup)
  Job 3: lstm_predict    — every 30 min (+5min startup)
  Job 4: cpcb_scrape     — every 15 min (+9min startup, offset from WAQI)
  Job 5: incident_scrape — every 10 min (+3min startup)
  Job 6: osm_diff_update — weekly Sunday 02:00 UTC (Phase 11.2)
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
    aqi_predictions. Skips gracefully if no .pt model files exist yet.
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


# ── Job 4: CPCB-only refresh ──────────────────────────────────────────

async def run_cpcb_cycle() -> None:
    """
    Runs the CPCB scraper independently every 15 min, offset 7 min from
    the WAQI cycle. Writes fresh CPCB readings to aqi_history and triggers
    an AQI grid refresh so routing picks up the new data.
    """
    from database import db
    from graph_cache import graph_cache

    logger.info("[scheduler] CPCB scrape cycle starting...")
    try:
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from config import settings
        if not settings.cpcb_api_key:
            logger.info("[scheduler] CPCB_API_KEY not set — skipping CPCB cycle.")
            return

        from cpcb_scraper import fetch_cpcb_stations
        from aqi_scraper import insert_aqi_history, interpolate_aqi_to_grid

        import asyncpg
        from datetime import datetime, timezone

        cpcb_stations = await fetch_cpcb_stations(settings.cpcb_api_key)
        if not cpcb_stations:
            logger.info("[scheduler] CPCB cycle: no stations returned.")
            return

        conn = await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_db,
            user=settings.postgres_user,
            password=settings.postgres_password,
        )
        try:
            now_utc = datetime.now(timezone.utc)
            count = 0
            for s in cpcb_stations:
                if s.get("aqi") is None:
                    continue
                await conn.fetchval("""
                    INSERT INTO aqi_stations (station_uid, name, geom)
                    VALUES ($1, $2, ST_SetSRID(ST_MakePoint($3, $4), 4326))
                    ON CONFLICT (station_uid) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id;
                """, s["uid"], s["name"], s["lon"], s["lat"])
                await insert_aqi_history(
                    conn, s["uid"], s["name"], s["lat"], s["lon"],
                    s, now_utc, source="cpcb",
                )
                count += 1

            logger.info(f"[scheduler] CPCB cycle: {count} stations written.")
            await interpolate_aqi_to_grid(conn)
        finally:
            await conn.close()

        await graph_cache.refresh_aqi_costs(db)
        logger.info("[scheduler] CPCB cycle complete — edge AQI costs updated.")

    except Exception as exc:
        logger.warning(f"[scheduler] CPCB cycle failed: {exc}", exc_info=True)


# ── Job 5: Live incident scrape ───────────────────────────────────────

async def run_incident_cycle() -> None:
    """
    Scrape OSM Overpass, Waze CCP, and @BlrCityTraffic for live incidents.
    Deduplicates by 100m spatial proximity, writes to live_incidents table,
    then refreshes edge_incident costs in the graph cache.
    """
    from database import db
    from graph_cache import graph_cache

    logger.info("[scheduler] Incident scrape cycle starting...")
    try:
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from incident_scraper import scrape_incidents
        inserted, expired = await scrape_incidents()
        logger.info(
            f"[scheduler] Incident cycle complete — "
            f"{inserted} new incidents, {expired} expired."
        )
        await graph_cache.refresh_incident_costs(db)
        logger.info("[scheduler] Incident edge costs refreshed.")
    except Exception as exc:
        logger.warning(f"[scheduler] Incident cycle failed: {exc}", exc_info=True)


# ── Scheduler lifecycle ───────────────────────────────────────────────

# ── Job 6: OSM weekly diff update ────────────────────────────────────

async def run_osm_diff_cycle() -> None:
    """
    Weekly OSM road network diff (Phase 11.2).
    Downloads latest Karnataka PBF, clips to Bangalore, diffs road_segments,
    deactivates removed edges, and triggers graph cache reload on changes.
    Runs Sunday 02:00 UTC — low-traffic window for Bangalore (7:30 IST).
    """
    logger.info("[scheduler] OSM weekly diff cycle starting...")
    try:
        import sys
        from pathlib import Path
        pipeline_dir = Path(__file__).resolve().parent.parent / "data_pipeline"
        if str(pipeline_dir) not in sys.path:
            sys.path.insert(0, str(pipeline_dir))

        from osm_diff_updater import run_osm_diff_update
        stats = await run_osm_diff_update()
        logger.info(f"[scheduler] OSM diff complete: {stats}")
    except Exception as exc:
        logger.warning(f"[scheduler] OSM diff cycle failed: {exc}", exc_info=True)


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

    scheduler.add_job(
        run_lstm_predict_cycle,
        trigger="interval", minutes=30,
        id="lstm_predict",
        name="LSTM AQI forecast refresh",
        next_run_time=_now_plus(minutes=5),
    )

    # Job 4: CPCB — offset 7 min from WAQI to avoid write collisions
    scheduler.add_job(
        run_cpcb_cycle,
        trigger="interval", minutes=15,
        id="cpcb_scrape",
        name="CPCB AQI scrape + grid update",
        next_run_time=_now_plus(minutes=9),
    )

    # Job 5: Live incidents — 10 min cadence
    scheduler.add_job(
        run_incident_cycle,
        trigger="interval", minutes=10,
        id="incident_scrape",
        name="Live incident scrape (OSM+Waze+Twitter)",
        next_run_time=_now_plus(minutes=3),
    )

    # Job 6: OSM weekly diff — every Sunday 02:00 UTC (Phase 11.2)
    scheduler.add_job(
        run_osm_diff_cycle,
        trigger="cron",
        day_of_week="sun",
        hour=2,
        minute=0,
        id="osm_diff_update",
        name="OSM weekly PBF diff — detect/apply road network changes",
    )

    scheduler.start()
    logger.info(
        "[scheduler] Started. "
        "AQI: 15min. Traffic: 5min. LSTM: 30min. "
        "CPCB: 15min (+7min). Incidents: 10min. "
        "OSM diff: Sunday 02:00 UTC."
    )
    return scheduler


def stop_scheduler(scheduler: AsyncIOScheduler) -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[scheduler] Stopped.")


def _now_plus(minutes: int):
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)
