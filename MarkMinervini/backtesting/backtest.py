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
        price_data = fetch_ohlcv_batch(sp500, period="max")

        spy_df = fetch_ohlcv("SPY", period="max")
        if spy_df is None:
            raise ValueError("Could not fetch SPY data")

        # Filter to backtest period
        start = pd.Timestamp(settings.BACKTEST_START)
        end = pd.Timestamp(settings.BACKTEST_END)

        spy_period = spy_df.loc[start:end]

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
        # Normalise to starting capital
        scale = settings.ACCOUNT_EQUITY_GBP / float(combined_equity.iloc[0])
        combined_equity = combined_equity * scale

        # SPY benchmark scaled to same starting capital
        spy_benchmark = spy_df["Close"].loc[combined_equity.index]
        if len(spy_benchmark) > 0:
            spy_scaled = spy_benchmark * (settings.ACCOUNT_EQUITY_GBP / float(spy_benchmark.iloc[0]))
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
                record["spy"] = round(float(spy_scaled[date_idx]), 2)
            equity_records.append(record)

        # --- Save to database ---
        _save_results(metrics, equity_records)

        return {"metrics": metrics, "equity_curve": equity_records}

    except Exception as exc:
        logger.error("Backtest failed: %s", exc, exc_info=True)
        return {"error": str(exc)}


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
    """
    from screening.rs_calculator import compute_rs_ratings
    from screening.trend_template import check_trend_template
    from patterns.vcp_detector import detect_vcp

    # Build period-filtered price data
    period_data = {}
    for ticker, df in price_data.items():
        period_df = df.loc[:end]  # data available up to end of test window
        if len(period_df) >= 252:
            period_data[ticker] = period_df

    if not period_data:
        return None, []

    # Track portfolio
    equity = settings.ACCOUNT_EQUITY_GBP
    open_positions: dict[str, dict] = {}  # ticker → {entry, stop, shares, entry_date}
    equity_series = {}
    trade_returns = []

    # Step through each trading day in the test window
    spy_period = spy_df.loc[start:end]
    for current_date in spy_period.index:
        # Apply slippage to entries/exits (0.2%)
        date_str = str(current_date.date())

        # Check stops and exits for open positions
        for ticker in list(open_positions.keys()):
            pos = open_positions[ticker]
            if ticker not in period_data:
                continue
            df = period_data[ticker]
            if current_date not in df.index:
                continue
            today_low = float(df.loc[current_date, "Low"])
            today_close = float(df.loc[current_date, "Close"])
            stop = pos["stop"]

            # Stop hit: exit at stop with slippage
            if today_low <= stop:
                exit_price = stop * (1 - SLIPPAGE)
                pnl_pct = (exit_price - pos["entry"]) / pos["entry"] * 100
                equity += pos["shares"] * (exit_price - pos["entry"])
                trade_returns.append(pnl_pct)
                del open_positions[ticker]
                continue

            # Time-based exit: max 6 months in position
            days_held = (current_date - pos["entry_date"]).days
            if days_held > 126:
                exit_price = today_close * (1 - SLIPPAGE)
                pnl_pct = (exit_price - pos["entry"]) / pos["entry"] * 100
                equity += pos["shares"] * (exit_price - pos["entry"])
                trade_returns.append(pnl_pct)
                del open_positions[ticker]

        # Max positions gate
        if len(open_positions) >= settings.BACKTEST_MAX_POSITIONS:
            equity_series[current_date] = equity
            continue

        # Compute RS on data up to current_date (anti-look-ahead)
        rs_map = {}
        for ticker, df in period_data.items():
            available = df.loc[:current_date]
            if len(available) >= 252:
                pr_now = float(available["Close"].iloc[-1])
                pr_252 = float(available["Close"].iloc[-252])
                if pr_252 > 0:
                    rs_map[ticker] = (pr_now / pr_252 - 1)

        if rs_map:
            sorted_rs = sorted(rs_map.values())
            rs_pct = {t: (sorted_rs.index(v) / len(sorted_rs) * 100)
                      for t, v in rs_map.items()}
        else:
            rs_pct = {}

        # Screen each ticker on data up to current_date
        for ticker, df in period_data.items():
            if ticker in open_positions:
                continue
            available = df.loc[:current_date]
            if len(available) < 252:
                continue

            rs = rs_pct.get(ticker, 0.0)
            tt = check_trend_template(ticker, available, rs)
            if not tt["passes"]:
                continue

            # VCP on available data (anti-look-ahead)
            vcp = detect_vcp(ticker, available, trend_template_passes=True)
            if not vcp["alert"]:
                continue

            # Entry at NEXT day's open (T+1) with slippage — anti-look-ahead
            next_dates = df.loc[current_date:].index
            if len(next_dates) < 2:
                continue
            next_date = next_dates[1]
            entry_price = float(df.loc[next_date, "Open"]) * (1 + SLIPPAGE)

            stop_price = vcp["stop_price"]
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                continue

            risk_dollars = equity * settings.RISK_PER_TRADE_PCT
            import math
            shares = math.floor(risk_dollars / risk_per_share)
            if shares <= 0:
                continue
            position_value = shares * entry_price
            if position_value > equity * settings.MAX_POSITION_PCT:
                shares = math.floor(equity * settings.MAX_POSITION_PCT / entry_price)

            if shares > 0 and len(open_positions) < settings.BACKTEST_MAX_POSITIONS:
                open_positions[ticker] = {
                    "entry": entry_price,
                    "stop": stop_price,
                    "shares": shares,
                    "entry_date": next_date,
                }
                equity -= shares * entry_price  # cash out
                break  # one entry per day to avoid look-ahead cascade

        equity_series[current_date] = equity + sum(
            pos["shares"] * float(period_data.get(t, pd.DataFrame()).get("Close", pd.Series()).get(current_date, 0) or 0)
            for t, pos in open_positions.items()
        )

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
