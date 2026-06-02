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
from datetime import date, datetime, time as dtime

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
    _step_times: dict[str, float] = {}   # step_name → elapsed seconds

    try:
        # --- Step 1: Universe + price data ---
        from screening.universe import get_universe
        from data.fetcher import fetch_ohlcv_batch, fetch_spy_ohlcv

        _t0 = time.time()
        universe = get_universe()
        logger.info("Universe: %d tickers", len(universe))

        price_data = fetch_ohlcv_batch(universe, period="2y")
        _step_times["data_fetch"] = time.time() - _t0
        logger.info("Price data loaded: %d tickers with sufficient history [%.1fs]",
                    len(price_data), _step_times["data_fetch"])

        # Universe coverage check — abort if fewer than 80% of tickers loaded.
        # A mass API failure would silently produce meaningless RS rankings that
        # make every stock look like a leader (or bottom-dweller).
        coverage_pct = len(price_data) / max(len(universe), 1) * 100
        if len(price_data) < len(universe) * 0.8:
            logger.error(
                "Universe coverage too low: %d/%d tickers loaded (%.0f%%). "
                "API failure suspected — aborting scan to prevent misleading RS rankings.",
                len(price_data), len(universe), coverage_pct,
            )
            return signals_generated
        elif coverage_pct < 95:
            logger.warning(
                "Universe coverage %.0f%% (%d/%d). Some RS rankings may be slightly skewed.",
                coverage_pct, len(price_data), len(universe),
            )

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

        # Bear mode: continue scanning and building the watchlist so we are ready
        # to act when conditions improve.  Only suppress alert SENDING.
        # Previously `return []` in bear mode silently stopped all scanning,
        # leaving the watchlist stale and blocking all watchlist-building.
        signals_suppressed = not regime["signals_allowed"]
        if signals_suppressed:
            logger.warning(
                "BEAR/NEUTRAL SUPPRESSION: Scanning continues but alerts are suppressed. "
                "Reason: %s", regime.get("regime_summary", regime["regime"])
            )

        # --- Step 4: Trend Template filter ---
        from screening.trend_template import check_trend_template

        _t0 = time.time()
        trend_passed = []
        _tt_crit_fails: dict[str, int] = {}  # criterion → count of stocks failing it
        _tt_score_dist: dict[int, int] = {}  # score → count (0–8)
        for ticker, df in price_data.items():
            rs = rs_map.get(ticker, 0.0)
            tt = check_trend_template(ticker, df, rs_rating=rs)
            if tt["passes"]:
                trend_passed.append((ticker, df, tt, rs))
            else:
                d = tt.get("details", {})
                for crit, label in (
                    ("c1_price_above_sma50",  "C1-above-SMA50"),
                    ("c2_price_above_sma150", "C2-above-SMA150"),
                    ("c3_price_above_sma200", "C3-above-SMA200"),
                    ("c4_sma200_rising",      "C4-SMA200-rising"),
                    ("c5_ma_stack",           "C5-MA-stack"),
                    ("c6_near_52wk_high",     "C6-near-52wk-high"),
                    ("c7_above_52wk_low",     "C7-above-52wk-low"),
                    ("c8_rs_rating",          "C8-RS≥70"),
                ):
                    if not d.get(crit, True):
                        _tt_crit_fails[label] = _tt_crit_fails.get(label, 0) + 1
            s = tt.get("score", 0)
            _tt_score_dist[s] = _tt_score_dist.get(s, 0) + 1

        _step_times["trend_template"] = time.time() - _t0
        logger.info("Trend Template: %d/%d passed [%.1fs]",
                    len(trend_passed), len(price_data), _step_times["trend_template"])

        # Capture TT-passed ticker details for the Scan Funnel dashboard page.
        _tt_funnel_data = [
            {"ticker": t, "scan_date": today, "rs_rating": rs, "tt_score": tt.get("score", 0)}
            for t, df, tt, rs in trend_passed
        ]

        # Log the top criterion failure reasons so we know what's blocking stocks
        if _tt_crit_fails:
            _top_fails = sorted(_tt_crit_fails.items(), key=lambda x: -x[1])[:5]
            logger.info("  TT failures by criterion: %s",
                        " | ".join(f"{lbl}={cnt}" for lbl, cnt in _top_fails))
        # Log score distribution — e.g. "7/8: 45 stocks  6/8: 92 stocks"
        if _tt_score_dist:
            _dist_str = "  ".join(
                f"{s}/8:{c}"
                for s, c in sorted(_tt_score_dist.items(), reverse=True)
                if s >= 5
            )
            if _dist_str:
                logger.info("  TT score dist (≥5/8): %s", _dist_str)

        # --- Step 5: Fundamentals filter ---
        from screening.fundamentals_filter import apply_fundamentals_filter

        _t0 = time.time()
        fundamentals_passed = []
        _fund_rejection_sample: dict[str, int] = {}  # reason → count
        _fund_sources: dict[str, int] = {}  # data source → count
        for ticker, df, tt, rs in trend_passed:
            fund = apply_fundamentals_filter(ticker)
            if fund["passes"]:
                fundamentals_passed.append((ticker, df, tt, rs, fund))
                # Track which source provided data
                src = fund.get("details", {}).get("status", "unknown")
                _fund_sources[src] = _fund_sources.get(src, 0) + 1
            else:
                reason = fund.get("rejection_reason", "unknown")
                key = reason[:60]
                _fund_rejection_sample[key] = _fund_rejection_sample.get(key, 0) + 1

        _step_times["fundamentals"] = time.time() - _t0
        logger.info("Fundamentals: %d/%d passed [%.1fs]",
                    len(fundamentals_passed), len(trend_passed), _step_times["fundamentals"])

        # Capture fundamentals-passed ticker details for the Scan Funnel dashboard page.
        _fund_funnel_data = [
            {
                "ticker": t, "scan_date": today, "rs_rating": rs,
                "tt_score": tt.get("score", 0),
                "eps_growth": f.get("eps_growth_yoy"),
                "rev_growth": f.get("rev_growth_yoy"),
            }
            for t, df, tt, rs, f in fundamentals_passed
        ]

        if _fund_sources:
            logger.info("  Fundamentals sources: %s",
                        " | ".join(f"{s}={c}" for s, c in _fund_sources.items()))

        # If every single stock failed fundamentals, that is almost certainly an API
        # key configuration problem rather than genuinely bad fundamentals — alert loudly.
        if len(fundamentals_passed) == 0 and len(trend_passed) > 0:
            logger.warning(
                "FUNDAMENTALS BOTTLENECK: 0/%d stocks passed. This typically means "
                "FINNHUB_API_KEY and ALPHA_VANTAGE_KEY are not set in your environment. "
                "Top rejection reasons: %s. "
                "Set REQUIRE_FUNDAMENTALS=false in your environment to bypass this gate "
                "and scan with Trend Template + VCP only.",
                len(trend_passed),
                dict(list(_fund_rejection_sample.items())[:3]),
            )
            # Bypass mode: if the env variable REQUIRE_FUNDAMENTALS is set to "false",
            # allow all trend_passed stocks through with a dummy fundamentals dict.
            if os.getenv("REQUIRE_FUNDAMENTALS", "true").lower() == "false":
                logger.warning(
                    "REQUIRE_FUNDAMENTALS=false: bypassing fundamentals filter, "
                    "scanning %d trend-template-passed stocks with VCP only.",
                    len(trend_passed),
                )
                _dummy_fund = {
                    "passes": True, "fundamentals_score": 0,
                    "status": "bypassed", "eps_growth_yoy": None,
                    "rev_growth_yoy": None, "gross_margin_current": None,
                    "gross_margin_prior": None, "roe": None, "details": {},
                    "rejection_reason": None,
                }
                fundamentals_passed = [(t, d, tt, rs, _dummy_fund) for t, d, tt, rs in trend_passed]

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
        from database.db import insert_signal, mark_telegram_sent, upsert_watchlist, insert_setup

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
            "watchlist": 0,
            "signals": 0,
        }

        import json as _json
        _vcp_rejections: dict[str, int] = {}   # rejection reason bucket → count
        _vcp_score_dist: dict[str, int] = {}   # "0-19","20-39",... → count
        _developing_tickers: list[dict] = []   # has base ≥15d, not yet VCP-quality
        _t0_vcp = time.time()

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

                # Fetch sector/name info once per ticker (SQLite-cached TTL_7D,
                # so effectively free on repeated calls; avoids the double-call
                # pattern where it was fetched again at the alert gate below).
                sector_info = _get_ticker_sector(ticker)

                # Track VCP outcome for diagnostic summary
                _vscore = vcp["vcp_score"]
                _vbucket = f"{(_vscore // 20) * 20}-{(_vscore // 20) * 20 + 19}"
                _vcp_score_dist[_vbucket] = _vcp_score_dist.get(_vbucket, 0) + 1
                if not vcp["watchlist_candidate"]:
                    _rej = (vcp.get("rejection_reason") or f"score={_vscore}<70")[:55]
                    _vcp_rejections[_rej] = _vcp_rejections.get(_rej, 0) + 1
                    # Track stocks that have a real base but aren't VCP-quality yet.
                    # These are "in the oven" — watch them daily as they tighten.
                    if (vcp.get("base_days") or 0) >= settings.MIN_BASE_TRADING_DAYS:
                        _developing_tickers.append({
                            "ticker": ticker,
                            "scan_date": today,
                            "rs_rating": rs,
                            "tt_score": tt.get("score", 0),
                            "eps_growth": fund.get("eps_growth_yoy"),
                            "rev_growth": fund.get("rev_growth_yoy"),
                            "base_days": vcp.get("base_days", 0),
                            # num_contractions is in steps even when Step 5 fails early
                            "contractions": vcp.get("steps", {}).get("num_contractions", 0),
                            "vcp_score": vcp.get("vcp_score", 0),
                            "rejection_reason": vcp.get("rejection_reason", ""),
                        })

                # Add to watchlist/setups if score >= 70 (moderate VCP — setup forming)
                if vcp["vcp_score"] >= 70:
                    scan_funnel["vcp"] += 1
                    scan_funnel["watchlist"] += 1
                    upsert_watchlist(ticker, {
                        "ticker": ticker,
                        "company_name": sector_info.get("name", ticker),
                        "sector": sector_info.get("sector", "Unknown"),
                        "added_date": today,
                        "vcp_score": vcp["vcp_score"],
                        "grade": vcp.get("grade"),
                        "pivot_price": vcp.get("pivot_price"),
                        "entry_price": vcp.get("entry_price"),
                        "stop_price": vcp.get("stop_price"),
                        "stop_pct": vcp.get("stop_pct"),
                        "target_1": vcp.get("target_1"),
                        "target_2": vcp.get("target_2"),
                        "base_days": vcp.get("base_days"),
                        "rs_rating": rs,
                        "rs_line_new_high": 1 if rs_line_nh else 0,
                        "eps_growth": fund.get("eps_growth_yoy"),
                        "rev_growth": fund.get("rev_growth_yoy"),
                        "fundamentals_score": fund.get("fundamentals_score"),
                        "earnings_date": None,
                        "ai_notes": None,
                        "breakout_confirmed": 1 if vcp.get("breakout_confirmed") else 0,
                        "last_updated": today,
                    })
                    # Also persist a full setup snapshot for audit trail and intraday engine
                    insert_setup({
                        "ticker": ticker,
                        "date": today,
                        "vcp_score": vcp["vcp_score"],
                        "grade": vcp.get("grade"),
                        "pivot_price": vcp.get("pivot_price"),
                        "entry_price": vcp.get("entry_price"),
                        "stop_price": vcp.get("stop_price"),
                        "stop_pct": vcp.get("stop_pct"),
                        "target_1": vcp.get("target_1"),
                        "target_2": vcp.get("target_2"),
                        "rs_rating": rs,
                        "rs_line_new_high": 1 if rs_line_nh else 0,
                        "base_days": vcp.get("base_days"),
                        "contractions_json": _json.dumps(vcp.get("contractions", [])),
                        "vcp_steps_json": _json.dumps(vcp.get("steps", {})),
                        "fundamentals_json": _json.dumps({
                            "eps_growth_yoy": fund.get("eps_growth_yoy"),
                            "rev_growth_yoy": fund.get("rev_growth_yoy"),
                            "gross_margin_current": fund.get("gross_margin_current"),
                            "fundamentals_score": fund.get("fundamentals_score"),
                        }),
                        "trend_json": _json.dumps(tt.get("details", {})),
                        "sector": sector_info.get("sector", "Unknown"),
                        "status": "watchlist",
                    })

                # Only alert on score >= 80
                if not vcp["alert"]:
                    continue

                # Bear / suppression gate: allow watchlist building above,
                # but do not send alerts when regime has disabled signals.
                if signals_suppressed:
                    logger.info(
                        "Alert suppressed (regime=%s): %s | VCP=%d",
                        regime["regime"], ticker, vcp["vcp_score"]
                    )
                    continue

                # Sector stage2 gate — result saved to avoid a second call when
                # formatting the alert (previously called twice: gate check + formatter arg)
                sector = sector_info.get("sector", "Unknown")
                sector_stage2 = get_sector_stage2_status(sector)
                if not sector_stage2:
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
                # sector_stage2 already fetched above — reuse it (no redundant call)
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
                    vps_ip=settings.VPS_IP,
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

        _step_times["vcp"] = time.time() - _t0_vcp
        elapsed = time.time() - start_time

        # ── SCAN REPORT ─────────────────────────────────────────────────────
        logger.info("=" * 60)
        logger.info("SCAN COMPLETE in %.1fs", elapsed)
        logger.info("  Funnel: Universe=%d → PriceData=%d → TrendTemplate=%d"
                    " → Fundamentals=%d → VCP(≥70)=%d → Watchlist=%d → Signals=%d",
                    scan_funnel["universe"], scan_funnel["price_data"],
                    scan_funnel["trend_template"], scan_funnel["fundamentals"],
                    scan_funnel["vcp"], scan_funnel["watchlist"], scan_funnel["signals"])
        logger.info("  Step times: data=%.1fs  TT=%.1fs  fund=%.1fs  VCP=%.1fs",
                    _step_times.get("data_fetch", 0), _step_times.get("trend_template", 0),
                    _step_times.get("fundamentals", 0), _step_times.get("vcp", 0))
        logger.info("  Regime: %s | VIX=%.1f | Breadth=%.1f%% | DistDays=%d | FTD=%s",
                    regime["regime"], regime.get("vix_level", 0),
                    regime.get("breadth_pct") or 0, regime.get("distribution_days", 0),
                    regime.get("ftd_confirmed", "?"))
        if _tt_crit_fails:
            _top = sorted(_tt_crit_fails.items(), key=lambda x: -x[1])[:4]
            logger.info("  TT top blocks: %s", " | ".join(f"{l}={c}" for l, c in _top))
        if fundamentals_passed or _fund_rejection_sample:
            _top_fund = sorted(_fund_rejection_sample.items(), key=lambda x: -x[1])[:3]
            if _top_fund:
                logger.info("  Fund top blocks: %s",
                            " | ".join(f'"{r}"={c}' for r, c in _top_fund))
        if _vcp_rejections:
            _top_vcp = sorted(_vcp_rejections.items(), key=lambda x: -x[1])[:4]
            logger.info("  VCP top blocks: %s",
                        " | ".join(f'"{r}"={c}' for r, c in _top_vcp))
        if _vcp_score_dist:
            logger.info("  VCP score dist: %s",
                        " | ".join(f"{b}:{c}" for b, c in
                                   sorted(_vcp_score_dist.items())))
        _dev_names = [d["ticker"] for d in _developing_tickers]
        if _developing_tickers:
            logger.info(
                "  Developing setups (%d with base ≥%dd, not yet VCP-quality): %s%s",
                len(_developing_tickers),
                settings.MIN_BASE_TRADING_DAYS,
                ", ".join(_dev_names[:10]),
                f" (+{len(_dev_names) - 10} more)" if len(_dev_names) > 10 else "",
            )
        else:
            logger.info("  Developing setups: 0 (all %d fund-pass stocks have base < %dd"
                        " — market at highs, no consolidation yet)",
                        scan_funnel["fundamentals"], settings.MIN_BASE_TRADING_DAYS)
        logger.info("=" * 60)
        # ────────────────────────────────────────────────────────────────────

        # Log scan funnel to DB
        _log_scan_funnel(scan_funnel, elapsed)

        # Persist per-stage ticker data so the Scan Funnel dashboard page can
        # show exactly which stocks cleared each stage of the pipeline.
        try:
            from database.db import save_scan_funnel_stage
            save_scan_funnel_stage("trend_template", _tt_funnel_data)
            save_scan_funnel_stage("fundamentals", _fund_funnel_data)
            save_scan_funnel_stage("developing_vcp", _developing_tickers)
        except Exception as _fe:
            logger.debug("Scan funnel stage save failed (non-fatal): %s", _fe)

    except Exception as exc:
        logger.error("Full scan crashed: %s", exc, exc_info=True)

    return signals_generated


def run_intraday_check():
    """
    Intraday check (every 15 min, 13:30–21:00 BST).

    Uses real intraday 5-minute bars (not daily bars) to detect breakouts.
    Volume is projected to full-day equivalent using elapsed session time,
    then compared to 50-day average volume to validate the breakout.

    Guard: exits early if called before 13:30 or after 21:00 BST.
    """
    import pytz
    BST = pytz.timezone("Europe/London")
    now_bst = datetime.now(BST).time()

    # Market-hours guard — intraday check only valid 13:30–21:00 BST
    if now_bst < dtime(13, 30) or now_bst > dtime(21, 0):
        logger.debug("Intraday check called outside market hours (%s BST) — skipping", now_bst)
        return

    logger.info("--- Intraday check @ %s BST ---", now_bst)
    try:
        from database.db import get_connection
        from data.fetcher import fetch_ohlcv, fetch_intraday_ohlcv

        conn = get_connection()
        watchlist = conn.execute(
            "SELECT ticker, pivot_price, entry_price, stop_price, stop_pct, "
            "target_1, target_2, vcp_score, rs_rating, eps_growth, rev_growth, sector "
            "FROM watchlist WHERE vcp_score >= 70"
        ).fetchall()
        conn.close()

        # Compute elapsed fraction of US session (13:30–21:00 BST = 7.5 hours)
        market_open_bst = dtime(13, 30)
        market_close_bst = dtime(21, 0)
        session_total_secs = (
            datetime.combine(date.today(), market_close_bst)
            - datetime.combine(date.today(), market_open_bst)
        ).total_seconds()
        elapsed_secs = max(60, (
            datetime.combine(date.today(), now_bst)
            - datetime.combine(date.today(), market_open_bst)
        ).total_seconds())
        fraction_elapsed = min(1.0, elapsed_secs / session_total_secs)

        today = date.today().isoformat()

        for row in watchlist:
            ticker = row["ticker"]
            pivot = row["pivot_price"]
            if not pivot:
                continue
            try:
                # Real intraday 5-minute bars for current price and volume
                intraday_df = fetch_intraday_ohlcv(ticker, interval="5m")
                if intraday_df is None or len(intraday_df) < 3:
                    continue

                current_price = float(intraday_df["Close"].iloc[-1])
                intraday_vol = float(intraday_df["Volume"].sum())

                # Project intraday volume to full-day equivalent
                projected_vol = intraday_vol / fraction_elapsed if fraction_elapsed > 0 else 0

                # 50-day average volume from daily data for comparison
                daily_df = fetch_ohlcv(ticker, period="3mo")
                if daily_df is None or len(daily_df) < 20:
                    continue
                avg_vol_50 = float(daily_df["Volume"].iloc[-50:].mean()) if len(daily_df) >= 50 else float(daily_df["Volume"].mean())

                volume_pace_ratio = projected_vol / avg_vol_50 if avg_vol_50 > 0 else 0

                # Breakout condition: price > pivot AND projected volume >= 1.4× avg
                if current_price > pivot and volume_pace_ratio >= settings.BREAKOUT_VOLUME_RATIO:
                    logger.info(
                        "INTRADAY BREAKOUT: %s @ $%.2f (pivot=$%.2f, vol pace=%.1fx avg)",
                        ticker, current_price, pivot, volume_pace_ratio
                    )
                    # Deduplicate: block if already alerted today
                    conn2 = get_connection()
                    already_alerted = conn2.execute(
                        "SELECT id FROM signals WHERE ticker=? AND date=? AND telegram_sent=1",
                        (ticker, today),
                    ).fetchone()
                    conn2.close()
                    if not already_alerted:
                        _send_intraday_breakout_alert(
                            ticker, row, current_price, volume_pace_ratio, avg_vol_50, today
                        )
                        break  # one alert per intraday window

            except Exception as exc:
                logger.debug("Intraday check error for %s: %s", ticker, exc)

    except Exception as exc:
        logger.error("Intraday check failed: %s", exc, exc_info=True)


def _send_intraday_breakout_alert(
    ticker: str,
    watchlist_row,
    current_price: float,
    volume_pace_ratio: float,
    avg_vol_50: float,
    today: str,
) -> None:
    """
    Send an intraday breakout alert using stored watchlist/setup data.

    Unlike the daily scan, this does NOT re-fetch daily bars or re-run the full
    VCP algorithm. It uses the already-stored VCP setup values (pivot, stop, entry,
    targets, score) and only substitutes the live intraday price and volume pace.

    Final gates still enforced: regime, sector, earnings, position sizing.
    """
    from market_intelligence.regime_detector import detect_regime
    from market_intelligence.sector_analyzer import get_sector_stage2_status, normalise_sector
    from data.earnings_calendar import earnings_safety_status
    from risk.position_sizer import compute_position_size, get_portfolio_drawdown, get_aggression_from_drawdown
    from database.db import insert_signal, mark_telegram_sent
    from alerts.telegram_bot import send_message

    try:
        regime = detect_regime()
        if not regime["signals_allowed"]:
            logger.info("Intraday alert suppressed for %s: regime=%s", ticker, regime["regime"])
            return

        sector = watchlist_row["sector"] or "Unknown"
        canonical_sector = normalise_sector(sector)
        if not get_sector_stage2_status(canonical_sector):
            logger.info("Intraday alert suppressed for %s: sector %s not Stage 2", ticker, sector)
            return

        earn_status = earnings_safety_status(ticker)
        if earn_status["action"] == "block":
            logger.info("Intraday alert blocked for %s: %s", ticker, earn_status["message"])
            return

        drawdown = get_portfolio_drawdown()
        dd_aggression, dd_warning = get_aggression_from_drawdown(drawdown)
        final_aggression = min(regime["aggression_factor"], dd_aggression)

        if final_aggression == 0.0:
            logger.info("Intraday alert suppressed for %s: aggression=0 (drawdown circuit)", ticker)
            return

        # Use stored setup values; live entry is current_price (already above pivot)
        pivot = watchlist_row["pivot_price"] or current_price
        stop_price = watchlist_row["stop_price"]
        entry_price = current_price  # use live intraday price as entry

        if not stop_price or entry_price <= stop_price:
            logger.info("Intraday alert invalid for %s: stop=%s entry=%s", ticker, stop_price, entry_price)
            return

        position = compute_position_size(
            entry_price=entry_price,
            stop_price=stop_price,
            account_equity=settings.ACCOUNT_EQUITY_GBP,
            aggression_factor=final_aggression,
            earnings_size_factor=earn_status["size_factor"],
        )
        if not position["valid"]:
            logger.info("Intraday sizing invalid for %s: %s", ticker, position["note"])
            return

        vcp_score = watchlist_row["vcp_score"] or 0
        # Recalculate stop_pct from the live intraday entry and stored stop.
        # The watchlist value was computed at setup entry — if the stock has
        # already moved above that price, the stored percentage is stale and
        # would understate true risk to the trader.
        risk_per_share = entry_price - stop_price
        stop_pct = risk_per_share / entry_price * 100
        target_1 = entry_price + 2 * risk_per_share
        target_2 = entry_price + 3 * risk_per_share

        # Build message as a list of lines — avoids Python precedence bugs with
        # conditional expressions inside implicit f-string concatenation blocks.
        msg_lines = [
            f"🚨 INTRADAY BREAKOUT ALERT — {ticker}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Price:    ${current_price:.2f}  (pivot ${pivot:.2f})",
            f"Volume:   {volume_pace_ratio:.1f}× average (projected full-day)",
            f"VCP Score: {vcp_score}",
            "",
            f"ENTRY:    ${entry_price:.2f}",
            f"STOP:     ${stop_price:.2f}  (-{stop_pct:.1f}%)",
            f"T1 (2R):  ${target_1:.2f}",
            f"T2 (3R):  ${target_2:.2f}",
            "",
            f"SIZE ({settings.RISK_PER_TRADE_PCT*100:.1f}% risk, £{settings.ACCOUNT_EQUITY_GBP:,.0f}):",
            f"  Shares: {position['shares']:,}",
            (
                f"  Position: ${position['position_value_usd']:,.0f} USD"
                f" / £{position['position_value_gbp']:,.0f} GBP"
                f" ({position['position_pct']:.1f}%)"
            ),
            f"  Max loss: £{position['risk_gbp']:,.0f} / ${position['risk_usd']:,.0f}",
        ]
        if position.get("fx_warning"):
            msg_lines.append("  ⚠️ FX fallback rate — verify size manually")
        msg_lines += [
            "",
            "⚠️ Execute MANUALLY in Trading 212 ISA",
            f"Signal: INTRADAY_BREAKOUT | Regime: {regime['regime']}",
        ]
        intraday_msg = "\n".join(msg_lines)
        if earn_status["action"] == "warn":
            intraday_msg += f"\n⚠️ {earn_status['message']}"
        if regime.get("high_impact_event_imminent"):
            intraday_msg += f"\n⚠️ High-impact macro event imminent — reduced size applied"

        signal_id = insert_signal({
            "ticker": ticker,
            "date": today,
            "signal_type": "INTRADAY_BREAKOUT",
            "vcp_score": vcp_score,
            "pivot_price": pivot,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "stop_pct": stop_pct,
            "target_1": target_1,
            "target_2": target_2,
            "rs_rating": watchlist_row["rs_rating"],
            "eps_growth": watchlist_row["eps_growth"],
            "rev_growth": watchlist_row["rev_growth"],
            "sector": sector,
            "regime": regime["regime"],
            "aggression_factor": final_aggression,
            "ai_catalyst": "",
            "ai_earnings_quality": "",
            "ai_sentiment": "N/A",
        })

        if send_message(intraday_msg):
            mark_telegram_sent(signal_id)
            logger.info("INTRADAY BREAKOUT ALERT SENT: %s @ $%.2f", ticker, current_price)

    except Exception as exc:
        logger.error("Intraday alert failed for %s: %s", ticker, exc, exc_info=True)


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


def _write_heartbeat() -> None:
    """Write a scanner heartbeat to system_status so the dashboard can detect stale scanner."""
    from database.db import db_session
    try:
        with db_session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_status(key, value, updated_at) "
                "VALUES('scanner_heartbeat', 'alive', CURRENT_TIMESTAMP)"
            )
    except Exception as exc:
        logger.debug("Heartbeat write failed: %s", exc)


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

    _git_commit = os.getenv("GIT_COMMIT", "dev")
    _build_date = os.getenv("BUILD_DATE", "local build")
    logger.info(
        "Starting Minervini SEPA System v3.0 | commit=%s | built=%s",
        _git_commit[:8] if len(_git_commit) > 8 else _git_commit,
        _build_date,
    )
    logger.info("DB: %s | Log: %s | Level: %s", settings.DB_PATH, LOG_PATH, LOG_LEVEL)
    logger.info(
        "Config: EPS_min=%d%% Rev_min=%d%% VCP_min=%d RS_min=%d "
        "Backtest=%s→%s Account=£%s",
        settings.EPS_GROWTH_MIN, settings.REVENUE_GROWTH_MIN,
        settings.VCP_SCORE_MIN, settings.RS_MINIMUM,
        settings.BACKTEST_START, settings.BACKTEST_END,
        f"{settings.ACCOUNT_EQUITY_GBP:,.0f}",
    )
    logger.info(
        "API keys: Finnhub=%s AlphaVantage=%s Telegram=%s",
        "SET" if settings.FINNHUB_API_KEY else "NOT SET",
        "SET" if settings.ALPHA_VANTAGE_KEY else "NOT SET",
        "SET" if settings.TELEGRAM_BOT_TOKEN else "NOT SET",
    )

    # Initialise database
    init_db()

    # Clear stale caches on startup so a Docker redeploy always gets fresh data.
    # The SQLite database is on a persistent volume and survives container restarts,
    # meaning old cached values (regime, sector stage2, breadth) from before a code
    # fix can persist for hours.  Wiping them here forces fresh computation on the
    # first scan after every deployment.
    try:
        from data.cache import delete as _cache_delete
        _stale_keys = (
            "regime:latest", "sector:performance",
            "breadth:sp500_above_200sma", "vix:latest",
            "ohlcv:SPY:2y", "ohlcv:QQQ:2y",
        )
        for _k in _stale_keys:
            _cache_delete(_k)
        logger.info("Startup: cleared %d stale cache keys", len(_stale_keys))
    except Exception as _exc:
        logger.warning("Startup cache clear failed (non-fatal): %s", _exc)

    # Clear stale fundamentals cache entries so the new yfinance-primary path
    # can succeed immediately (old Finnhub-based "unknown" entries were stored
    # with a 7-day TTL and would otherwise block yfinance for a full week).
    try:
        from database.db import get_connection as _gc
        _conn = _gc()
        _n = _conn.execute(
            "DELETE FROM cache WHERE key LIKE 'fundamentals:%'"
        ).rowcount
        _conn.commit()
        _conn.close()
        if _n:
            logger.info("Startup: cleared %d stale fundamentals cache entries "
                        "(yfinance will repopulate on first scan)", _n)
    except Exception as _exc:
        logger.warning("Fundamentals cache clear failed (non-fatal): %s", _exc)

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

    # Keep alive — write heartbeat every 60s; also services dashboard-triggered scans.
    # The dashboard writes 'scan_trigger=requested' to system_status; we pick it up
    # here and run a full scan without waiting for the next scheduled slot.
    try:
        from database.db import check_and_clear_scan_trigger, clear_scan_trigger
        while True:
            time.sleep(60)
            _write_heartbeat()
            if check_and_clear_scan_trigger():
                logger.info("Dashboard scan trigger detected — running on-demand full scan")
                run_full_scan()
                clear_scan_trigger()
                logger.info("On-demand scan complete")
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown signal received — stopping scheduler")
        scheduler.shutdown(wait=False)
        logger.info("SEPA system stopped")


if __name__ == "__main__":
    main()
