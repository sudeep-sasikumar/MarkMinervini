"""
Streamlit web dashboard — 6 pages (Section 2).
Run: streamlit run dashboard.py --server.port 8501
Access: http://localhost:8501 or http://YOUR_VPS_IP:8501

Pages:
  1. 🏠 Live Dashboard     — metric cards, signals table, market intel
  2. 📋 Watchlist          — full watchlist with filters
  3. 📈 Signal History     — all signals with charts
  4. 💼 Trade Journal      — manual trade log + P&L tracker
  5. ⚙️ System Status      — API health, scheduler, scan funnel, logs
  6. 📊 Backtest Results   — run backtest, view equity curve + metrics
"""

import os
import subprocess
import sys
import time
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="SEPA — Minervini Signal System",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Path setup so imports work regardless of working directory
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))

from config import settings
from database.db import get_connection, init_db

init_db()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_regime():
    try:
        from market_intelligence.regime_detector import detect_regime
        return detect_regime()
    except Exception:
        return {"regime": "UNKNOWN", "vix_level": 0, "breadth_pct": None,
                "distribution_days": 0, "aggression_factor": 1.0,
                "signals_allowed": True, "regime_summary": "Data unavailable"}


def _get_watchlist_df() -> pd.DataFrame:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM watchlist ORDER BY vcp_score DESC").fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _get_signals_df(limit: int = 200) -> pd.DataFrame:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM signals ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _get_positions_df() -> pd.DataFrame:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY entry_date DESC"
    ).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _get_system_logs(n: int = 50) -> pd.DataFrame:
    conn = get_connection()
    rows = conn.execute(
        "SELECT timestamp, level, module, message FROM system_log "
        "ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    conn.close()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in rows])


def _regime_colour(regime: str) -> str:
    return {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "🟡"}.get(regime, "⚪")


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------
page = st.sidebar.radio(
    "Navigation",
    ["🏠 Live Dashboard", "📋 Watchlist", "📈 Signal History",
     "💼 Trade Journal", "⚙️ System Status", "📊 Backtest Results"],
)

st.sidebar.markdown("---")
st.sidebar.caption(f"SEPA v3.0 | {date.today().isoformat()}")
st.sidebar.caption(f"Dashboard port: {settings.DASHBOARD_PORT}")


# ===========================================================================
# PAGE 1 — Live Dashboard
# ===========================================================================
if page == "🏠 Live Dashboard":
    st.title("📈 Minervini SEPA — Live Dashboard")

    # Auto-refresh every 60 seconds
    refresh_placeholder = st.empty()
    with refresh_placeholder.container():
        st.caption(f"Auto-refreshing every {settings.DASHBOARD_REFRESH_SECONDS}s | "
                   f"Last update: {datetime.now().strftime('%H:%M:%S')}")

    # --- Top metric cards ---
    regime_data = _get_regime()
    regime_label = regime_data.get("regime", "UNKNOWN")
    vix = regime_data.get("vix_level", 0.0) or 0.0
    breadth = regime_data.get("breadth_pct")

    signals_df = _get_signals_df(50)
    today_signals = len(signals_df[signals_df["date"] == date.today().isoformat()]) if not signals_df.empty else 0
    watchlist_df = _get_watchlist_df()
    watchlist_size = len(watchlist_df)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Market Regime", f"{_regime_colour(regime_label)} {regime_label}")
    col2.metric("Signals Today", today_signals)
    col3.metric("Watchlist", watchlist_size)
    col4.metric("VIX", f"{vix:.1f}" if vix else "N/A")

    st.divider()

    left, right = st.columns([3, 2])

    # --- Left: Active Buy Signals ---
    with left:
        st.subheader("🚀 Active Buy Signals (Today)")
        today_str = date.today().isoformat()

        if not signals_df.empty:
            today_df = signals_df[signals_df["date"] == today_str].copy()
        else:
            today_df = pd.DataFrame()

        if today_df.empty:
            st.info("No signals generated today yet.")
        else:
            display_cols = ["ticker", "vcp_score", "rs_rating", "entry_price",
                            "stop_price", "stop_pct", "eps_growth", "sector",
                            "ai_sentiment", "created_at"]
            display_df = today_df[[c for c in display_cols if c in today_df.columns]].copy()
            display_df.columns = [c.replace("_", " ").title() for c in display_df.columns]

            def _row_colour(score):
                if score >= 90:
                    return "background-color: #d4edda"
                if score >= 80:
                    return "background-color: #cce5ff"
                return ""

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
            )

    # --- Right: Market Intelligence ---
    with right:
        st.subheader("🌍 Market Intelligence")

        try:
            from market_intelligence.sector_analyzer import get_leading_sectors
            leaders = get_leading_sectors(3)
            st.markdown("**Top Sector Leaders:**")
            for s in leaders:
                stage = "✅" if s.get("stage2") else "❌"
                st.markdown(f"- **{s['sector']}** ({s['etf']}): "
                            f"{s['1m_pct']:+.1f}% 1m | {s['3m_pct']:+.1f}% 3m {stage}")
        except Exception:
            st.info("Sector data loading...")

        st.markdown("---")

        dist_days = regime_data.get("distribution_days", 0)
        st.markdown(f"**Distribution Days:** {dist_days}/{settings.DISTRIBUTION_DAYS_DANGER}")
        st.progress(min(dist_days / settings.DISTRIBUTION_DAYS_DANGER, 1.0))

        if breadth is not None:
            st.markdown(f"**Market Breadth (% above 200-SMA):** {breadth:.1f}%")
            st.progress(breadth / 100)

        st.markdown(f"**Regime Summary:** {regime_data.get('regime_summary', '')}")
        st.markdown(f"**Aggression Factor:** {regime_data.get('aggression_factor', 1.0):.0%}")

    # Auto-refresh via rerun
    time.sleep(settings.DASHBOARD_REFRESH_SECONDS)
    st.rerun()


# ===========================================================================
# PAGE 2 — Watchlist
# ===========================================================================
elif page == "📋 Watchlist":
    st.title("📋 Watchlist")

    watchlist_df = _get_watchlist_df()

    if watchlist_df.empty:
        st.info("Watchlist is empty. Run a full scan to populate it.")
        st.stop()

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        sectors = ["All"] + sorted(watchlist_df["sector"].dropna().unique().tolist())
        sector_filter = st.selectbox("Sector", sectors)
    with col2:
        min_vcp = st.slider("Min VCP Score", 0, 100, 70)
    with col3:
        min_rs = st.slider("Min RS Rating", 0, 100, 70)

    filtered = watchlist_df.copy()
    if sector_filter != "All":
        filtered = filtered[filtered["sector"] == sector_filter]
    filtered = filtered[filtered["vcp_score"] >= min_vcp]
    filtered = filtered[filtered["rs_rating"] >= min_rs]

    st.caption(f"Showing {len(filtered)} of {len(watchlist_df)} watchlist stocks")

    display_cols = ["ticker", "company_name", "sector", "vcp_score", "pivot_price",
                    "rs_rating", "eps_growth", "rev_growth", "earnings_date",
                    "added_date", "ai_notes"]
    display_df = filtered[[c for c in display_cols if c in filtered.columns]].copy()
    display_df.columns = [c.replace("_", " ").title() for c in display_df.columns]

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    if st.button("🔄 Refresh Scan Now"):
        with st.spinner("Running scan..."):
            try:
                from main import run_full_scan
                signals = run_full_scan()
                st.success(f"Scan complete! {len(signals)} new signals.")
                st.rerun()
            except Exception as e:
                st.error(f"Scan failed: {e}")


# ===========================================================================
# PAGE 3 — Signal History
# ===========================================================================
elif page == "📈 Signal History":
    st.title("📈 Signal History")

    signals_df = _get_signals_df(500)

    if signals_df.empty:
        st.info("No signals in database yet.")
        st.stop()

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        tickers = ["All"] + sorted(signals_df["ticker"].unique().tolist())
        ticker_filter = st.selectbox("Ticker", tickers)
    with col2:
        if "date" in signals_df.columns:
            min_date = pd.to_datetime(signals_df["date"]).min().date()
            max_date = pd.to_datetime(signals_df["date"]).max().date()
        else:
            min_date = max_date = date.today()
        date_from = st.date_input("From", value=min_date)
    with col3:
        date_to = st.date_input("To", value=max_date)

    filtered = signals_df.copy()
    if ticker_filter != "All":
        filtered = filtered[filtered["ticker"] == ticker_filter]
    if "date" in filtered.columns:
        filtered = filtered[
            (pd.to_datetime(filtered["date"]).dt.date >= date_from) &
            (pd.to_datetime(filtered["date"]).dt.date <= date_to)
        ]

    st.caption(f"{len(filtered)} signals found")

    # Signal count chart
    if "date" in filtered.columns and len(filtered) > 0:
        weekly = (filtered.groupby(
            pd.to_datetime(filtered["date"]).dt.to_period("W").astype(str)
        ).size().reset_index(name="count"))
        fig = px.bar(weekly, x="date", y="count",
                     title="Signals per Week", color_discrete_sequence=["#1f77b4"])
        st.plotly_chart(fig, use_container_width=True)

    # VCP score distribution
    if "vcp_score" in filtered.columns and len(filtered) > 0:
        fig2 = px.histogram(filtered, x="vcp_score", nbins=20,
                            title="VCP Score Distribution",
                            color_discrete_sequence=["#2ca02c"])
        st.plotly_chart(fig2, use_container_width=True)

    # Table
    display_cols = ["date", "ticker", "signal_type", "vcp_score", "entry_price",
                    "stop_price", "stop_pct", "rs_rating", "eps_growth", "sector",
                    "ai_sentiment", "regime", "telegram_sent"]
    display_df = filtered[[c for c in display_cols if c in filtered.columns]]
    st.dataframe(display_df, use_container_width=True, hide_index=True)


# ===========================================================================
# PAGE 4 — Trade Journal
# ===========================================================================
elif page == "💼 Trade Journal":
    st.title("💼 Trade Journal")

    # Input form for new trades
    with st.expander("➕ Add New Trade"):
        with st.form("new_trade"):
            col1, col2, col3 = st.columns(3)
            with col1:
                t_ticker = st.text_input("Ticker").upper()
                t_entry_date = st.date_input("Entry Date", value=date.today())
            with col2:
                t_entry_price = st.number_input("Entry Price ($)", min_value=0.01, step=0.01)
                t_shares = st.number_input("Shares", min_value=1, step=1)
            with col3:
                t_stop = st.number_input("Stop Price ($)", min_value=0.01, step=0.01)
                t_notes = st.text_area("Notes", height=70)

            submitted = st.form_submit_button("Add Trade")
            if submitted and t_ticker and t_entry_price > 0 and t_shares > 0:
                from database.db import db_session
                with db_session() as conn:
                    conn.execute(
                        "INSERT INTO positions (ticker, entry_date, entry_price, shares, "
                        "stop_price, account_equity_at_entry, notes) VALUES (?,?,?,?,?,?,?)",
                        (t_ticker, t_entry_date.isoformat(), t_entry_price,
                         int(t_shares), t_stop, settings.ACCOUNT_EQUITY_GBP, t_notes),
                    )
                st.success(f"Trade added: {t_ticker} {int(t_shares)}sh @ ${t_entry_price:.2f}")
                st.rerun()

    # Open positions with live P&L
    st.subheader("Open Positions")
    positions_df = _get_positions_df()

    if positions_df.empty:
        st.info("No open positions. Add trades above.")
    else:
        from data.fetcher import fetch_latest_price

        rows = []
        for _, pos in positions_df.iterrows():
            current = fetch_latest_price(pos["ticker"]) or pos["entry_price"]
            pnl_pct = (current / pos["entry_price"] - 1) * 100
            pnl_gbp = (current - pos["entry_price"]) * pos["shares"]
            r_multiple = pnl_pct / pos["stop_pct"] if pos.get("stop_pct") else 0
            rows.append({
                "Ticker": pos["ticker"],
                "Entry": f"${pos['entry_price']:.2f}",
                "Current": f"${current:.2f}",
                "P&L %": f"{pnl_pct:+.1f}%",
                "P&L £": f"£{pnl_gbp:+,.0f}",
                "Stop": f"${pos.get('stop_price', 0):.2f}",
                "Shares": int(pos["shares"]),
                "Notes": pos.get("notes", ""),
            })

        df_pnl = pd.DataFrame(rows)
        st.dataframe(df_pnl, use_container_width=True, hide_index=True)

        # Portfolio summary
        total_pnl = sum(
            (fetch_latest_price(pos["ticker"]) or pos["entry_price"]) * pos["shares"]
            - pos["entry_price"] * pos["shares"]
            for _, pos in positions_df.iterrows()
        )
        total_invested = (positions_df["entry_price"] * positions_df["shares"]).sum()
        st.metric("Total Open P&L", f"£{total_pnl:+,.0f}",
                  delta=f"{total_pnl/settings.ACCOUNT_EQUITY_GBP*100:+.1f}% of account")
        st.metric("Total Invested", f"£{total_invested:,.0f}",
                  delta=f"{total_invested/settings.ACCOUNT_EQUITY_GBP*100:.1f}% of account")


# ===========================================================================
# PAGE 5 — System Status
# ===========================================================================
elif page == "⚙️ System Status":
    st.title("⚙️ System Status")

    # API health
    st.subheader("API Health")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        try:
            import yfinance as yf
            yf.Ticker("AAPL").history(period="1d")
            st.metric("yfinance", "✅ OK")
        except Exception:
            st.metric("yfinance", "❌ Error")

    with col2:
        if settings.FINNHUB_API_KEY:
            try:
                import requests
                r = requests.get(
                    f"https://finnhub.io/api/v1/stock/profile2?symbol=AAPL&token={settings.FINNHUB_API_KEY}",
                    timeout=5
                )
                st.metric("Finnhub", "✅ OK" if r.status_code == 200 else f"❌ {r.status_code}")
            except Exception:
                st.metric("Finnhub", "❌ Error")
        else:
            st.metric("Finnhub", "⚠️ No key")

    with col3:
        from alerts.telegram_bot import is_telegram_available
        tg = is_telegram_available()
        st.metric("Telegram", "✅ OK" if tg else "❌ Not configured")

    with col4:
        from market_intelligence.ai_analyst import is_ai_online
        ai = is_ai_online()
        st.metric("Ollama AI", "✅ OK" if ai else "❌ Offline")

    st.divider()

    # Universe and scan funnel
    st.subheader("Latest Scan Funnel")
    conn = get_connection()
    log_row = conn.execute(
        "SELECT message FROM system_log WHERE module='scanner' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if log_row:
        st.code(log_row["message"])
    else:
        st.info("No scan funnel data yet — run a scan first.")

    # Scheduler next jobs (simplified — shows configured times)
    st.subheader("Scheduled Jobs (BST)")
    jobs_info = {
        "data_refresh": "08:00 — Data refresh + RS + Breadth",
        "market_intelligence": "09:00 — Regime + Sectors + Macro",
        "full_scan_1": "11:00 — Full scan #1",
        "morning_briefing": "13:00 — Morning briefing (Telegram)",
        "intraday": "13:30–21:00 every 15min — Intraday breakout check",
        "full_scan_2": "15:30 — Full scan #2",
        "full_scan_3": "19:00 — Full scan #3",
        "post_market": "21:15 — Post-market wrap",
        "weekly": "Sunday 10:00 — Weekly AI + backtest",
    }
    for job_id, desc in jobs_info.items():
        st.text(f"  • {desc}")

    st.divider()

    # Log viewer
    st.subheader("Recent Logs (last 50 lines)")
    logs_df = _get_system_logs(50)
    if not logs_df.empty:
        st.dataframe(logs_df, use_container_width=True, hide_index=True, height=400)
    else:
        # Fallback: show file log tail
        try:
            with open(LOG_PATH if os.path.exists(LOG_PATH) else "logs/sepa.log") as f:
                lines = f.readlines()
            st.code("".join(lines[-50:]))
        except Exception:
            st.info("No logs available yet.")

    if st.button("🔄 Refresh"):
        st.rerun()


# ===========================================================================
# PAGE 6 — Backtest Results
# ===========================================================================
elif page == "📊 Backtest Results":
    st.title("📊 Backtest Results")

    st.info(
        "Backtesting uses vectorbt with walk-forward validation. "
        f"Period: {settings.BACKTEST_START} → {settings.BACKTEST_END}. "
        "Commission: £0. Slippage: 0.2%."
    )

    if st.button("▶️ Run Backtest (may take several minutes)"):
        with st.spinner("Running backtest..."):
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "backtesting.backtest"],
                    capture_output=True, text=True, timeout=600,
                )
                if result.returncode == 0:
                    st.success("Backtest complete!")
                else:
                    st.error(f"Backtest failed:\n{result.stderr[:500]}")
            except subprocess.TimeoutExpired:
                st.error("Backtest timed out (>10 min)")
            except Exception as e:
                st.error(f"Error: {e}")

    # Load stored results
    conn = get_connection()
    bt_rows = conn.execute(
        "SELECT * FROM backtest_results ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()

    if bt_rows:
        import json
        metrics = json.loads(bt_rows["metrics"])
        equity = json.loads(bt_rows["equity_curve"])

        st.subheader("Performance Metrics")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("CAGR", f"{metrics.get('cagr', 0):.1f}%")
        col2.metric("Sharpe", f"{metrics.get('sharpe', 0):.2f}")
        col3.metric("Max Drawdown", f"{metrics.get('max_drawdown', 0):.1f}%")
        col4.metric("Win Rate", f"{metrics.get('win_rate', 0):.1f}%")

        col5, col6, col7, col8 = st.columns(4)
        col5.metric("Sortino", f"{metrics.get('sortino', 0):.2f}")
        col6.metric("Expectancy", f"{metrics.get('expectancy', 0):.2f}R")
        col7.metric("Total Trades", metrics.get("total_trades", 0))
        col8.metric("Avg Win/Loss", f"{metrics.get('avg_win_loss', 0):.2f}")

        # Equity curve
        if equity:
            eq_df = pd.DataFrame(equity)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=eq_df.get("date", []), y=eq_df.get("portfolio", []),
                                     name="SEPA Strategy", line=dict(color="#2ca02c")))
            if "spy" in eq_df.columns:
                fig.add_trace(go.Scatter(x=eq_df["date"], y=eq_df["spy"],
                                         name="SPY Benchmark", line=dict(color="#1f77b4", dash="dash")))
            fig.update_layout(title="Equity Curve vs SPY", xaxis_title="Date",
                              yaxis_title="Portfolio Value (£)")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No backtest results yet. Click 'Run Backtest' above.")


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.sidebar.markdown("---")
st.sidebar.caption(
    "For educational and research purposes.\n"
    "All trading involves risk of capital loss."
)
