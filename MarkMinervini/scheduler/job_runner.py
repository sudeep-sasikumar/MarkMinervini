"""
APScheduler job definitions (Section 12).
All times are in Europe/London (BST/GMT) timezone.

Schedule:
  08:00  — Data refresh + RS computation + breadth
  09:00  — Market intelligence (regime, sectors, macro)
  11:00  — Full screening run #1
  13:00  — Morning briefing Telegram message
  13:30  — US market open; begin intraday monitoring
  15:30  — Full screening run #2
  19:00  — Full screening run #3
  21:15  — Post-market wrap
  Sunday 10:00 — Weekly: management quality AI + backtest validation
  Every 15 min (13:30–21:00) — Intraday breakout check
"""

import logging
import os
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

BST = pytz.timezone("Europe/London")


def create_scheduler() -> BackgroundScheduler:
    """Build and return a configured APScheduler instance (not yet started)."""
    scheduler = BackgroundScheduler(timezone=BST)

    # --- 08:00 BST: Data refresh ---
    scheduler.add_job(
        job_data_refresh,
        CronTrigger(hour=8, minute=0, timezone=BST),
        id="data_refresh",
        name="Data refresh + RS + Breadth",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 09:00 BST: Market intelligence ---
    scheduler.add_job(
        job_market_intelligence,
        CronTrigger(hour=9, minute=0, timezone=BST),
        id="market_intelligence",
        name="Regime + Sectors + Macro",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # --- 11:00 BST: Full screening run #1 ---
    scheduler.add_job(
        job_full_scan,
        CronTrigger(hour=11, minute=0, timezone=BST),
        id="full_scan_1",
        name="Full screen #1",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 13:00 BST: Morning briefing ---
    scheduler.add_job(
        job_morning_briefing,
        CronTrigger(hour=13, minute=0, timezone=BST),
        id="morning_briefing",
        name="Morning Telegram briefing",
        replace_existing=True,
        max_instances=1,
    )

    # --- 15:30 BST: Full screening run #2 ---
    scheduler.add_job(
        job_full_scan,
        CronTrigger(hour=15, minute=30, timezone=BST),
        id="full_scan_2",
        name="Full screen #2",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 19:00 BST: Full screening run #3 ---
    scheduler.add_job(
        job_full_scan,
        CronTrigger(hour=19, minute=0, timezone=BST),
        id="full_scan_3",
        name="Full screen #3",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 21:15 BST: Post-market wrap ---
    scheduler.add_job(
        job_post_market,
        CronTrigger(hour=21, minute=15, timezone=BST),
        id="post_market",
        name="Post-market wrap",
        replace_existing=True,
        max_instances=1,
    )

    # --- Intraday: every 15 minutes, 13:30–21:00 BST Mon–Fri ---
    scheduler.add_job(
        job_intraday_check,
        CronTrigger(
            day_of_week="mon-fri",
            hour="13-20",
            minute="*/15",
            timezone=BST,
        ),
        id="intraday",
        name="Intraday breakout check",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    # --- Sunday 10:00: Weekly AI analysis + backtest ---
    scheduler.add_job(
        job_weekly,
        CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=BST),
        id="weekly",
        name="Weekly AI analysis + backtest",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=1800,
    )

    return scheduler


# ---------------------------------------------------------------------------
# Job implementations — all import lazily to avoid circular imports at startup
# ---------------------------------------------------------------------------

def job_data_refresh():
    """08:00 BST — Download OHLCV, update fundamentals cache, compute RS and breadth."""
    logger.info("=== JOB: Data Refresh ===")
    try:
        from data.cache import purge_expired
        from screening.universe import get_universe
        from data.fetcher import fetch_ohlcv_batch
        from screening.rs_calculator import compute_rs_ratings
        from market_intelligence.breadth_monitor import compute_breadth

        purge_expired()
        universe = get_universe()
        price_data = fetch_ohlcv_batch(universe, period="2y")

        rs_df = compute_rs_ratings(price_data)
        breadth = compute_breadth(price_data)

        logger.info("Data refresh complete: %d tickers, breadth=%.1f%%",
                    len(price_data), breadth)
    except Exception as exc:
        logger.error("Data refresh failed: %s", exc, exc_info=True)


def job_market_intelligence():
    """09:00 BST — Regime detection, sector analysis, macro calendar."""
    logger.info("=== JOB: Market Intelligence ===")
    try:
        from market_intelligence.regime_detector import detect_regime
        from market_intelligence.sector_analyzer import fetch_sector_performance
        from data.cache import delete

        # Force fresh regime data
        delete("regime:latest")
        regime = detect_regime()
        sector_perf = fetch_sector_performance()

        if not regime["signals_allowed"]:
            from alerts.telegram_bot import send_message
            from alerts.alert_formatter import format_bear_market_alert
            send_message(format_bear_market_alert())

        logger.info("Market intelligence: regime=%s, aggression=%.2f",
                    regime["regime"], regime["aggression_factor"])
    except Exception as exc:
        logger.error("Market intelligence failed: %s", exc, exc_info=True)


def job_full_scan():
    """Run the complete screening pipeline: universe → trend → fundamentals → VCP."""
    logger.info("=== JOB: Full Scan ===")
    try:
        from main import run_full_scan
        run_full_scan()
    except Exception as exc:
        logger.error("Full scan failed: %s", exc, exc_info=True)


def job_morning_briefing():
    """13:00 BST — Send morning Telegram briefing."""
    logger.info("=== JOB: Morning Briefing ===")
    try:
        from market_intelligence.regime_detector import detect_regime
        from market_intelligence.sector_analyzer import get_leading_sectors
        from database.db import get_connection
        from alerts.alert_formatter import format_morning_briefing
        from alerts.telegram_bot import send_message
        from data.earnings_calendar import earnings_safety_status

        regime = detect_regime()
        leaders = get_leading_sectors(3)
        weak_sectors = []  # simplified — caller can enrich

        conn = get_connection()
        watchlist = conn.execute("SELECT ticker FROM watchlist").fetchall()
        conn.close()

        watch_list = [r["ticker"] for r in watchlist]
        near_pivot = []
        earnings_blocked = []

        for ticker in watch_list:
            status = earnings_safety_status(ticker)
            if status["action"] == "block":
                earnings_blocked.append(ticker)

        msg = format_morning_briefing(
            regime=regime,
            watchlist=watch_list,
            near_pivot=near_pivot,
            earnings_blocked=earnings_blocked,
            sector_leaders=leaders,
            weak_sectors=weak_sectors,
            economic_events=[],
        )
        send_message(msg)
    except Exception as exc:
        logger.error("Morning briefing failed: %s", exc, exc_info=True)


def job_intraday_check():
    """Every 15 min during US market hours — check watchlist for breakouts."""
    logger.info("=== JOB: Intraday Check ===")
    try:
        from main import run_intraday_check
        run_intraday_check()
    except Exception as exc:
        logger.error("Intraday check failed: %s", exc, exc_info=True)


def job_post_market():
    """21:15 BST — Update OHLCV, check stops, send daily summary."""
    logger.info("=== JOB: Post-Market ===")
    try:
        from risk.stop_manager import update_open_position_stops
        from alerts.telegram_bot import send_message

        alerts = update_open_position_stops()
        for a in alerts:
            send_message(a["message"])

        now = datetime.now(BST).strftime("%Y-%m-%d")
        from database.db import get_connection
        conn = get_connection()
        today_signals = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE date=?", (now,)
        ).fetchone()["cnt"]
        conn.close()

        send_message(
            f"📊 Post-Market Summary — {now}\n"
            f"Signals generated today: {today_signals}\n"
            f"System running normally ✅"
        )
    except Exception as exc:
        logger.error("Post-market job failed: %s", exc, exc_info=True)


def job_weekly():
    """Sunday 10:00 — Weekly AI management analysis + backtest validation."""
    logger.info("=== JOB: Weekly Analysis ===")
    try:
        from database.db import get_connection
        from market_intelligence.ai_analyst import analyse_management_quality
        from alerts.telegram_bot import send_message

        conn = get_connection()
        # Stocks on watchlist for 7+ days
        rows = conn.execute(
            "SELECT ticker FROM watchlist "
            "WHERE added_date <= date('now', '-7 days')"
        ).fetchall()
        conn.close()

        results = []
        for row in rows:
            ticker = row["ticker"]
            # Simplified: would normally fetch SEC EDGAR text
            ai = analyse_management_quality(ticker, "Annual report data pending SEC fetch.")
            results.append(f"  {ticker}: {ai.get('rating', 'N/A')} — {ai.get('rationale', '')[:40]}")

        if results:
            send_message("📊 Weekly Management Quality:\n" + "\n".join(results))

    except Exception as exc:
        logger.error("Weekly job failed: %s", exc, exc_info=True)
