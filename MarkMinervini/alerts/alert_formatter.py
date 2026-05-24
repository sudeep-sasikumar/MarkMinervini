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
    vps_ip: str = "",
) -> str:
    """
    Format the full breakout alert message for Telegram.
    Matches the template in Section 11 of the master prompt.
    """
    vps_ip = vps_ip or settings.VPS_IP
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

    eps_str = f"+{eps_growth:.0f}%" if eps_growth is not None else "N/A"
    rev_str = f"+{rev_growth:.0f}%" if rev_growth is not None else "N/A"
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

    # Volume dry-up: read from VCP steps dict rather than hardcoding ✅
    steps = vcp.get("steps", {})
    vol_dry_up_days = steps.get("volume_dry_up_days", 0)
    vol_dry_up_str = "✅" if vol_dry_up_days >= 3 else f"⚠️ ({vol_dry_up_days}/5 days)"

    # Pocket pivot bonus
    pocket_pivot_str = " | Pocket Pivot ✅" if steps.get("pocket_pivot_bonus") else ""

    # RS line new high flag — shown in trend section only (not duplicated in VCP section)
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
        f"Vol dry-up {vol_dry_up_str}{pocket_pivot_str}\n"
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
        msg += "⚠️ FX fallback rate — verify size manually\n"

    if regime.get("high_impact_event_imminent"):
        msg += "⚠️ High-impact macro event imminent — reduced size applied\n"

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
    vps_ip: str = "",
) -> str:
    """Format the daily morning briefing message (1:00 PM BST)."""
    from datetime import date, datetime
    import pytz

    today = date.today().strftime("%A %d %B %Y").replace(" 0", " ")
    _vps_ip = vps_ip or settings.VPS_IP

    # Compute accurate time-to-open so the message is factually correct.
    # The briefing fires at 13:00 BST; US market opens at 13:30 BST.
    try:
        _bst = pytz.timezone("Europe/London")
        _now = datetime.now(_bst)
        _open = _now.replace(hour=13, minute=30, second=0, microsecond=0)
        _mins = int((_open - _now).total_seconds() / 60)
        if _mins > 0:
            market_status = f"US market opens in ~{_mins} min 🔔"
        else:
            market_status = "US market is OPEN 🟢"
    except Exception:
        market_status = "US market opens at 13:30 BST 🔔"

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
        f"{market_status}\n"
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
        f"Dashboard: http://{_vps_ip}:{settings.DASHBOARD_PORT}"
    )


def format_bear_market_alert(regime: dict = None) -> str:
    """
    Format the bear-market suppression alert.

    Accepts the full regime dict so the actual trigger reason is shown
    rather than a hardcoded 'SPY below 200-day SMA' message which was
    factually wrong when the trigger was distribution days or high VIX.
    """
    if regime is not None:
        dist = regime.get("distribution_days", 0)
        vix  = regime.get("vix_level", 0.0)
        breadth = regime.get("breadth_pct")
        spy_above = regime.get("spy_above_sma200", None)

        reason_lines = []
        if spy_above is False:
            spy_close  = regime.get("spy_close", "?")
            spy_sma200 = regime.get("spy_sma200", "?")
            reason_lines.append(f"📉 SPY below 200-day SMA (${spy_close} < ${spy_sma200})")
        if dist >= settings.DISTRIBUTION_DAYS_DANGER:
            reason_lines.append(
                f"📊 Distribution days: {dist}/{settings.DISTRIBUTION_DAYS_DANGER} "
                f"(danger threshold reached)"
            )
        if vix >= settings.VIX_DANGER:
            reason_lines.append(f"😱 VIX: {vix:.1f} — extreme fear")
        if breadth is not None and breadth < settings.BREADTH_BEAR:
            reason_lines.append(f"📉 Breadth: {breadth:.1f}% — bear territory")

        # If no specific cause matched, show the full summary
        if not reason_lines:
            summary = regime.get("regime_summary", "Signals suppressed")
            reason_lines.append(summary)

        reasons = "\n".join(reason_lines)
    else:
        reasons = "Market conditions do not favour new long positions."

    return (
        f"⚠️ BEAR MARKET MODE — Signals Suppressed\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{reasons}\n"
        f"\n"
        f"No new long positions.\n"
        f"Monitor existing stops carefully.\n"
        f"Cash is a position. 💵"
    )


def format_earnings_assessment(assessments: list[dict]) -> str:
    """Format post-earnings watchlist assessment for morning update."""
    if not assessments:
        return "📋 No earnings from watchlist stocks in last 2 days."
    lines = ["📋 POST-EARNINGS ASSESSMENT:"]
    for a in assessments:
        lines.append(f"  {a['note']} — ${a['ticker']}")
    return "\n".join(lines)
