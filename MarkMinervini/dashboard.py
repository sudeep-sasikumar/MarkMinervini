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
                "signals_allowed": True, "regime_summary": "Data unavailable",
                "spy_close": None, "spy_sma200": None, "spy_above_sma200": None}


def _force_refresh_caches():
    """Delete all regime/market caches so the next fetch recomputes from live data."""
    try:
        from data.cache import delete as cache_delete
        for key in ("regime:latest", "sector:performance",
                    "breadth:sp500_above_200sma", "vix:latest",
                    "ohlcv:SPY:2y", "ohlcv:QQQ:2y"):
            cache_delete(key)
    except Exception:
        pass


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
if st.sidebar.button("🔄 Force Regime Refresh", help="Clears cached regime, sector, and breadth data and recomputes from live market data."):
    _force_refresh_caches()
    st.rerun()
st.sidebar.caption(f"SEPA v3.0 | {date.today().isoformat()}")
st.sidebar.caption(f"Dashboard port: {settings.DASHBOARD_PORT}")


# ===========================================================================
# PAGE 1 — Live Dashboard
# ===========================================================================
if page == "🏠 Live Dashboard":
    st.title("📈 Minervini SEPA — Live Dashboard")

    # Auto-refresh header
    st.caption(
        f"Auto-refreshing every {settings.DASHBOARD_REFRESH_SECONDS}s | "
        f"Last update: {datetime.now().strftime('%H:%M:%S')} | "
        f"Use **🔄 Force Regime Refresh** in sidebar to clear stale cache"
    )

    # ---------------------------------------------------------------------------
    # Data loading
    # ---------------------------------------------------------------------------
    regime_data  = _get_regime()
    regime_label = regime_data.get("regime", "UNKNOWN")
    aggression   = regime_data.get("aggression_factor", 1.0)
    vix          = float(regime_data.get("vix_level", 0.0) or 0.0)
    breadth      = regime_data.get("breadth_pct")
    dist_days    = regime_data.get("distribution_days", 0)
    spy_close    = regime_data.get("spy_close")
    spy_sma50    = regime_data.get("spy_sma50")
    spy_sma150   = regime_data.get("spy_sma150")
    spy_sma200   = regime_data.get("spy_sma200")
    spy_above    = regime_data.get("spy_above_sma200")
    spy_stack_ok = regime_data.get("spy_ma_stack_ok")
    spy_date     = regime_data.get("spy_last_date", "")
    bear_gate    = regime_data.get("bear_gate", False)
    ftd          = regime_data.get("ftd_confirmed", False)
    hi_impact    = regime_data.get("high_impact_event_imminent", False)

    signals_df   = _get_signals_df(50)
    today_str    = date.today().isoformat()
    today_count  = (
        len(signals_df[signals_df["date"] == today_str])
        if not signals_df.empty else 0
    )
    watchlist_df  = _get_watchlist_df()
    watchlist_size = len(watchlist_df)

    # ---------------------------------------------------------------------------
    # Top metric cards (5 columns)
    # ---------------------------------------------------------------------------
    mc1, mc2, mc3, mc4, mc5 = st.columns(5)

    _regime_icon = {"BULL": "🟢", "BEAR": "🔴", "NEUTRAL": "🟡"}.get(regime_label, "⚪")
    mc1.metric(
        "Market Regime",
        f"{_regime_icon} {regime_label}",
        f"{aggression:.0%} aggression",
    )

    _vix_delta = (
        "Calm ✅" if vix < 15 else
        "Normal ✅" if vix < 25 else
        "Caution ⚠️" if vix < 35 else
        "EXTREME 🔴"
    )
    mc2.metric("VIX", f"{vix:.1f}", _vix_delta)

    _dd_delta = (
        "🔴 DANGER — suppressed" if dist_days >= settings.DISTRIBUTION_DAYS_DANGER else
        "🟡 Caution — 50% sizing" if dist_days >= settings.DISTRIBUTION_DAYS_CAUTION else
        "🟢 Clear"
    )
    mc3.metric(
        "Distribution Days",
        f"{dist_days} / {settings.DISTRIBUTION_DAYS_DANGER}",
        _dd_delta,
    )

    _breadth_str = f"{breadth:.0f}%" if breadth is not None else "N/A"
    _breadth_delta = (
        "Healthy 🟢" if (breadth or 0) >= 60 else
        "Mixed 🟡" if (breadth or 0) >= 40 else
        "Weak 🟠" if (breadth or 0) >= 20 else
        "Bear 🔴"
    ) if breadth is not None else ""
    mc4.metric("Breadth (>200-SMA)", _breadth_str, _breadth_delta)

    mc5.metric("Signals Today", today_count, f"{watchlist_size} on watchlist")

    st.divider()

    # ---------------------------------------------------------------------------
    # Main layout: left (regime detail) | right (sectors + signals)
    # ---------------------------------------------------------------------------
    left_col, right_col = st.columns([3, 2])

    # ===================================================================
    # LEFT — Full regime diagnostic panel
    # ===================================================================
    with left_col:

        # --- Regime banner ---
        _summary = regime_data.get("regime_summary", "")
        if regime_label == "BULL":
            st.success(f"✅ **BULL MARKET** — Full signals active | {_summary}")
        elif regime_label == "BEAR":
            st.error(f"🔴 **BEAR MARKET MODE** — New buy signals suppressed | {_summary}")
        else:
            st.warning(f"🟡 **NEUTRAL** — Reduced position sizing | {_summary}")

        st.subheader("🔍 SPY / Index Health")

        # SPY vs SMA200
        spy_c1, spy_c2 = st.columns(2)
        with spy_c1:
            if spy_close and spy_sma200:
                pct_vs_200 = (spy_close / spy_sma200 - 1) * 100
                _spy_icon = "✅" if spy_above else "🔴"
                st.metric(
                    f"{_spy_icon} SPY vs 200-day SMA",
                    f"${spy_close:,.2f}",
                    f"{pct_vs_200:+.2f}% | SMA200 = ${spy_sma200:,.2f}",
                )
                if spy_date:
                    st.caption(f"Last data: {spy_date}")
            else:
                st.metric("SPY vs 200-day SMA", "Loading…")

        with spy_c2:
            if spy_sma50 and spy_sma150 and spy_sma200:
                _stack_icon = "✅" if spy_stack_ok else "⚠️"
                st.metric(
                    f"{_stack_icon} MA Stack  50 > 150 > 200",
                    "Aligned ✅" if spy_stack_ok else "Broken ⚠️",
                    f"50=${spy_sma50:,.0f}  150=${spy_sma150:,.0f}  200=${spy_sma200:,.0f}",
                )
            else:
                st.metric("MA Stack", "Loading…")

        # 52-week high proximity (fetch from cache if available)
        try:
            from data.fetcher import fetch_ohlcv as _fetch_spy
            _spy_df = _fetch_spy("SPY", "2y")
            if _spy_df is not None and len(_spy_df) >= 20:
                _52wk_hi = float(_spy_df["Close"].rolling(252).max().iloc[-1])
                _52wk_lo = float(_spy_df["Close"].rolling(252).min().iloc[-1])
                _spy_cur = float(_spy_df["Close"].iloc[-1])
                _pct_hi  = (_spy_cur / _52wk_hi - 1) * 100
                _pct_lo  = (_spy_cur / _52wk_lo - 1) * 100
                _qqq_df  = _fetch_spy("QQQ", "2y")
                _qqq_cur = float(_qqq_df["Close"].iloc[-1]) if _qqq_df is not None else None
                _qqq_sma200 = (
                    float(_qqq_df["Close"].rolling(200).mean().iloc[-1])
                    if _qqq_df is not None and len(_qqq_df) >= 200 else None
                )

                idx_c1, idx_c2, idx_c3 = st.columns(3)
                with idx_c1:
                    _hi_icon = "✅" if _pct_hi >= -15 else "⚠️"
                    st.metric(f"{_hi_icon} SPY vs 52-wk High", f"{_pct_hi:+.1f}%",
                              f"High ${_52wk_hi:,.2f}")
                with idx_c2:
                    st.metric("✅ SPY vs 52-wk Low", f"+{_pct_lo:.1f}%",
                              f"Low ${_52wk_lo:,.2f}")
                with idx_c3:
                    if _qqq_cur and _qqq_sma200:
                        _qqq_pct = (_qqq_cur / _qqq_sma200 - 1) * 100
                        _q_icon = "✅" if _qqq_cur > _qqq_sma200 else "🔴"
                        st.metric(f"{_q_icon} QQQ vs 200-SMA", f"${_qqq_cur:,.2f}",
                                  f"{_qqq_pct:+.1f}% | SMA200=${_qqq_sma200:,.0f}")
        except Exception:
            pass

        # --- Distribution Days ---
        st.subheader("📊 Distribution Days")
        _dist_pct  = min(dist_days / max(settings.DISTRIBUTION_DAYS_DANGER, 1), 1.0)
        _dist_icon = (
            "🔴" if dist_days >= settings.DISTRIBUTION_DAYS_DANGER else
            "🟡" if dist_days >= settings.DISTRIBUTION_DAYS_CAUTION else
            "🟢"
        )
        st.markdown(
            f"{_dist_icon} **{dist_days} / {settings.DISTRIBUTION_DAYS_DANGER}** "
            f"distribution days in last {settings.DISTRIBUTION_LOOKBACK} sessions "
            f"*(IBD definition: close ≥ 0.2% lower on higher volume)*"
        )
        st.progress(_dist_pct)
        if dist_days >= settings.DISTRIBUTION_DAYS_DANGER:
            _signals_allowed = regime_data.get("signals_allowed", True)
            if not _signals_allowed or not spy_above:
                # SPY below SMA200 (or data unavailable): full suppression
                st.error(
                    f"⛔ Danger threshold reached + SPY below SMA200 — "
                    f"all buy signals suppressed until count drops below "
                    f"{settings.DISTRIBUTION_DAYS_DANGER}"
                )
            else:
                # SPY above SMA200: signals still allowed, position size at 25%
                st.warning(
                    f"⚠️ Danger threshold reached but SPY above SMA200 — "
                    f"signals allowed at **quarter size (25%)** until count drops below "
                    f"{settings.DISTRIBUTION_DAYS_DANGER}"
                )
        elif dist_days >= settings.DISTRIBUTION_DAYS_CAUTION:
            st.warning(
                f"⚠️ Caution zone — position sizes reduced to 50% "
                f"(threshold: {settings.DISTRIBUTION_DAYS_CAUTION})"
            )
        else:
            st.success("✅ Distribution count within safe range")

        # --- VIX ---
        st.subheader("😨 VIX — Fear Gauge")
        _vix_pct  = min(vix / 50.0, 1.0)
        _vix_zone = (
            f"🟢 Calm (<{settings.VIX_LOW}) — ideal conditions for momentum stocks"
            if vix < settings.VIX_LOW else
            f"🟡 Normal ({settings.VIX_LOW}–{settings.VIX_CAUTION}) — standard conditions"
            if vix < settings.VIX_CAUTION else
            f"🟠 Caution ({settings.VIX_CAUTION}–{settings.VIX_DANGER}) — 50% position sizing"
            if vix < settings.VIX_DANGER else
            f"🔴 EXTREME (≥{settings.VIX_DANGER}) — all signals suppressed!"
        )
        st.markdown(f"**VIX: {vix:.2f}** — {_vix_zone}")
        st.progress(_vix_pct)

        # --- Market Breadth ---
        st.subheader("🌡️ Market Breadth  (% S&P 500 above 200-SMA)")
        if breadth is not None:
            _br_pct  = breadth / 100.0
            _br_zone = (
                f"🟢 Healthy bull (≥{settings.BREADTH_BULL}%) — broad participation"
                if breadth >= settings.BREADTH_BULL else
                f"🟡 Mixed ({settings.BREADTH_WEAK}–{settings.BREADTH_BULL}%) — selective market"
                if breadth >= settings.BREADTH_WEAK else
                f"🟠 Weak ({settings.BREADTH_BEAR}–{settings.BREADTH_WEAK}%) — 75% sizing"
                if breadth >= settings.BREADTH_BEAR else
                f"🔴 Bear territory (<{settings.BREADTH_BEAR}%) — signals suppressed!"
            )
            st.markdown(f"**{breadth:.1f}%** — {_br_zone}")
            st.progress(_br_pct)
        else:
            st.markdown("Breadth data computing... (runs at 08:00 BST)")

        # --- Follow-Through Day ---
        st.subheader("📅 Follow-Through Day (FTD)")
        _ftd_icon = "✅" if ftd else "⚠️"
        _ftd_msg  = (
            "Confirmed — rally attempt validated (gain ≥1.7% on higher volume, day 4+)"
            if ftd else
            "Not confirmed — no FTD since last correction → aggression capped at 50%"
        )
        st.markdown(f"{_ftd_icon} **{'Confirmed' if ftd else 'Unconfirmed'}** — {_ftd_msg}")

        # --- Macro event warning ---
        if hi_impact:
            st.subheader("📣 Macro Event Warning")
            st.warning(
                "⚠️ **High-impact event within 2 trading days** (CPI / Fed / NFP / PCE / GDP) "
                "— position sizes automatically reduced to 50%"
            )

        # --- SPY Price Chart ---
        st.subheader("📈 SPY — Price vs Moving Averages (last 90 sessions)")
        try:
            from data.fetcher import fetch_ohlcv as _fspy
            _df = _fspy("SPY", "2y")
            if _df is not None and len(_df) >= 50:
                _n   = min(90, len(_df))
                _win = _df.iloc[-_n:].copy()
                _win["SMA50"]  = _df["Close"].rolling(50).mean().iloc[-_n:].values
                _win["SMA150"] = (
                    _df["Close"].rolling(150).mean().iloc[-_n:].values
                    if len(_df) >= 150 else None
                )
                _win["SMA200"] = (
                    _df["Close"].rolling(200).mean().iloc[-_n:].values
                    if len(_df) >= 200 else None
                )

                _fig = go.Figure()
                _fig.add_trace(go.Candlestick(
                    x=_win.index, open=_win["Open"], high=_win["High"],
                    low=_win["Low"], close=_win["Close"],
                    name="SPY",
                    increasing_line_color="#26a69a",
                    decreasing_line_color="#ef5350",
                    increasing_fillcolor="#26a69a",
                    decreasing_fillcolor="#ef5350",
                ))
                _fig.add_trace(go.Scatter(
                    x=_win.index, y=_win["SMA50"],
                    name="SMA 50", line=dict(color="#2196F3", width=1.5),
                ))
                if _win["SMA150"] is not None:
                    _fig.add_trace(go.Scatter(
                        x=_win.index, y=_win["SMA150"],
                        name="SMA 150", line=dict(color="#FF9800", width=1.5),
                    ))
                if _win["SMA200"] is not None:
                    _fig.add_trace(go.Scatter(
                        x=_win.index, y=_win["SMA200"],
                        name="SMA 200", line=dict(color="#F44336", width=2.5),
                    ))
                _fig.update_layout(
                    height=380,
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=0, r=0, t=20, b=0),
                    legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0),
                    plot_bgcolor="#0e1117",
                    paper_bgcolor="#0e1117",
                    font=dict(color="#fafafa"),
                    xaxis=dict(gridcolor="#2a2a2a"),
                    yaxis=dict(gridcolor="#2a2a2a"),
                )
                st.plotly_chart(_fig, use_container_width=True)
        except Exception as _chart_exc:
            st.info(f"SPY chart unavailable: {_chart_exc}")

    # ===================================================================
    # RIGHT — Sector status + Active signals
    # ===================================================================
    with right_col:

        # --- Sector Stage 2 status ---
        st.subheader("🏭 Sector Stage 2 Status")
        st.caption(
            "Stage 2 = all 8 Minervini Trend Template criteria pass for the sector ETF. "
            "Only stocks in Stage 2 sectors trigger alerts."
        )

        try:
            from market_intelligence.sector_analyzer import fetch_sector_performance
            _sectors = fetch_sector_performance()

            _rows = []
            for _sec, _d in sorted(
                _sectors.items(), key=lambda x: x[1].get("3m_pct", 0), reverse=True
            ):
                _rows.append({
                    "Sector": _sec,
                    "ETF": _d.get("etf", ""),
                    "1m %": f"{_d.get('1m_pct', 0):+.1f}%",
                    "3m %": f"{_d.get('3m_pct', 0):+.1f}%",
                    "TT": f"{_d.get('tt_score', 0)}/8",
                    "Stage 2": "✅" if _d.get("stage2") else "❌",
                })

            if _rows:
                _sec_df = pd.DataFrame(_rows)
                st.dataframe(
                    _sec_df,
                    use_container_width=True,
                    hide_index=True,
                    height=350,
                    column_config={
                        "Stage 2": st.column_config.TextColumn("Stage 2", width="small"),
                        "TT":      st.column_config.TextColumn("TT Score", width="small"),
                    },
                )
                _n_stage2 = sum(1 for r in _rows if r["Stage 2"] == "✅")
                _total    = len(_rows)
                if _n_stage2 == 0:
                    st.error(f"⛔ No sectors in Stage 2 — all alerts gated (0/{_total})")
                elif _n_stage2 <= 3:
                    st.warning(f"⚠️ Only {_n_stage2}/{_total} sectors in Stage 2 — limited opportunities")
                else:
                    st.success(f"✅ {_n_stage2}/{_total} sectors in Stage 2 — broad opportunity")
        except Exception as _sec_exc:
            st.info(f"Sector data loading… ({_sec_exc})")

        st.divider()

        # --- Active Buy Signals ---
        st.subheader("🚀 Active Buy Signals (Today)")

        if not signals_df.empty:
            _today_df = signals_df[signals_df["date"] == today_str].copy()
        else:
            _today_df = pd.DataFrame()

        if _today_df.empty:
            if not regime_data.get("signals_allowed", True):
                st.warning("🔇 Signals suppressed by regime gate")
            else:
                st.info("No signals generated today yet.")
        else:
            _disp_cols = ["ticker", "vcp_score", "entry_price", "stop_pct",
                          "rs_rating", "sector", "ai_sentiment"]
            _disp      = _today_df[[c for c in _disp_cols if c in _today_df.columns]].copy()
            _disp.columns = [c.replace("_", " ").title() for c in _disp.columns]
            st.dataframe(_disp, use_container_width=True, hide_index=True)

        st.divider()

        # --- Watchlist near pivot ---
        st.subheader("🎯 Watchlist — Near Pivot")
        if not watchlist_df.empty and "pivot_price" in watchlist_df.columns:
            try:
                from data.fetcher import fetch_latest_price as _flp
                _near = []
                for _, _row in watchlist_df.head(30).iterrows():
                    _pivot = _row.get("pivot_price")
                    if not _pivot:
                        continue
                    _cur = _flp(_row["ticker"])
                    if _cur and _pivot:
                        _pct = (_cur / _pivot - 1) * 100
                        if abs(_pct) <= 5.0:
                            _near.append({
                                "Ticker": _row["ticker"],
                                "Score": int(_row.get("vcp_score", 0)),
                                "Current": f"${_cur:.2f}",
                                "Pivot": f"${_pivot:.2f}",
                                "Δ%": f"{_pct:+.1f}%",
                            })
                if _near:
                    st.dataframe(pd.DataFrame(_near), use_container_width=True, hide_index=True)
                else:
                    st.info("No watchlist stocks within 5% of pivot.")
            except Exception:
                st.caption("Near-pivot check skipped (live price unavailable)")
        elif watchlist_df.empty:
            st.info("Watchlist is empty — run a full scan.")

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

    # ------------------------------------------------------------------
    # Build / deployment info — change between deploys confirms new image
    # ------------------------------------------------------------------
    _git_commit = os.getenv("GIT_COMMIT", "dev")
    _build_date = os.getenv("BUILD_DATE", "local build")
    _short_hash = _git_commit[:8] if len(_git_commit) > 8 else _git_commit
    _is_dev = (_git_commit == "dev")

    if _is_dev:
        st.warning(
            "⚠️ **Running local/dev build** — no GIT_COMMIT env var set. "
            "If this is the VPS container, the image was NOT built by CI. "
            "Re-pull the image from ghcr.io after CI completes."
        )
    else:
        st.success(
            f"✅ **Deployed image:** commit `{_short_hash}` — built {_build_date}  "
            f"([view on GitHub](https://github.com/sudeep-sasikumar/MarkMinervini/commit/{_git_commit}))"
        )

    st.caption(
        f"Full commit hash: `{_git_commit}` | "
        "After a git push, wait for CI (~4–6 min), then **pull + restart** in Hostinger. "
        "This badge must change — if it shows 'dev' or the same old hash, the old image is still running."
    )
    st.divider()

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
        st.metric("Ollama AI", "✅ OK" if ai else "⚠️ Offline")

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

    # Log viewer — DB-backed (fast) with raw file fallback
    st.subheader("Recent Logs")
    n_lines = st.slider("Lines to show", min_value=50, max_value=500, value=100, step=50)
    logs_df = _get_system_logs(n_lines)
    if not logs_df.empty:
        st.dataframe(logs_df, use_container_width=True, hide_index=True, height=400)
    else:
        # Fallback: read the raw log file (always available in container)
        log_file = settings.LOG_PATH if hasattr(settings, "LOG_PATH") else "/app/logs/sepa.log"
        if not os.path.exists(log_file):
            log_file = "logs/sepa.log"
        try:
            with open(log_file) as f:
                lines = f.readlines()
            st.code("".join(lines[-n_lines:]), language=None)
        except FileNotFoundError:
            st.info("Log file not found — no logs written yet.")
        except Exception as exc:
            st.warning(f"Could not read log file: {exc}")

    # Raw log file download — useful for sending to Claude for debugging
    log_file = settings.LOG_PATH if hasattr(settings, "LOG_PATH") else "/app/logs/sepa.log"
    if not os.path.exists(log_file):
        log_file = "logs/sepa.log"
    if os.path.exists(log_file):
        with open(log_file, "rb") as f:
            log_bytes = f.read()
        st.download_button(
            label="⬇️ Download full log file (for debugging)",
            data=log_bytes,
            file_name="sepa.log",
            mime="text/plain",
        )

    if st.button("🔄 Refresh"):
        st.rerun()


# ===========================================================================
# PAGE 6 — Backtest Results
# ===========================================================================
elif page == "📊 Backtest Results":
    st.title("📊 Backtest Results")

    st.info(
        "Custom walk-forward backtest (pure pandas/numpy, no external backtesting library). "
        f"Period: {settings.BACKTEST_START} → {settings.BACKTEST_END}. "
        "Commission: £0. Slippage: 0.2%."
    )

    if st.button("▶️ Run Backtest (may take several minutes)"):
        with st.spinner("Running backtest... (may take 5–15 min depending on data availability)"):
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "backtesting.backtest"],
                    capture_output=True, text=True, timeout=600,
                    cwd=os.path.dirname(__file__),
                )
                stdout_out = (result.stdout or "").strip()
                stderr_out = (result.stderr or "").strip()
                # Last 6 000 chars of stderr keeps the ERROR line always visible
                stderr_tail = stderr_out[-6000:] if len(stderr_out) > 6000 else stderr_out

                if result.returncode == 0:
                    st.success("Backtest complete! Scroll down for results.")
                else:
                    st.error("Backtest failed — see diagnostic output below.")

                # Always show the [DIAG] window lines — they appear on both success and
                # failure. On success they reveal which filter eliminated every candidate.
                # On failure they show where the crash occurred.
                _diag_lines = [l for l in stdout_out.splitlines() if l.strip()]
                with st.expander(
                    "🔍 Backtest diagnostics (filter funnel per window)",
                    expanded=(result.returncode != 0 or not _diag_lines),
                ):
                    st.caption(
                        "These [DIAG] lines show how many stocks passed each filter per "
                        "walk-forward window. "
                        "days = scan days | tt_pass = trend template | vcp_wc = VCP watchlist | "
                        "breakout = price broke prior resistance | trades = executed trades."
                    )
                    if _diag_lines:
                        st.code("\n".join(_diag_lines), language=None)
                    elif stderr_tail:
                        st.subheader("stderr log (last ~6 000 chars)")
                        st.code(stderr_tail, language=None)
                    else:
                        st.warning("No output captured — process may have been killed by OOM.")
            except subprocess.TimeoutExpired:
                st.error("Backtest timed out (>10 min) — process killed.")
                st.info("Try reducing BACKTEST_END in settings.py or check VPS memory (docker stats).")
            except Exception as e:
                st.error(f"Error launching backtest subprocess: {e}")

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
