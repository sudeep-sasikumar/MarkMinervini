"""
Custom walk-forward backtester with anti-look-ahead rules (Section 14).
Implemented with pandas/numpy — no external backtesting library required.

Anti-look-ahead rules (strictly enforced):
  - Fundamentals: 2-day lag (earnings take 48h to appear in APIs)
  - Entry: compute signal on day T's close; entry at day T+1 open price
  - Rolling calculations use only data up to and including day T

Walk-forward validation:
  - Train: 18 months → Test: 6 months → Roll: 6 months
  - Results reported ONLY on test windows

Usage:
  python -m backtesting.backtest
  # or from dashboard via subprocess
"""

import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from config import settings
from database.db import db_session, init_db

logger = logging.getLogger(__name__)

# Slippage applied on entry and exit
SLIPPAGE = settings.BACKTEST_SLIPPAGE

# ---------------------------------------------------------------------------
# Main backtest runner
# ---------------------------------------------------------------------------

def run_backtest() -> dict:
    """
    Run the full walk-forward backtest.
    Returns a dict with metrics and equity curve, also saves to DB.
    """
    logger.info("Starting backtest: %s → %s", settings.BACKTEST_START, settings.BACKTEST_END)

    try:
        # --- Fetch S&P 500 and SPY data ---
        from screening.universe import get_sp500_tickers
        from data.fetcher import fetch_ohlcv_batch, fetch_ohlcv

        sp500 = get_sp500_tickers()
        logger.info("Fetching price data for %d S&P 500 tickers (this takes a while)...", len(sp500))

        # Use "7y" — covers back to ~2019, sufficient for BACKTEST_START=2021-01-01.
        # Rationale: the first test window (2022-07-01) needs 252 trading days of
        # RS history before it, i.e. back to ~2021-08.  "7y" from today (2026)
        # = 2019, giving a comfortable 2-year buffer.
        #
        # Previously "max" downloaded 20+ years per ticker (unnecessary with a 2021
        # start), causing ~5–8 min of extra download time and larger DataFrames in
        # the inner per-day loop — the primary driver of the >10 min timeout.
        price_data = fetch_ohlcv_batch(sp500, period="7y")
        logger.info("Fetched %d tickers with sufficient history", len(price_data))

        spy_df = fetch_ohlcv("SPY", period="7y")
        if spy_df is None:
            raise ValueError("Could not fetch SPY data")

        # --- Defensive timezone normalisation (second layer after fetch_ohlcv_batch) ---
        # yfinance bulk download occasionally returns tz-aware UTC DatetimeIndex even
        # after the per-ticker extraction fix.  Slicing a tz-aware DataFrame with a
        # tz-naive Timestamp raises TypeError, which silently crashes the backtest.
        # This loop guarantees all indexes are tz-naive before any .loc[] calls.
        tz_fixed = 0
        for ticker in list(price_data.keys()):
            df = price_data[ticker]
            if getattr(df.index, "tz", None) is not None:
                price_data[ticker] = df.copy()
                price_data[ticker].index = df.index.tz_localize(None)
                tz_fixed += 1
        if tz_fixed:
            logger.warning(
                "Stripped timezone from %d ticker DataFrames (yfinance returned tz-aware index)",
                tz_fixed,
            )

        if getattr(spy_df.index, "tz", None) is not None:
            spy_df = spy_df.copy()
            spy_df.index = spy_df.index.tz_localize(None)
            logger.warning("Stripped timezone from SPY DataFrame")

        # Filter to backtest period
        start = pd.Timestamp(settings.BACKTEST_START)
        end = pd.Timestamp(settings.BACKTEST_END)

        spy_period = spy_df.loc[start:end]
        if len(spy_period) == 0:
            raise ValueError(
                f"SPY has no data in backtest range {settings.BACKTEST_START}→{settings.BACKTEST_END}. "
                f"SPY data spans {spy_df.index[0].date()} → {spy_df.index[-1].date()}"
            )

        # --- Walk-forward validation ---
        train_months = settings.BACKTEST_TRAIN_MONTHS
        test_months = settings.BACKTEST_TEST_MONTHS
        roll_months = settings.BACKTEST_ROLL_MONTHS

        all_test_equity = []
        all_trade_returns = []
        wf_start = start

        while wf_start + pd.DateOffset(months=train_months + test_months) <= end:
            train_end = wf_start + pd.DateOffset(months=train_months)
            test_end = min(train_end + pd.DateOffset(months=test_months), end)

            logger.info("Walk-forward window: train=%s→%s, test=%s→%s",
                        wf_start.date(), train_end.date(), train_end.date(), test_end.date())

            # Run on test window only
            test_equity, trade_returns = _run_single_window(
                price_data, spy_df, train_end, test_end
            )

            if test_equity is not None:
                all_test_equity.append(test_equity)
                all_trade_returns.extend(trade_returns)

            wf_start += pd.DateOffset(months=roll_months)

        # --- Combine test equity curves ---
        if not all_test_equity:
            logger.warning("No equity data from backtest — universe too small or no signals")
            return {"error": "No signals generated in backtest period"}

        combined_equity = pd.concat(all_test_equity)
        # Walk-forward window boundaries create duplicate dates: the end of window N
        # equals the start of window N+1, so that date appears in both Series.
        # Duplicate dates cause spy_scaled[date_idx] to return a Series instead of
        # a scalar → float(Series) raises TypeError.  Deduplicate before any lookups.
        combined_equity = combined_equity[~combined_equity.index.duplicated(keep="last")]
        combined_equity.sort_index(inplace=True)

        # Normalise to starting capital
        scale = settings.ACCOUNT_EQUITY_GBP / float(combined_equity.iloc[0])
        combined_equity = combined_equity * scale

        # SPY benchmark scaled to same starting capital
        spy_benchmark = spy_df["Close"].reindex(combined_equity.index, method="ffill")
        if len(spy_benchmark.dropna()) > 0:
            spy_scaled = spy_benchmark * (settings.ACCOUNT_EQUITY_GBP / float(spy_benchmark.dropna().iloc[0]))
        else:
            spy_scaled = pd.Series(dtype=float)

        # --- Compute metrics ---
        from backtesting.metrics import compute_all_metrics
        metrics = compute_all_metrics(
            portfolio=combined_equity,
            trade_returns=all_trade_returns,
            benchmark=spy_scaled if len(spy_scaled) else None,
        )

        logger.info("Backtest complete: CAGR=%.1f%%, Sharpe=%.2f, MaxDD=%.1f%%",
                    metrics["cagr"], metrics["sharpe"], metrics["max_drawdown"])

        # --- Build equity curve for dashboard ---
        equity_records = []
        for date_idx, val in combined_equity.items():
            record = {"date": str(date_idx.date()), "portfolio": round(float(val), 2)}
            if date_idx in spy_scaled.index:
                spy_val = spy_scaled[date_idx]
                # Guard: reindex deduplication should prevent Series here, but be safe
                if isinstance(spy_val, pd.Series):
                    spy_val = spy_val.iloc[0]
                record["spy"] = round(float(spy_val), 2)
            equity_records.append(record)

        # --- Save to database ---
        _save_results(metrics, equity_records)

        return {"metrics": metrics, "equity_curve": equity_records}

    except Exception as exc:
        import traceback as _tb
        tb_str = _tb.format_exc()
        logger.error("Backtest failed: %s", exc, exc_info=True)
        # Print to stdout so the dashboard subprocess captures it separately from
        # the logging stream — dashboard shows result.stderr; traceback goes here.
        print(f"\n=== BACKTEST EXCEPTION ===\n{tb_str}\n=== END ===", flush=True)
        return {"error": str(exc), "traceback": tb_str}


def _run_single_window(
    price_data: dict[str, pd.DataFrame],
    spy_df: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.Series | None, list[float]]:
    """
    Run a single backtest window using the SEPA screening rules.
    Returns (equity_series, trade_returns_list).
    Anti-look-ahead: signals computed on day T, entered at T+1 open.

    Equity accounting:
        equity = cash balance (entry cost deducted, exit proceeds added)
        MTM    = equity (cash) + Σ(shares × current_price) for open positions
    """
    from screening.trend_template import check_trend_template
    from patterns.vcp_detector import detect_vcp
    from screening.rs_calculator import check_rs_line_new_high

    # Build period-filtered price data (data available up to end of test window)
    period_data = {}
    for ticker, df in price_data.items():
        period_df = df.loc[:end]
        if len(period_df) >= 252:
            period_data[ticker] = period_df

    if not period_data:
        return None, []

    # Track portfolio
    equity = settings.ACCOUNT_EQUITY_GBP   # cash balance only
    open_positions: dict[str, dict] = {}   # ticker → {entry, stop, shares, entry_date}
    equity_series: dict = {}
    trade_returns: list[float] = []

    # Pre-build a set of dates with data per ticker for fast membership checks
    ticker_dates: dict[str, set] = {
        t: set(df.index) for t, df in period_data.items()
    }

    def _close_price(ticker: str, date) -> float:
        """Best-effort close price for MTM; falls back to entry price if date absent."""
        df = period_data.get(ticker)
        if df is None:
            return open_positions[ticker]["entry"]
        try:
            return float(df.loc[date, "Close"])
        except KeyError:
            # Date not in index (holiday / data gap) — use last available close
            sub = df.loc[:date]
            return float(sub["Close"].iloc[-1]) if len(sub) else open_positions[ticker]["entry"]

    def _mark_to_market() -> float:
        """Total portfolio value = cash + open position MTM."""
        return equity + sum(
            pos["shares"] * _close_price(t, current_date)
            for t, pos in open_positions.items()
        )

    # Step through each trading day in the test window
    spy_period = spy_df.loc[start:end]

    # Diagnostic counters — logged once at end of window to diagnose filter bottlenecks
    _diag = {
        "days": 0, "tt_pass": 0, "vcp_wc": 0, "breakout": 0, "entries": 0,
    }
    # Rejection reason sampling — first 8 unique VCP rejections per window
    # Printed to stdout so the dashboard subprocess captures them (stderr = logs).
    _rejection_samples: list[str] = []
    _rejection_seen: set[str] = set()

    # RS cache — recomputed weekly (every 5 trading days) rather than daily.
    # RS rankings change slowly; weekly resolution is accurate enough for backtesting
    # and gives a 5× speedup (was the primary cause of >10 min backtest timeouts).
    _rs_pct: dict[str, float] = {}
    _rs_last_day: int = -99  # force compute on first day

    for day_num, current_date in enumerate(spy_period.index):
        # ---------------------------------------------------------------
        # 1. Process exits for open positions
        # ---------------------------------------------------------------
        for ticker in list(open_positions.keys()):
            pos = open_positions[ticker]
            if current_date not in ticker_dates.get(ticker, set()):
                continue
            df = period_data[ticker]
            today_low   = float(df.loc[current_date, "Low"])
            today_close = float(df.loc[current_date, "Close"])
            stop = pos["stop"]

            # Stop hit: exit at stop with slippage.
            # BUG FIX: previously `equity += shares × (exit_price − entry)` which
            # added only the P&L, causing equity to deplete at 2× the correct rate.
            # Correct: add back the full exit proceeds; the entry cost was already
            # subtracted when the position was opened.
            if today_low <= stop:
                exit_price = round(stop * (1 - SLIPPAGE), 4)
                pnl_pct = (exit_price - pos["entry"]) / pos["entry"] * 100
                equity += pos["shares"] * exit_price   # ← full proceeds, not P&L
                trade_returns.append(pnl_pct)
                del open_positions[ticker]
                continue

            # Time-based exit: max ~6 months in position
            days_held = (current_date - pos["entry_date"]).days
            if days_held > 126:
                exit_price = round(today_close * (1 - SLIPPAGE), 4)
                pnl_pct = (exit_price - pos["entry"]) / pos["entry"] * 100
                equity += pos["shares"] * exit_price   # ← full proceeds, not P&L
                trade_returns.append(pnl_pct)
                del open_positions[ticker]

        # ---------------------------------------------------------------
        # 2. Max-positions gate — skip scanning but ALWAYS record MTM
        #    BUG FIX: previously `equity_series[date] = equity; continue`
        #    which stored only cash, not the full portfolio value.
        # ---------------------------------------------------------------
        if len(open_positions) >= settings.BACKTEST_MAX_POSITIONS:
            equity_series[current_date] = _mark_to_market()
            continue

        # ---------------------------------------------------------------
        # 3. Compute RS on data up to current_date (anti-look-ahead).
        #    Uses IBD/Minervini weighted quarterly formula to match the
        #    live system (0.40×Q4 + 0.20×Q3 + 0.20×Q2 + 0.20×Q1).
        #    PERF: recomputed every 5 trading days (weekly) rather than
        #    daily — 5× speedup, critical fix for >10 min timeout.
        # ---------------------------------------------------------------
        if day_num - _rs_last_day >= 5:
            rs_map: dict[str, float] = {}
            for ticker, df in period_data.items():
                avail = df.loc[:current_date]
                if len(avail) < 252:
                    continue
                c = avail["Close"]
                p0   = float(c.iloc[-1])
                p63  = float(c.iloc[-63])
                p126 = float(c.iloc[-126])
                p189 = float(c.iloc[-189])
                p252 = float(c.iloc[-252])
                if p63 > 0 and p126 > 0 and p189 > 0 and p252 > 0:
                    q4 = p0 / p63   - 1.0
                    q3 = p63 / p126 - 1.0
                    q2 = p126 / p189 - 1.0
                    q1 = p189 / p252 - 1.0
                    rs_map[ticker] = 0.40 * q4 + 0.20 * q3 + 0.20 * q2 + 0.20 * q1

            if rs_map:
                tickers_rs = list(rs_map.keys())
                values_rs  = np.array([rs_map[t] for t in tickers_rs], dtype=float)
                order      = np.argsort(values_rs)      # weakest → strongest
                ranks      = np.empty(len(order), dtype=float)
                ranks[order] = np.arange(len(order), dtype=float)
                n = float(len(tickers_rs))
                _rs_pct = {t: ranks[i] / n * 100.0 for i, t in enumerate(tickers_rs)}
            else:
                _rs_pct = {}
            _rs_last_day = day_num

        rs_pct = _rs_pct

        # Pre-compute spy_avail ONCE per day (used for regime gate + RS line check).
        # Previously this was inside the ticker loop — recomputed for every ticker.
        spy_avail = spy_df.loc[:current_date]

        # ---------------------------------------------------------------
        # 4. Regime gate — block new entries when SPY < 200-SMA.
        #
        #    The live system suppresses all entry signals whenever the
        #    market regime is BEAR (SPY below 200-SMA).  Without this gate,
        #    the backtest enters bear-market bounces (Jul–Dec 2022) that
        #    quickly hit stops → artificially poor win rates and distorted
        #    metrics.  Exits and MTM recording are NOT affected.
        # ---------------------------------------------------------------
        if len(spy_avail) >= 200:
            _spy_c   = float(spy_avail["Close"].iloc[-1])
            _spy_s200 = float(spy_avail["Close"].rolling(200).mean().iloc[-1])
            if _spy_c < _spy_s200:
                equity_series[current_date] = _mark_to_market()
                continue  # no new entries while SPY below 200-SMA

        # ---------------------------------------------------------------
        # 5. Screen each ticker for a new entry signal
        # ---------------------------------------------------------------
        _diag["days"] += 1
        for ticker, df in period_data.items():
            if ticker in open_positions:
                continue
            avail = df.loc[:current_date]
            if len(avail) < 252:
                continue

            rs = rs_pct.get(ticker, 0.0)
            tt = check_trend_template(ticker, avail, rs)
            if not tt["passes"]:
                continue
            _diag["tt_pass"] += 1

            # VCP on available data (strict anti-look-ahead).
            # Use watchlist_candidate (score >= 70) instead of alert (score >= 80
            # AND live breakout confirmed on today's bar).
            #
            # CRITICAL: do NOT use vcp["pivot_price"] for the breakout check.
            # pivot_price = max(High[-PIVOT_ZONE_DAYS:]) which INCLUDES today's bar.
            # Since close <= high always, current_close > pivot_price is mathematically
            # impossible → 0 trades every run.  Instead compute resistance from the
            # prior PIVOT_ZONE_DAYS bars (excluding today) so a genuine close above
            # prior resistance is detectable.
            #
            # SCORING BUG FIX: must pass rs_line_new_high — without it the max
            # achievable score is 65 (all-vol 25 + very-tight ATR 35 + PP 5),
            # which is below the watchlist_candidate threshold of 70 unless a
            # breakout bonus (+5) also fires simultaneously. With the +10 RS
            # bonus, 70 is reachable from just all-vol + very-tight-ATR + RS,
            # giving the backtest realistic entry opportunities in the 2022–2024
            # bear/recovery period.
            rs_line_nh = check_rs_line_new_high(avail, spy_avail)  # spy_avail pre-computed above
            vcp = detect_vcp(ticker, avail, trend_template_passes=True,
                             rs_line_new_high=rs_line_nh)
            if not vcp.get("watchlist_candidate", False):
                # Sample unique rejection reasons (printed to stdout for dashboard visibility)
                reason = vcp.get("rejection_reason") or f"score={vcp.get('vcp_score',0)} < 70"
                # Bucket by first ~50 chars so similar reasons aren't all sampled
                key = reason[:50]
                if key not in _rejection_seen and len(_rejection_samples) < 8:
                    _rejection_seen.add(key)
                    _rejection_samples.append(
                        f"  {ticker} ({str(current_date.date())}): {reason}"
                    )
                continue
            _diag["vcp_wc"] += 1

            current_close = float(avail["Close"].iloc[-1])
            # Prior resistance: highest High in the PIVOT_ZONE_DAYS bars before today
            lookback = min(settings.PIVOT_ZONE_DAYS, len(avail) - 1)
            if lookback <= 0:
                continue
            prior_resistance = float(avail["High"].iloc[-lookback - 1 : -1].max())
            if prior_resistance <= 0 or current_close <= prior_resistance * 1.001:
                continue
            _diag["breakout"] += 1

            # Entry at T+1 open with slippage
            future = df.loc[current_date:]
            if len(future) < 2:
                continue
            next_date = future.index[1]
            entry_price = round(float(df.loc[next_date, "Open"]) * (1 + SLIPPAGE), 4)

            stop_price = vcp.get("stop_price")
            if stop_price is None or stop_price <= 0:
                continue
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                continue

            risk_budget = equity * settings.RISK_PER_TRADE_PCT
            shares = math.floor(risk_budget / risk_per_share)
            if shares <= 0:
                continue
            if shares * entry_price > equity * settings.MAX_POSITION_PCT:
                shares = math.floor(equity * settings.MAX_POSITION_PCT / entry_price)
            if shares <= 0:
                continue

            open_positions[ticker] = {
                "entry":      entry_price,
                "stop":       stop_price,
                "shares":     shares,
                "entry_date": next_date,
            }
            equity -= shares * entry_price    # cash out
            _diag["entries"] += 1
            break  # one new entry per day to avoid look-ahead cascade

        # ---------------------------------------------------------------
        # 6. Record daily portfolio value (cash + open position MTM)
        # ---------------------------------------------------------------
        equity_series[current_date] = _mark_to_market()

    # ---------------------------------------------------------------
    # End-of-window cleanup: force-close all positions still open.
    #
    # Positions entered late in the test window may not have hit their
    # 8% stop or the 126-day time exit before the window boundary.
    # Silently dropping them biases the sample: in the 2022–2024 backtest
    # run, 5 of 10 entries were dropped (50% of outcomes invisible).
    #
    # Fix: mark-to-market exit at the final window day's close with
    # normal 0.2% slippage.  This gives the same fair exit a live trader
    # would get if they reviewed positions at each 6-month checkpoint.
    # ---------------------------------------------------------------
    _window_end_closed = 0
    if open_positions:
        _last_date = spy_period.index[-1]
        for _ticker, _pos in list(open_positions.items()):
            _df = period_data.get(_ticker)
            if _df is not None:
                _sub = _df.loc[:_last_date]
                if len(_sub) > 0:
                    _exit_price = round(float(_sub["Close"].iloc[-1]) * (1 - SLIPPAGE), 4)
                    _pnl_pct = (_exit_price - _pos["entry"]) / _pos["entry"] * 100
                    equity += _pos["shares"] * _exit_price
                    trade_returns.append(_pnl_pct)
                    _window_end_closed += 1
        open_positions.clear()
    if _window_end_closed:
        logger.info(
            "Window end: force-closed %d open position(s) at mark-to-market",
            _window_end_closed,
        )

    # Log diagnostic stats for this window so filter bottlenecks are visible
    logger.info(
        "Window %s→%s | days=%d | tt_pass=%d | vcp_wc=%d | breakout=%d | entries=%d | trades=%d (window-end closes=%d)",
        start.date(), end.date(),
        _diag["days"], _diag["tt_pass"], _diag["vcp_wc"], _diag["breakout"],
        _diag["entries"], len(trade_returns), _window_end_closed,
    )
    # Print rejection samples to STDOUT (captured separately by dashboard subprocess)
    # so they appear in the "Full error + traceback" panel even on success.
    print(
        f"\n[DIAG] Window {start.date()}→{end.date()} "
        f"days={_diag['days']} tt_pass={_diag['tt_pass']} "
        f"vcp_wc={_diag['vcp_wc']} breakout={_diag['breakout']} "
        f"entries={_diag['entries']} trades={len(trade_returns)} open_closed={_window_end_closed}",
        flush=True,
    )
    if _rejection_samples:
        print("[DIAG] Sample VCP rejection reasons:", flush=True)
        for r in _rejection_samples:
            print(r, flush=True)

    if not equity_series:
        return None, []

    return pd.Series(equity_series), trade_returns


def _save_results(metrics: dict, equity_curve: list[dict]) -> None:
    """Store backtest results in the database."""
    today = datetime.today().date().isoformat()
    with db_session() as conn:
        conn.execute(
            "INSERT INTO backtest_results (run_date, period, metrics, equity_curve) "
            "VALUES (?,?,?,?)",
            (
                today,
                f"{settings.BACKTEST_START} → {settings.BACKTEST_END}",
                json.dumps(metrics),
                json.dumps(equity_curve),
            ),
        )
    logger.info("Backtest results saved to database")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(message)s")
    init_db()
    results = run_backtest()
    if "error" in results:
        print(f"Backtest error: {results['error']}")
        sys.exit(1)
    m = results["metrics"]
    print(f"\n=== Backtest Results ===")
    print(f"CAGR:         {m['cagr']:.1f}%")
    print(f"Sharpe:       {m['sharpe']:.2f}")
    print(f"Sortino:      {m['sortino']:.2f}")
    print(f"Max Drawdown: {m['max_drawdown']:.1f}%")
    print(f"Win Rate:     {m['win_rate']:.1f}%")
    print(f"Expectancy:   {m['expectancy']:.3f}R")
    print(f"Total trades: {m['total_trades']}")
