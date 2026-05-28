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
import time
from datetime import datetime

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

BST = pytz.timezone("Europe/London")


def create_scheduler() -> BackgroundScheduler:
    """Build and return a configured APScheduler instance (not yet started)."""
    scheduler = BackgroundScheduler(timezone=BST)

    # --- 08:00 BST Mon–Fri: Data refresh ---
    scheduler.add_job(
        job_data_refresh,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=BST),
        id="data_refresh",
        name="Data refresh + RS + Breadth",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 09:00 BST Mon–Fri: Market intelligence ---
    scheduler.add_job(
        job_market_intelligence,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=0, timezone=BST),
        id="market_intelligence",
        name="Regime + Sectors + Macro",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # --- 11:00 BST Mon–Fri: Full screening run #1 ---
    scheduler.add_job(
        job_full_scan,
        CronTrigger(day_of_week="mon-fri", hour=11, minute=0, timezone=BST),
        id="full_scan_1",
        name="Full screen #1",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 13:00 BST Mon–Fri: Morning briefing ---
    scheduler.add_job(
        job_morning_briefing,
        CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=BST),
        id="morning_briefing",
        name="Morning Telegram briefing",
        replace_existing=True,
        max_instances=1,
    )

    # --- 15:30 BST Mon–Fri: Full screening run #2 ---
    scheduler.add_job(
        job_full_scan,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=BST),
        id="full_scan_2",
        name="Full screen #2",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 19:00 BST Mon–Fri: Full screening run #3 ---
    scheduler.add_job(
        job_full_scan,
        CronTrigger(day_of_week="mon-fri", hour=19, minute=0, timezone=BST),
        id="full_scan_3",
        name="Full screen #3",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # --- 21:15 BST Mon–Fri: Post-market wrap ---
    scheduler.add_job(
        job_post_market,
        CronTrigger(day_of_week="mon-fri", hour=21, minute=15, timezone=BST),
        id="post_market",
        name="Post-market wrap",
        replace_existing=True,
        max_instances=1,
    )

    # --- Intraday: every 15 minutes, 13:30–21:00 BST Mon–Fri ---
    # hour="13-20" fires from 13:00 but run_intraday_check() has its own
    # 13:30 guard so the 13:00 and 13:15 firings are no-ops.
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
    _job_start = time.time()
    logger.info("=== JOB: Data Refresh ===")
    try:
        from data.cache import purge_expired
        from screening.universe import get_universe
        from data.fetcher import fetch_ohlcv_batch
        from screening.rs_calculator import compute_rs_ratings
        from market_intelligence.breadth_monitor import compute_breadth
        from database.db import cleanup_stale_watchlist
        from config import settings as _settings

        purge_expired()

        _t = time.time()
        universe = get_universe()
        logger.info("  Universe: %d tickers [%.1fs]", len(universe), time.time() - _t)

        _t = time.time()
        price_data = fetch_ohlcv_batch(universe, period="2y")
        coverage = len(price_data) / max(len(universe), 1) * 100
        logger.info("  OHLCV: %d/%d tickers (%.0f%% coverage) [%.1fs]",
                    len(price_data), len(universe), coverage, time.time() - _t)
        if coverage < 90:
            logger.warning("  LOW COVERAGE: only %.0f%% of universe downloaded — "
                           "API may be throttling or unavailable", coverage)

        _t = time.time()
        rs_df = compute_rs_ratings(price_data)
        rs_threshold = len(rs_df[rs_df["rs_rating"] >= 70]) if rs_df is not None and len(rs_df) else 0
        logger.info("  RS: %d tickers ranked, %d qualify (≥70) [%.1fs]",
                    len(rs_df) if rs_df is not None else 0, rs_threshold, time.time() - _t)

        _t = time.time()
        breadth = compute_breadth(price_data)
        logger.info("  Breadth: %.1f%% above 200-SMA [%.1fs]", breadth, time.time() - _t)

        # Remove watchlist entries that haven't been refreshed recently.
        removed = cleanup_stale_watchlist(max_age_days=_settings.WATCHLIST_MAX_AGE_DAYS)
        if removed:
            logger.info("  Watchlist: removed %d stale entries (not refreshed in %dd)",
                        removed, _settings.WATCHLIST_MAX_AGE_DAYS)

        logger.info("=== JOB: Data Refresh DONE in %.1fs ===", time.time() - _job_start)
    except Exception as exc:
        logger.error("Data refresh failed after %.1fs: %s",
                     time.time() - _job_start, exc, exc_info=True)


def job_market_intelligence():
    """09:00 BST — Regime detection, sector analysis, macro calendar."""
    _job_start = time.time()
    logger.info("=== JOB: Market Intelligence ===")
    try:
        from market_intelligence.regime_detector import detect_regime
        from market_intelligence.sector_analyzer import fetch_sector_performance
        from data.cache import delete

        # Force fresh regime data by deleting ALL stale underlying cache keys.
        # Previously only "regime:latest" was deleted, so regime recomputed on
        # stale SPY/QQQ/VIX/breadth data — defeating the forced refresh.
        for key in ("regime:latest", "ohlcv:SPY:2y", "ohlcv:QQQ:2y",
                    "vix:latest", "breadth:sp500_above_200sma",
                    "sector:performance"):
            delete(key)

        regime = detect_regime()
        sector_perf = fetch_sector_performance()

        if not regime["signals_allowed"]:
            from alerts.telegram_bot import send_message
            from alerts.alert_formatter import format_bear_market_alert
            # Pass the full regime dict so the alert shows the actual trigger reason
            # instead of always hardcoding "SPY below 200-day SMA"
            send_message(format_bear_market_alert(regime=regime))

        # Sector stage2 summary
        stage2_sectors = [s for s, d in sector_perf.items() if d.get("stage2")]
        weak_sectors_log = [s for s, d in sector_perf.items()
                            if not d.get("stage2") or d.get("3m_pct", 0) < 0]
        logger.info("  Stage 2 sectors (%d): %s", len(stage2_sectors),
                    ", ".join(stage2_sectors[:6]) + ("..." if len(stage2_sectors) > 6 else ""))
        if weak_sectors_log:
            logger.info("  Weak/non-Stage2 sectors: %s",
                        ", ".join(weak_sectors_log[:4]))
        logger.info("=== JOB: Market Intelligence DONE in %.1fs ===",
                    time.time() - _job_start)
    except Exception as exc:
        logger.error("Market intelligence failed after %.1fs: %s",
                     time.time() - _job_start, exc, exc_info=True)


def job_full_scan():
    """Run the complete screening pipeline: universe → trend → fundamentals → VCP."""
    _job_start = time.time()
    logger.info("=== JOB: Full Scan ===")
    try:
        from main import run_full_scan
        signals = run_full_scan()
        logger.info("=== JOB: Full Scan DONE in %.1fs | signals=%d ===",
                    time.time() - _job_start, len(signals) if signals else 0)
    except Exception as exc:
        logger.error("Full scan failed after %.1fs: %s",
                     time.time() - _job_start, exc, exc_info=True)


def job_morning_briefing():
    """13:00 BST — Send morning Telegram briefing."""
    logger.info("=== JOB: Morning Briefing ===")
    try:
        from market_intelligence.regime_detector import detect_regime
        from market_intelligence.sector_analyzer import get_leading_sectors, fetch_sector_performance
        from database.db import get_connection, remove_watchlist_ticker
        from alerts.alert_formatter import format_morning_briefing, format_earnings_assessment
        from alerts.telegram_bot import send_message
        from data.earnings_calendar import earnings_safety_status, assess_post_earnings
        from data.fetcher import fetch_latest_price
        from config import settings as _settings

        regime = detect_regime()
        leaders = get_leading_sectors(3)

        # Weak sectors: those whose ETF is NOT in Stage 2 (or declining 3m performance)
        sector_perf = fetch_sector_performance()
        weak_sectors = [
            s for s, d in sector_perf.items()
            if not d.get("stage2", False) or d.get("3m_pct", 0) < 0
        ]

        conn = get_connection()
        watchlist_rows = conn.execute(
            "SELECT ticker, pivot_price FROM watchlist WHERE pivot_price IS NOT NULL"
        ).fetchall()
        conn.close()

        watch_list = [r["ticker"] for r in watchlist_rows]
        near_pivot = []
        earnings_blocked = []

        for row in watchlist_rows:
            ticker = row["ticker"]
            pivot = row["pivot_price"]

            # Near-pivot check: live price within ±5% of stored pivot
            if pivot:
                try:
                    price = fetch_latest_price(ticker)
                    if price and abs(price / pivot - 1) <= _settings.NEAR_PIVOT_THRESHOLD:
                        near_pivot.append(ticker)
                except Exception:
                    pass

            # Earnings block check
            status = earnings_safety_status(ticker)
            if status["action"] == "block":
                earnings_blocked.append(ticker)

        # Post-earnings assessment: flag beats and remove misses from watchlist
        assessments = []
        for ticker in watch_list:
            try:
                result = assess_post_earnings(ticker)
                if result:
                    assessments.append(result)
                    if result["verdict"] == "EARNINGS MISS":
                        remove_watchlist_ticker(ticker)
                        logger.info("Watchlist: removed %s after earnings miss", ticker)
            except Exception as exc:
                logger.debug("Post-earnings check failed for %s: %s", ticker, exc)

        if assessments:
            send_message(format_earnings_assessment(assessments))

        # Fetch live economic events for this morning's briefing
        from data.economic_calendar import get_high_impact_events
        economic_events = get_high_impact_events(days_ahead=2)

        msg = format_morning_briefing(
            regime=regime,
            watchlist=watch_list,
            near_pivot=near_pivot,
            earnings_blocked=earnings_blocked,
            sector_leaders=leaders,
            weak_sectors=weak_sectors,
            economic_events=economic_events,
            vps_ip=_settings.VPS_IP,
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
    _job_start = time.time()
    logger.info("=== JOB: Post-Market ===")
    try:
        from risk.stop_manager import update_open_position_stops
        from alerts.telegram_bot import send_message

        alerts = update_open_position_stops()
        for a in alerts:
            send_message(a["message"])
            logger.info("  Stop alert sent: %s", a.get("ticker", "?"))
        if not alerts:
            logger.info("  No stops triggered today")

        now = datetime.now(BST).strftime("%Y-%m-%d")
        from database.db import get_connection
        conn = get_connection()
        today_signals = conn.execute(
            "SELECT COUNT(*) as cnt FROM signals WHERE date=?", (now,)
        ).fetchone()["cnt"]
        watchlist_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM watchlist"
        ).fetchone()["cnt"]
        conn.close()

        logger.info("  Day summary: signals=%d watchlist=%d stops_triggered=%d",
                    today_signals, watchlist_cnt, len(alerts))

        send_message(
            f"📊 Post-Market Summary — {now}\n"
            f"Signals generated today: {today_signals}\n"
            f"Watchlist size: {watchlist_cnt}\n"
            f"Stops triggered: {len(alerts)}\n"
            f"System running normally ✅"
        )
        logger.info("=== JOB: Post-Market DONE in %.1fs ===", time.time() - _job_start)
    except Exception as exc:
        logger.error("Post-market job failed after %.1fs: %s",
                     time.time() - _job_start, exc, exc_info=True)


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
            # Fetch SEC EDGAR data for management quality analysis
            try:
                from data.sec_edgar import get_sec_summary
                sec_data = get_sec_summary(ticker)
                report_text = sec_data.get("summary_text", "SEC data unavailable")
            except Exception as sec_exc:
                logger.debug("SEC fetch failed for %s: %s", ticker, sec_exc)
                report_text = "SEC data unavailable — manual review recommended"
            try:
                ai = analyse_management_quality(ticker, report_text)
                if ai.get("offline"):
                    logger.warning(
                        "Weekly job: Ollama offline — skipping AI management analysis for %s", ticker
                    )
                    continue
                results.append(f"  {ticker}: {ai.get('rating', 'N/A')} — {ai.get('rationale', '')[:40]}")
            except Exception as ai_exc:
                logger.warning(
                    "Weekly job: AI management quality failed for %s (%s) — skipping", ticker, ai_exc
                )

        if results:
            send_message("📊 Weekly Management Quality:\n" + "\n".join(results))

    except Exception as exc:
        logger.error("Weekly job failed: %s", exc, exc_info=True)
