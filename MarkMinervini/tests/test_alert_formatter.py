"""
Unit tests for alerts/alert_formatter.py.

Covers:
  - format_breakout_alert: GBP/USD dual display, FX warning, macro warning
  - format_morning_briefing: economic_events accepts list[str] and list[dict]
"""
import pytest

from alerts.alert_formatter import format_breakout_alert, format_morning_briefing


# ---------------------------------------------------------------------------
# Shared input factories
# ---------------------------------------------------------------------------

def _vcp(vcp_score=88, entry=100.0, stop=93.0, t1=114.0, t2=121.0):
    return {
        "grade": "HIGH QUALITY VCP",
        "vcp_score": vcp_score,
        "entry_price": entry,
        "stop_price": stop,
        "stop_pct": (entry - stop) / entry * 100,
        "target_1": t1,
        "target_2": t2,
        "base_days": 35,
        "contractions": [
            {"depth_pct": 20.0},
            {"depth_pct": 15.0},
            {"depth_pct": 10.0},
        ],
    }


def _trend():
    return {
        "score": 8,
        "details": {
            "c4_sma200_rising_strong": True,
            "pct_from_52wk_high": -3.5,
        },
    }


def _fundamentals():
    return {
        "eps_growth_yoy": 45.0,
        "rev_growth_yoy": 30.0,
        "gross_margin_current": 0.65,
        "gross_margin_prior": 0.60,
    }


def _position(fx_warning=False, pos_gbp=4_960.0, pos_usd=6_299.0,
              risk_gbp=441.0, risk_usd=559.0):
    return {
        "shares": 63,
        "position_value_gbp": pos_gbp,
        "position_value_usd": pos_usd,
        "position_value": pos_gbp,  # legacy alias
        "position_pct": 9.9,
        "risk_gbp": risk_gbp,
        "risk_usd": risk_usd,
        "risk_dollars": risk_usd,  # USD alias
        "gbpusd_rate": 1.27,
        "fx_warning": fx_warning,
    }


def _regime(high_impact=False, label="BULL"):
    return {
        "regime": label,
        "vix_level": 14.2,
        "breadth_pct": 68.0,
        "aggression_factor": 1.0,
        "distribution_days": 1,
        "high_impact_event_imminent": high_impact,
    }


def _call_breakout(**overrides):
    kwargs = dict(
        ticker="ACME",
        company_name="Acme Corp",
        sector="Technology",
        sector_stage2=True,
        vcp=_vcp(),
        trend=_trend(),
        fundamentals=_fundamentals(),
        position=_position(),
        regime=_regime(),
        ai_news={"sentiment": "BULLISH", "catalyst_type": "Earnings beat",
                 "offline": False},
        ai_earnings={"genuine_growth": True},
        rs_rating=92.0,
        rs_line_new_high=True,
        earnings_warning="",
        vps_ip="1.2.3.4",
    )
    kwargs.update(overrides)
    return format_breakout_alert(**kwargs)


# ---------------------------------------------------------------------------
# format_breakout_alert tests
# ---------------------------------------------------------------------------

class TestFormatBreakoutAlert:

    def test_returns_non_empty_string(self):
        """The formatter must return a non-empty string."""
        msg = _call_breakout()
        assert isinstance(msg, str)
        assert len(msg) > 100

    def test_ticker_present(self):
        """The alert must contain the ticker symbol."""
        msg = _call_breakout()
        assert "ACME" in msg

    def test_shows_gbp_position(self):
        """Position value in GBP (£) must appear in the alert."""
        pos = _position(pos_gbp=4_960.0, pos_usd=6_299.0)
        msg = _call_breakout(position=pos)
        assert "£4,960" in msg

    def test_shows_usd_position(self):
        """Position value in USD ($) must appear alongside GBP."""
        pos = _position(pos_gbp=4_960.0, pos_usd=6_299.0)
        msg = _call_breakout(position=pos)
        assert "$6,299" in msg

    def test_shows_gbp_max_loss(self):
        """Max loss in GBP must appear in the alert."""
        pos = _position(risk_gbp=441.0, risk_usd=559.0)
        msg = _call_breakout(position=pos)
        assert "£441" in msg

    def test_shows_usd_max_loss(self):
        """Max loss in USD must appear alongside GBP."""
        pos = _position(risk_gbp=441.0, risk_usd=559.0)
        msg = _call_breakout(position=pos)
        assert "$559" in msg

    def test_fx_warning_shown_when_flagged(self):
        """FX fallback warning line must appear when position fx_warning=True."""
        msg = _call_breakout(position=_position(fx_warning=True))
        assert "FX fallback" in msg

    def test_no_fx_warning_when_live(self):
        """No FX fallback warning when position fx_warning=False."""
        msg = _call_breakout(position=_position(fx_warning=False))
        assert "FX fallback" not in msg

    def test_macro_warning_when_high_impact(self):
        """Macro event warning must appear when regime.high_impact_event_imminent=True."""
        msg = _call_breakout(regime=_regime(high_impact=True))
        assert "High-impact macro event" in msg or "macro event" in msg.lower()

    def test_no_macro_warning_when_clear(self):
        """No macro warning when high_impact_event_imminent=False."""
        msg = _call_breakout(regime=_regime(high_impact=False))
        assert "High-impact macro" not in msg

    def test_earnings_warning_included(self):
        """earnings_warning string is included when non-empty."""
        msg = _call_breakout(earnings_warning="⚠️ Earnings in 3 days")
        assert "Earnings in 3 days" in msg

    def test_regime_bull_shown(self):
        """BULL regime must appear in the alert."""
        msg = _call_breakout(regime=_regime(label="BULL"))
        assert "BULL" in msg

    def test_rs_line_new_high_shown(self):
        """RS Line NEW HIGH flag must appear when rs_line_new_high=True."""
        msg = _call_breakout(rs_line_new_high=True)
        assert "RS Line" in msg and "NEW HIGH" in msg


# ---------------------------------------------------------------------------
# format_morning_briefing tests
# ---------------------------------------------------------------------------

def _briefing_kwargs(**overrides):
    base = dict(
        regime=_regime(),
        watchlist=[{"ticker": "AAPL"}, {"ticker": "NVDA"}],
        near_pivot=["AAPL"],
        earnings_blocked=["MSFT"],
        sector_leaders=[{"sector": "Technology"}],
        weak_sectors=["Utilities"],
        economic_events=[],
        vps_ip="1.2.3.4",
    )
    base.update(overrides)
    return base


class TestFormatMorningBriefing:

    def test_returns_non_empty_string(self):
        msg = format_morning_briefing(**_briefing_kwargs())
        assert isinstance(msg, str) and len(msg) > 50

    def test_no_events_shows_none_today(self):
        """Empty event list should show 'None today'."""
        msg = format_morning_briefing(**_briefing_kwargs(economic_events=[]))
        assert "None today" in msg

    def test_economic_events_as_strings(self):
        """Accepts list[str] — must include event name in output."""
        msg = format_morning_briefing(
            **_briefing_kwargs(economic_events=["CPI Data", "Fed Minutes"])
        )
        assert "CPI Data" in msg

    def test_economic_events_as_dicts(self):
        """Accepts list[dict] from get_high_impact_events() — must extract 'event' key."""
        events = [
            {"event": "CPI Data", "time": "08:30", "impact": "high"},
            {"event": "Fed Minutes", "time": "14:00", "impact": "high"},
        ]
        msg = format_morning_briefing(**_briefing_kwargs(economic_events=events))
        assert "CPI Data" in msg

    def test_economic_events_dict_missing_event_key(self):
        """Dicts without 'event' key must not crash — falls back to str()."""
        events = [{"description": "something", "time": "09:00"}]
        # Should not raise
        msg = format_morning_briefing(**_briefing_kwargs(economic_events=events))
        assert isinstance(msg, str)

    def test_watchlist_count_shown(self):
        """Watchlist count must appear in the briefing."""
        kwargs = _briefing_kwargs(
            watchlist=[{"ticker": "A"}, {"ticker": "B"}, {"ticker": "C"}]
        )
        msg = format_morning_briefing(**kwargs)
        assert "3" in msg
