"""
Rich, structured Telegram alert message builder (Section 11).
All formatting logic is here — telegram_bot.py only handles sending.
"""

import logging
from datetime import datetime, timezone

from config import settings

logger = logging.getLogger(__name__)


def format_breakout_alert(
    ticker: str,
    company_name: str,
    sector: str,
    sector_stage2: bool,
    vcp: dict,
    trend: dict,
    fundamentals: dict,
    position: dict,
    regime: dict,
    ai_news: dict,
    ai_earnings: dict,
    rs_rating: float,
    rs_line_new_high: bool,
    earnings_warning: str = "",
    vps_ip: str = "YOUR_VPS_IP",
) -> str:
    """
    Format the full breakout alert message for Telegram.
    Matches the template in Section 11 of the master prompt.
    """
    grade = vcp.get("grade", "HIGH QUALITY VCP")
    score = vcp.get("vcp_score", 0)
    entry = vcp.get("entry_price", 0.0)
    stop = vcp.get("stop_price", 0.0)
    stop_pct = vcp.get("stop_pct", 0.0)
    t1 = vcp.get("target_1", 0.0)
    t2 = vcp.get("target_2", 0.0)

    # Compute target percentage gains
    t1_pct = (t1 / entry - 1) * 100 if entry and t1 else 0
    t2_pct = (t2 / entry - 1) * 100 if entry and t2 else 0

    # EPS / revenue from fundamentals
    eps_growth = fundamentals.get("eps_growth_yoy")
    rev_growth = fundamentals.get("rev_growth_yoy")
    gm_now = fundamentals.get("gross_margin_current")
    gm_prior = fundamentals.get("gross_margin_prior")

    eps_str = f"+{eps_growth:.0f}%" if eps_growth else "N/A"
    rev_str = f"+{rev_growth:.0f}%" if rev_growth else "N/A"
    margin_str = "Expanding ✅" if (gm_now and gm_prior and gm_now >= gm_prior) else "Flat/Contracting"

    # AI sentiment
    ai_sentiment = ai_news.get("sentiment", "NEUTRAL") if ai_news else "N/A"
    ai_catalyst = ai_news.get("catalyst_type", "") if ai_news else ""
    ai_genuine = ai_earnings.get("genuine_growth", True) if ai_earnings else True
    ai_online = not ai_news.get("offline", False) if ai_news else False

    ai_line = (
        f"✅ Genuine growth ✅" if ai_genuine else "⚠️ Quality uncertain"
    ) + (
        f" | Catalyst: {ai_catalyst} ✅" if ai_catalyst else ""
    ) + (
        f" | {ai_sentiment}" if ai_online else " | ℹ️ AI offline"
    )

    # Contractions
    contractions = vcp.get("contractions", [])
    contraction_depths = "→".join(f"{c['depth_pct']:.0f}%" for c in contractions)

    # RS line new high flag
    rs_line_str = "RS Line: NEW HIGH ✅" if rs_line_new_high else f"RS Rating: {rs_rating:.0f}"

    # SMA200 info
    details = trend.get("details", {})
    sma200_months = "5mo ✅" if details.get("c4_sma200_rising_strong") else "1mo"
    pct_from_high = details.get("pct_from_52wk_high", 0)

    # Trend template summary
    tt_score = trend.get("score", 0)
    tt_str = f"✅ All {tt_score} Pass" if tt_score == 8 else f"⚠️ {tt_score}/8"

    # Position sizing — separate GBP and USD fields
    shares = position.get("shares", 0)
    pos_val_gbp = position.get("position_value_gbp", position.get("position_value", 0.0))
    pos_val_usd = position.get("position_value_usd", 0.0)
    pos_pct = position.get("position_pct", 0.0)
    risk_gbp = position.get("risk_gbp", 0.0)       # correct field name (GBP)
    risk_usd = position.get("risk_usd", 0.0)       # USD equivalent
    fx_rate = position.get("gbpusd_rate", 1.27)
    fx_warning_flag = position.get("fx_warning", False)

    # Sector
    sector_str = f"{sector} {'✅ Stage 2' if sector_stage2 else '⚠️ Not Stage 2'}"

    # Regime
    regime_label = regime.get("regime", "NEUTRAL")
    regime_emoji = "🟢" if regime_label == "BULL" else ("🔴" if regime_label == "BEAR" else "🟡")
    vix = regime.get("vix_level", 0.0)
    breadth = regime.get("breadth_pct")
    breadth_str = f"{breadth:.0f}%" if breadth else "N/A"

    msg = (
        f"🚀 BREAKOUT: ${ticker} — {company_name}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"VCP: {grade.split()[0]} | Score: {score}/100\n"
        f"Sector: {sector_str}\n"
        f"\n"
        f"📊 TREND TEMPLATE: {tt_str}\n"
        f"  {rs_line_str} | 52W Hi: {pct_from_high:+.0f}% | SMA200: Rising {sma200_months}\n"
        f"\n"
        f"💰 FUNDAMENTALS:\n"
        f"  EPS (Q YoY): {eps_str}\n"
        f"  Revenue:     {rev_str}\n"
        f"  Margins:     {margin_str}\n"
        f"\n"
        f"🤖 AI: {ai_line}\n"
        f"\n"
        f"📈 VCP: {len(contractions)} contractions [{contraction_depths}]\n"
        f"  Base: {vcp.get('base_days', 0) // 5:.1f} weeks | "
        f"Vol dry-up ✅ | {rs_line_str}\n"
        f"\n"
        f"📍 SETUP:\n"
        f"  Entry:    ${entry:.2f}\n"
        f"  Stop:     ${stop:.2f} (−{stop_pct:.1f}%)\n"
        f"  Target 1: ${t1:.2f} (+{t1_pct:.1f}%) [2R]\n"
        f"  Target 2: ${t2:.2f} (+{t2_pct:.1f}%) [3R]\n"
        f"\n"
        f"💼 SIZE ({settings.RISK_PER_TRADE_PCT*100:.1f}% risk, "
        f"£{settings.ACCOUNT_EQUITY_GBP:,.0f}):\n"
        f"  Shares: {shares} | £{pos_val_gbp:,.0f} / ${pos_val_usd:,.0f} ({pos_pct:.1f}%)\n"
        f"  Max loss: £{risk_gbp:,.0f} / ${risk_usd:,.0f}\n"
    )

    if fx_warning_flag:
        msg += "  ⚠️ FX fallback rate — verify size manually\n"

    if earnings_warning:
        msg += f"\n{earnings_warning}\n"

    msg += (
        f"\n"
        f"🌍 REGIME: {regime_emoji} {regime_label} | VIX: {vix:.1f} | Breadth: {breadth_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📱 Dashboard: http://{vps_ip}:{settings.DASHBOARD_PORT}"
    )

    return msg


def format_morning_briefing(
    regime: dict,
    watchlist: list[dict],
    near_pivot: list[str],
    earnings_blocked: list[str],
    sector_leaders: list[dict],
    weak_sectors: list[str],
    economic_events: list[str],
    vps_ip: str = "YOUR_VPS_IP",
) -> str:
    """Format the daily morning briefing message (1:00 PM BST)."""
    from datetime import date
    today = date.today().strftime("%A %-d %B %Y")

    regime_label = regime.get("regime", "NEUTRAL")
    regime_emoji = "🟢" if regime_label == "BULL" else ("🔴" if regime_label == "BEAR" else "🟡")
    aggression = regime.get("aggression_factor", 1.0)
    breadth = regime.get("breadth_pct")
    breadth_str = f"{breadth:.0f}%" if breadth else "N/A"
    vix = regime.get("vix_level", 0.0)
    dist = regime.get("distribution_days", 0)

    sizing_str = f"{aggression*100:.0f}% sizing"
    leaders_str = ", ".join(s.get("sector", "")[:12] for s in sector_leaders[:3])
    weak_str = ", ".join(weak_sectors[:2]) if weak_sectors else "None"
    near_pivot_str = ", ".join(f"${t}" for t in near_pivot[:3]) if near_pivot else "None"
    blocked_str = ", ".join(f"${t}" for t in earnings_blocked[:3]) if earnings_blocked else "None"
    events_str = (
        ", ".join(
            (e.get("event", str(e)) if isinstance(e, dict) else str(e))
            for e in economic_events[:2]
        ) if economic_events else "None today ✅"
    )

    return (
        f"📋 MORNING BRIEFING — {today}\n"
        f"US market opens in ~1h\n"
        f"\n"
        f"🌍 Regime: {regime_emoji} {regime_label} ({sizing_str})\n"
        f"Breadth: {breadth_str} | VIX: {vix:.1f} | Dist Days: {dist}/{settings.DISTRIBUTION_DAYS_DANGER}\n"
        f"\n"
        f"Watchlist: {len(watchlist)} stocks\n"
        f"Near pivot: {near_pivot_str}\n"
        f"Earnings block today: {blocked_str}\n"
        f"\n"
        f"Events: {events_str}\n"
        f"Leaders: {leaders_str}\n"
        f"Weak: {weak_str}\n"
        f"\n"
        f"Dashboard: http://{vps_ip}:{settings.DASHBOARD_PORT}"
    )


def format_bear_market_alert() -> str:
    return (
        "⚠️ BEAR MARKET MODE\n"
        "SPY below 200-day SMA.\n"
        "No new long positions.\n"
        "Monitor existing stops carefully.\n"
        "Cash is a position."
    )


def format_earnings_assessment(assessments: list[dict]) -> str:
    """Format post-earnings watchlist assessment for morning update."""
    if not assessments:
        return "📋 No earnings from watchlist stocks in last 2 days."
    lines = ["📋 POST-EARNINGS ASSESSMENT:"]
    for a in assessments:
        lines.append(f"  {a['note']} — ${a['ticker']}")
    return "\n".join(lines)
