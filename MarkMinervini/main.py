"""
Master entry point for the Minervini SEPA system.
Starts the APScheduler, runs an initial full scan, and loops indefinitely.

Usage:
    python main.py              # production mode (runs forever)
    python main.py --test-mode  # one scan + one Telegram message, then exits
"""

import argparse
import logging
import os
import sys
import time
from datetime import date

# ---------------------------------------------------------------------------
# Logging setup — must happen before any other imports that use logger
# ---------------------------------------------------------------------------
LOG_PATH = os.getenv("LOG_PATH", os.path.join(os.path.dirname(__file__), "logs", "sepa.log"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Core imports (after logging)
# ---------------------------------------------------------------------------
from config import settings
from database.db import init_db


def run_full_scan(test_mode: bool = False) -> list[dict]:
    """
    Execute the complete screening pipeline:
      Universe → RS Rank → Trend Template → Fundamentals → VCP → Earnings Gate → Regime Gate → Alert

    Returns list of signal dicts generated this run.
    """
    logger.info("=" * 60)
    logger.info("FULL SCAN STARTED")
    logger.info("=" * 60)
    start_time = time.time()

    signals_generated = []

    try:
        # --- Step 1: Universe + price data ---
        from screening.universe import get_universe
        from data.fetcher import fetch_ohlcv_batch, fetch_spy_ohlcv

        universe = get_universe()
        logger.info("Universe: %d tickers", len(universe))

        price_data = fetch_ohlcv_batch(universe, period="2y")
        logger.info("Price data loaded: %d tickers with sufficient history", len(price_data))

        spy_df = fetch_spy_ohlcv()

        # --- Step 2: RS Ratings (entire universe, vectorised) ---
        from screening.rs_calculator import compute_rs_ratings, check_rs_line_new_high

        rs_df = compute_rs_ratings(price_data)
        rs_map: dict = dict(zip(rs_df["ticker"], rs_df["rs_rating"]))

        # --- Step 3: Market Regime Gate ---
        from market_intelligence.regime_detector import detect_regime
        from market_intelligence.breadth_monitor import compute_breadth

        breadth = compute_breadth(price_data)
        regime = detect_regime(spy_df=spy_df, breadth_pct=breadth)

        logger.info("Regime: %s | Aggression: %.2f | Signals allowed: %s",
                    regime["regime"], regime["aggression_factor"], regime["signals_allowed"])

        if not regime["signals_allowed"]:
            logger.warning("Signals suppressed by regime gate: %s", regime["regime"])
            if not test_mode:
                return []

        # --- Step 4: Trend Template filter ---
        from screening.trend_template import check_trend_template

        trend_passed = []
        for ticker, df in price_data.items():
            rs = rs_map.get(ticker, 0.0)
            tt = check_trend_template(ticker, df, rs_rating=rs)
            if tt["passes"]:
                trend_passed.append((ticker, df, tt, rs))

        logger.info("Trend Template: %d/%d passed", len(trend_passed), len(price_data))

        # --- Step 5: Fundamentals filter ---
        from screening.fundamentals_filter import apply_fundamentals_filter

        fundamentals_passed = []
        for ticker, df, tt, rs in trend_passed:
            fund = apply_fundamentals_filter(ticker)
            if fund["passes"]:
                fundamentals_passed.append((ticker, df, tt, rs, fund))

        logger.info("Fundamentals: %d/%d passed", len(fundamentals_passed), len(trend_passed))

        # --- Step 6: Sector stage2 check + VCP ---
        from patterns.vcp_detector import detect_vcp
        from patterns.pivot_calculator import enrich_vcp_result
        from market_intelligence.sector_analyzer import get_sector_stage2_status
        from data.earnings_calendar import earnings_safety_status
        from risk.position_sizer import (compute_position_size,
                                          get_portfolio_drawdown,
                                          get_aggression_from_drawdown)
        from market_intelligence.ai_analyst import analyse_news_catalyst, analyse_earnings_quality
        from alerts.alert_formatter import format_breakout_alert
        from alerts.telegram_bot import send_message
        from database.db import insert_signal, mark_telegram_sent, upsert_watchlist

        drawdown = get_portfolio_drawdown()
        dd_aggression, dd_warning = get_aggression_from_drawdown(drawdown)
        final_aggression = min(regime["aggression_factor"], dd_aggression)

        if dd_warning:
            send_message(dd_warning)

        today = date.today().isoformat()
        scan_funnel = {
            "universe": len(universe),
            "price_data": len(price_data),
            "trend_template": len(trend_passed),
            "fundamentals": len(fundamentals_passed),
            "vcp": 0,
            "signals": 0,
        }

        for ticker, df, tt, rs, fund in fundamentals_passed:
            try:
                # RS line new high check
                rs_line_nh = False
                if spy_df is not None:
                    rs_line_nh = check_rs_line_new_high(df, spy_df)

                # VCP detection
                vcp = detect_vcp(ticker, df, trend_template_passes=True,
                                 rs_line_new_high=rs_line_nh)
                vcp = enrich_vcp_result(vcp)

                # Add to watchlist if score >= 70 (moderate VCP)
                if vcp["vcp_score"] >= 70:
                    scan_funnel["vcp"] += 1
                    sector_info = _get_ticker_sector(ticker)
                    upsert_watchlist(ticker, {
                        "ticker": ticker,
                        "company_name": sector_info.get("name", ticker),
                        "sector": sector_info.get("sector", "Unknown"),
                        "added_date": today,
                        "vcp_score": vcp["vcp_score"],
                        "pivot_price": vcp.get("pivot_price"),
                        "rs_rating": rs,
                        "eps_growth": fund.get("eps_growth_yoy"),
                        "rev_growth": fund.get("rev_growth_yoy"),
                        "earnings_date": None,
                        "ai_notes": None,
                        "last_updated": today,
                    })

                # Only alert on score >= 80
                if not vcp["alert"]:
                    continue

                # Sector stage2 gate
                sector_info = _get_ticker_sector(ticker)
                sector = sector_info.get("sector", "Unknown")
                if not get_sector_stage2_status(sector):
                    logger.info("Sector gate blocked %s (sector=%s not Stage 2)", ticker, sector)
                    continue

                # Earnings gate
                earn_status = earnings_safety_status(ticker)
                if earn_status["action"] == "block":
                    logger.info("Earnings blocked %s: %s", ticker, earn_status["message"])
                    continue

                # Position sizing
                position = compute_position_size(
                    entry_price=vcp["entry_price"],
                    stop_price=vcp["stop_price"],
                    account_equity=settings.ACCOUNT_EQUITY_GBP,
                    aggression_factor=final_aggression,
                    earnings_size_factor=earn_status["size_factor"],
                )

                if not position["valid"]:
                    logger.info("Position sizing rejected %s: %s", ticker, position["note"])
                    continue

                # AI analysis
                ai_news = analyse_news_catalyst(ticker)
                ai_earnings = analyse_earnings_quality(ticker, f"EPS growth {fund.get('eps_growth_yoy', 0):.1f}%")

                # Format and send alert
                company = sector_info.get("name", ticker)
                sector_stage2 = get_sector_stage2_status(sector)
                alert_msg = format_breakout_alert(
                    ticker=ticker,
                    company_name=company,
                    sector=sector,
                    sector_stage2=sector_stage2,
                    vcp=vcp,
                    trend=tt,
                    fundamentals=fund,
                    position=position,
                    regime=regime,
                    ai_news=ai_news,
                    ai_earnings=ai_earnings,
                    rs_rating=rs,
                    rs_line_new_high=rs_line_nh,
                    earnings_warning=earn_status["message"] if earn_status["action"] == "warn" else "",
                )

                # Save to database
                signal_id = insert_signal({
                    "ticker": ticker,
                    "date": today,
                    "signal_type": vcp["grade"],
                    "vcp_score": vcp["vcp_score"],
                    "pivot_price": vcp["pivot_price"],
                    "entry_price": vcp["entry_price"],
                    "stop_price": vcp["stop_price"],
                    "stop_pct": vcp["stop_pct"],
                    "target_1": vcp["target_1"],
                    "target_2": vcp["target_2"],
                    "rs_rating": rs,
                    "eps_growth": fund.get("eps_growth_yoy"),
                    "rev_growth": fund.get("rev_growth_yoy"),
                    "sector": sector,
                    "regime": regime["regime"],
                    "aggression_factor": final_aggression,
                    "ai_catalyst": ai_news.get("catalyst_type", "") if ai_news else "",
                    "ai_earnings_quality": ai_earnings.get("summary", "") if ai_earnings else "",
                    "ai_sentiment": ai_news.get("sentiment", "NEUTRAL") if ai_news else "N/A",
                })

                if send_message(alert_msg):
                    mark_telegram_sent(signal_id)

                scan_funnel["signals"] += 1
                signals_generated.append({
                    "ticker": ticker,
                    "vcp_score": vcp["vcp_score"],
                    "signal_id": signal_id,
                })
                logger.info("SIGNAL: %s | VCP=%d | Entry=$%.2f",
                            ticker, vcp["vcp_score"], vcp["entry_price"])

            except Exception as exc:
                logger.error("Pipeline error for %s: %s", ticker, exc, exc_info=True)

        elapsed = time.time() - start_time
        logger.info("SCAN COMPLETE in %.1fs | Funnel: Universe=%d → Trend=%d → "
                    "Fundamentals=%d → VCP=%d → Signals=%d",
                    elapsed,
                    scan_funnel["universe"],
                    scan_funnel["trend_template"],
                    scan_funnel["fundamentals"],
                    scan_funnel["vcp"],
                    scan_funnel["signals"])

        # Log scan funnel to DB
        _log_scan_funnel(scan_funnel, elapsed)

    except Exception as exc:
        logger.error("Full scan crashed: %s", exc, exc_info=True)

    return signals_generated


def run_intraday_check():
    """
    Intraday check (every 15 min, 13:30–21:00 BST).
    Re-check watchlist stocks: if today's close > pivot AND volume >= 1.4x avg → alert.
    """
    logger.info("--- Intraday check ---")
    try:
        from database.db import get_connection
        from data.fetcher import fetch_ohlcv
        from patterns.vcp_detector import detect_vcp

        conn = get_connection()
        watchlist = conn.execute(
            "SELECT ticker, pivot_price FROM watchlist WHERE vcp_score >= 80"
        ).fetchall()
        conn.close()

        for row in watchlist:
            ticker = row["ticker"]
            pivot = row["pivot_price"]
            if not pivot:
                continue
            try:
                df = fetch_ohlcv(ticker, period="3mo")
                if df is None or len(df) < 20:
                    continue

                today_close = float(df["Close"].iloc[-1])
                today_vol = float(df["Volume"].iloc[-1])
                avg_vol_50 = float(df["Volume"].iloc[-50:].mean()) if len(df) >= 50 else float(df["Volume"].mean())

                # Check for intraday breakout
                if today_close > pivot and today_vol >= avg_vol_50 * settings.BREAKOUT_VOLUME_RATIO:
                    logger.info("INTRADAY BREAKOUT: %s @ $%.2f (vol %.1fx avg)",
                                ticker, today_close, today_vol / avg_vol_50)
                    # Full VCP re-run to get current score and trigger alert if warranted
                    # (avoids sending duplicate alerts — check if already sent today)
                    today = date.today().isoformat()
                    conn2 = get_connection()
                    already_alerted = conn2.execute(
                        "SELECT id FROM signals WHERE ticker=? AND date=? AND telegram_sent=1",
                        (ticker, today),
                    ).fetchone()
                    conn2.close()
                    if not already_alerted:
                        run_full_scan()  # re-run will handle the alert properly
                        break  # avoid cascade of re-runs in same 15-min window

            except Exception as exc:
                logger.debug("Intraday check error for %s: %s", ticker, exc)

    except Exception as exc:
        logger.error("Intraday check failed: %s", exc, exc_info=True)


def _get_ticker_sector(ticker: str) -> dict:
    """Return basic metadata for a ticker (name + sector). Uses yfinance info cache."""
    from data.cache import get as cache_get, set as cache_set, TTL_7D

    cache_key = f"ticker_info:{ticker}"
    cached = cache_get(cache_key)
    if cached:
        return cached
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
        result = {
            "name": info.get("longName", ticker),
            "sector": info.get("sector", "Unknown"),
        }
        cache_set(cache_key, result, ttl_seconds=TTL_7D)
        return result
    except Exception:
        return {"name": ticker, "sector": "Unknown"}


def _log_scan_funnel(funnel: dict, elapsed: float) -> None:
    """Write scan funnel metrics to system_log table."""
    import json
    from database.db import db_session

    try:
        with db_session() as conn:
            conn.execute(
                "INSERT INTO system_log(level, module, message) VALUES(?,?,?)",
                ("INFO", "scanner",
                 f"Scan funnel: {json.dumps(funnel)} | elapsed={elapsed:.1f}s"),
            )
    except Exception as exc:
        logger.debug("Failed to log scan funnel: %s", exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Minervini SEPA Signal System")
    parser.add_argument("--test-mode", action="store_true",
                        help="Run one scan, send one Telegram message, then exit")
    args = parser.parse_args()

    logger.info("Starting Minervini SEPA System v3.0")
    logger.info("DB: %s | Log: %s", settings.DB_PATH, LOG_PATH)

    # Initialise database
    init_db()

    # Send startup confirmation
    from alerts.telegram_bot import send_startup_message, is_telegram_available
    tg_ok = is_telegram_available()
    logger.info("Telegram: %s", "OK" if tg_ok else "NOT CONFIGURED")
    if tg_ok:
        send_startup_message()

    if args.test_mode:
        logger.info("TEST MODE: running one scan then exiting")
        signals = run_full_scan(test_mode=True)
        logger.info("Test mode complete. Signals: %d", len(signals))
        sys.exit(0)

    # Production mode: start scheduler + loop
    from scheduler.job_runner import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started with %d jobs", len(scheduler.get_jobs()))

    # Run initial full scan immediately on startup
    logger.info("Running initial scan on startup...")
    run_full_scan()

    # Keep alive
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received — stopping scheduler")
        scheduler.shutdown(wait=False)
        logger.info("SEPA system stopped")


if __name__ == "__main__":
    main()
