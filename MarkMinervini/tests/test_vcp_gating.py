"""
Unit tests for VCP gating invariants (patterns/vcp_detector.py).

detect_vcp() signature:
    detect_vcp(ticker: str, df: pd.DataFrame,
               trend_template_passes: bool, rs_line_new_high: bool = False)

Strategy:
  - trend_template_passes=False → fast-path rejection at Step 1 (trend gate).
    Used for structural tests and for explicitly testing trend-gate rejection.
  - trend_template_passes=True  → proceed into VCP scoring logic.
    Used for invariant tests and for testing gates deeper in the algorithm
    (data-length check, breakout gate).

Tests focus on:
  1. Return-dict structure — all expected keys always present
  2. Universal invariant: alert=True implies breakout_confirmed=True
  3. Trend-gate rejection cases (downtrend, flat stock)
  4. Data-length rejection (< minimum bars)
  5. Breakout-gate rejection (close well below pivot zone)
  6. Intraday stop_pct and 2R/3R target arithmetic (pure calculation tests)

Full positive-path tests and contraction-tightening tests are deferred
until the paper-trading phase establishes which synthetic patterns reliably
score >= 80 under current settings.
"""
import numpy as np
import pandas as pd
import pytest

from patterns.vcp_detector import detect_vcp


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _ohlcv(prices, volumes=None, start="2021-01-01"):
    """Build a minimal OHLCV DataFrame from a 1-D price array."""
    n = len(prices)
    prices = np.asarray(prices, dtype=float)
    if volumes is None:
        volumes = np.full(n, 1_000_000.0)
    return pd.DataFrame(
        {
            "Open":   prices * 0.999,
            "High":   prices * 1.010,
            "Low":    prices * 0.990,
            "Close":  prices,
            "Volume": volumes.astype(float),
        },
        index=pd.date_range(start, periods=n, freq="B"),
    )


def _uptrend_then_base(n_up=200, n_base=95, base_price=100.0,
                        noise_std=0.3, seed=42):
    """Return a 300-bar uptrend-then-flat-base price series."""
    rng = np.random.default_rng(seed)
    uptrend = np.linspace(40.0, base_price * 1.05, n_up)
    base    = np.full(n_base, base_price)
    prices  = np.concatenate([uptrend, base])
    prices += rng.normal(0, noise_std, len(prices))
    return prices


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------

class TestReturnDictStructure:
    """Verify return-dict shape regardless of the rejection path taken."""

    def test_all_required_keys_present_on_trend_gate_rejection(self):
        """All expected keys must exist even on fast trend-gate rejection."""
        prices = np.linspace(200, 50, 300)
        result = detect_vcp(
            ticker="TEST_KEYS",
            df=_ohlcv(prices),
            trend_template_passes=False,
        )
        required_keys = (
            "alert", "watchlist_candidate", "vcp_score",
            "breakout_confirmed", "rejection_reason",
        )
        for key in required_keys:
            assert key in result, f"missing key: '{key}'"

    def test_all_required_keys_present_when_attempting_vcp(self):
        """All keys must exist when detector proceeds past the trend gate."""
        prices = _uptrend_then_base()
        result = detect_vcp(
            ticker="TEST_KEYS2",
            df=_ohlcv(prices),
            trend_template_passes=True,
        )
        required_keys = (
            "alert", "watchlist_candidate", "vcp_score",
            "breakout_confirmed", "rejection_reason",
        )
        for key in required_keys:
            assert key in result, f"missing key: '{key}'"

    def test_result_is_dict(self):
        result = detect_vcp(
            ticker="TEST_DICT",
            df=_ohlcv(np.linspace(100, 50, 300)),
            trend_template_passes=False,
        )
        assert isinstance(result, dict)

    def test_vcp_score_is_numeric(self):
        result = detect_vcp(
            ticker="TEST_SCORE",
            df=_ohlcv(np.linspace(100, 50, 300)),
            trend_template_passes=False,
        )
        assert isinstance(result["vcp_score"], (int, float))

    def test_alert_is_bool(self):
        result = detect_vcp(
            ticker="TEST_BOOL",
            df=_ohlcv(np.linspace(100, 50, 300)),
            trend_template_passes=False,
        )
        assert isinstance(result["alert"], bool)


# ---------------------------------------------------------------------------
# Universal invariant: alert=True → breakout_confirmed=True
# ---------------------------------------------------------------------------

class TestAlertGatingInvariant:
    """
    The alert gate requires breakout_confirmed=True (close > pivot AND volume
    >= 1.4× 50-day avg).  No amount of score or other checks should cause
    alert=True without confirmed breakout.

    Tests pass trend_template_passes=True so the detector attempts full VCP
    scoring — this exercises the gate logic, not just the trend-template exit.
    """

    def _run_and_check(self, prices, volumes=None, ticker="INV",
                       trend_template_passes=True):
        """Run detector and enforce alert → breakout_confirmed invariant."""
        result = detect_vcp(
            ticker=ticker,
            df=_ohlcv(prices, volumes=volumes),
            trend_template_passes=trend_template_passes,
        )
        if result["alert"]:
            assert result["breakout_confirmed"] is True, (
                f"GATE VIOLATION: alert=True but breakout_confirmed="
                f"{result['breakout_confirmed']} (ticker={ticker})"
            )
        return result

    def test_invariant_trend_gate_rejected(self):
        """Invariant holds trivially when trend gate rejects."""
        self._run_and_check(
            np.linspace(200, 50, 300),
            trend_template_passes=False,
        )

    def test_invariant_on_pure_uptrend_no_base(self):
        """Continuous uptrend has no base — VCP scoring fails; invariant holds."""
        self._run_and_check(np.linspace(50, 150, 300))

    def test_invariant_on_uptrend_then_base(self):
        """Uptrend + flat base may or may not score >= 80; invariant must hold."""
        self._run_and_check(_uptrend_then_base())

    def test_invariant_with_high_volume_last_bar(self):
        """Elevated volume on last bar doesn't circumvent breakout gate."""
        prices = _uptrend_then_base()
        volumes = np.full(len(prices), 1_000_000.0)
        volumes[-1] = 5_000_000.0
        self._run_and_check(prices, volumes=volumes)

    def test_invariant_close_below_base_no_alert(self):
        """Last close forced far below the base → breakout impossible → alert=False."""
        prices = _uptrend_then_base()
        prices[-1] = 70.0   # sharp gap-down well below the base (~100)
        result = detect_vcp(
            ticker="GAP_DOWN",
            df=_ohlcv(prices),
            trend_template_passes=True,
        )
        assert result["alert"] is False


# ---------------------------------------------------------------------------
# Explicit negative cases
# ---------------------------------------------------------------------------

class TestNegativeCases:

    def test_trend_gate_false_never_alerts(self):
        """trend_template_passes=False is the trend-gate rejection path; alert=False."""
        for prices in (
            np.linspace(200, 50, 300),    # downtrend
            np.full(300, 50.0),            # flat
            np.linspace(50, 150, 300),     # uptrend — still fails if caller says False
        ):
            result = detect_vcp(
                ticker="GATE_FALSE",
                df=_ohlcv(prices),
                trend_template_passes=False,
            )
            assert result["alert"] is False, (
                "Detector must respect trend_template_passes=False"
            )

    def test_insufficient_data_not_alerted(self):
        """
        Fewer bars than minimum required (>= 60 per docstring) must not alert.
        Pass trend_template_passes=True so the detector hits the data-length
        guard rather than the trend-gate exit.
        """
        prices = np.linspace(50, 100, 40)   # only 40 bars
        result = detect_vcp(
            ticker="SHORT",
            df=_ohlcv(prices),
            trend_template_passes=True,
        )
        assert result["alert"] is False

    def test_watchlist_candidate_not_set_when_trend_gate_rejects(self):
        """
        When trend_template_passes=False, watchlist_candidate must also be False —
        the stock didn't even pass the trend template, so it is not a candidate.
        """
        result = detect_vcp(
            ticker="NO_TREND",
            df=_ohlcv(np.linspace(200, 50, 300)),
            trend_template_passes=False,
        )
        assert result["watchlist_candidate"] is False


# ---------------------------------------------------------------------------
# Intraday stop_pct and target arithmetic (pure calculation tests)
# ---------------------------------------------------------------------------

class TestIntradayStopPctFormula:
    """
    Verify the arithmetic of the formula used in _send_intraday_breakout_alert():

        risk_per_share = entry_price - stop_price
        stop_pct       = risk_per_share / entry_price * 100
        target_1       = entry_price + 2 * risk_per_share
        target_2       = entry_price + 3 * risk_per_share

    Pure calculation tests — no VCP detector involved.
    """

    def test_live_entry_gives_higher_stop_pct_than_original(self):
        """
        Live entry $105, stored stop $93 (calculated when entry was $100).
        Stale stored stop_pct = 7%.  Live stop_pct = 11.43%.
        The live formula must produce the materially higher value.
        """
        live_entry  = 105.0
        stored_stop =  93.0
        risk = live_entry - stored_stop
        live_pct = risk / live_entry * 100

        assert live_pct == pytest.approx(11.43, rel=0.01)
        stale_pct = (100.0 - stored_stop) / 100.0 * 100   # entry was $100
        assert live_pct > stale_pct

    def test_stop_pct_matches_stored_when_entry_unchanged(self):
        """When live entry equals the original setup entry, pct values agree."""
        entry = 100.0
        stored_stop_price = 93.0
        risk = entry - stored_stop_price
        computed = risk / entry * 100
        stored_pct = (entry - stored_stop_price) / entry * 100
        assert computed == pytest.approx(stored_pct)

    def test_target_1_is_2r(self):
        """T1 = entry + 2 × risk_per_share gives exactly 2R."""
        entry, stop = 100.0, 93.0
        r  = entry - stop        # $7
        t1 = entry + 2 * r       # $114
        assert t1 == pytest.approx(114.0)

    def test_target_2_is_3r(self):
        """T2 = entry + 3 × risk_per_share gives exactly 3R."""
        entry, stop = 100.0, 93.0
        r  = entry - stop        # $7
        t2 = entry + 3 * r       # $121
        assert t2 == pytest.approx(121.0)

    def test_larger_intraday_gap_increases_stop_pct(self):
        """A larger price gap from original entry increases the true stop percentage."""
        stop           =  93.0
        entry_original = 100.0
        entry_intraday = 110.0

        pct_original  = (entry_original - stop) / entry_original * 100
        pct_intraday  = (entry_intraday - stop) / entry_intraday * 100

        assert pct_intraday > pct_original
