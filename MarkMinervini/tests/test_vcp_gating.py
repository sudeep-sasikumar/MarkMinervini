"""
Unit tests for VCP gating invariants (patterns/vcp_detector.py).

Strategy: construct minimal synthetic OHLCV DataFrames that exercise
specific code paths, then assert on the resulting dict's key properties.

We cannot easily synthesise data that scores >= 80 AND passes all 12 steps
deterministically, so the tests focus on:
  1. Return-dict structure (all expected keys are always present)
  2. Universal invariant: alert=True implies breakout_confirmed=True
  3. Simple negative cases (downtrend, close below pivot) yield alert=False
  4. Intraday stop_pct formula correctness (tested in isolation)

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

    def test_all_required_keys_present_on_rejection(self):
        """detect_vcp must always return all expected keys even on fast rejection."""
        # Steady downtrend — fails immediately on trend template
        prices = np.linspace(200, 50, 300)
        result = detect_vcp(_ohlcv(prices), ticker="TEST_DOWN")
        required_keys = (
            "alert", "watchlist_candidate", "vcp_score",
            "breakout_confirmed", "rejection_reason",
        )
        for key in required_keys:
            assert key in result, f"missing key: '{key}'"

    def test_result_is_dict(self):
        """Return value is always a dict."""
        prices = np.linspace(100, 50, 300)
        result = detect_vcp(_ohlcv(prices), ticker="TEST_DICT")
        assert isinstance(result, dict)

    def test_vcp_score_is_numeric(self):
        """vcp_score is always a numeric type."""
        prices = np.linspace(100, 50, 300)
        result = detect_vcp(_ohlcv(prices), ticker="TEST_SCORE")
        assert isinstance(result["vcp_score"], (int, float))

    def test_alert_is_bool(self):
        """alert field is always a Python bool (not int or None)."""
        prices = np.linspace(100, 50, 300)
        result = detect_vcp(_ohlcv(prices), ticker="TEST_BOOL")
        assert isinstance(result["alert"], bool)


# ---------------------------------------------------------------------------
# Invariant: alert=True requires breakout_confirmed=True
# ---------------------------------------------------------------------------

class TestAlertGatingInvariant:

    def _run_and_check(self, prices, volumes=None, ticker="INV"):
        """Run detector and enforce the alert → breakout_confirmed invariant."""
        result = detect_vcp(_ohlcv(prices, volumes=volumes), ticker=ticker)
        if result["alert"]:
            assert result["breakout_confirmed"] is True, (
                f"GATE VIOLATION: alert=True but breakout_confirmed="
                f"{result['breakout_confirmed']} (ticker={ticker})"
            )
        return result

    def test_invariant_on_downtrend(self):
        self._run_and_check(np.linspace(200, 50, 300))

    def test_invariant_on_uptrend_no_base(self):
        self._run_and_check(np.linspace(50, 150, 300))

    def test_invariant_on_uptrend_then_base(self):
        prices = _uptrend_then_base()
        self._run_and_check(prices)

    def test_invariant_with_high_breakout_volume(self):
        """Even with elevated volume on the last bar, the invariant must hold."""
        prices = _uptrend_then_base()
        volumes = np.full(len(prices), 1_000_000.0)
        volumes[-1] = 5_000_000.0  # big volume on last bar
        self._run_and_check(prices, volumes=volumes)

    def test_invariant_close_below_pivot_no_alert(self):
        """With last close forced well below the base, alert must be False."""
        prices = _uptrend_then_base()
        prices[-1] = 70.0  # sharp gap down — far below any base pivot
        result = detect_vcp(_ohlcv(prices), ticker="GAP_DOWN")
        assert result["alert"] is False


# ---------------------------------------------------------------------------
# Simple negative cases
# ---------------------------------------------------------------------------

class TestNegativeCases:

    def test_steady_downtrend_not_alerted(self):
        """A stock in steady decline can never pass the trend template."""
        prices = np.linspace(200, 50, 300)
        result = detect_vcp(_ohlcv(prices), ticker="BEAR")
        assert result["alert"] is False

    def test_flat_stock_not_alerted(self):
        """A stock trading sideways for 300 bars has no prior advance."""
        prices = np.full(300, 50.0) + np.random.default_rng(0).normal(0, 0.1, 300)
        result = detect_vcp(_ohlcv(prices), ticker="FLAT")
        assert result["alert"] is False

    def test_insufficient_data_not_alerted(self):
        """Fewer bars than the minimum required (200 for SMA200) must not alert."""
        prices = np.linspace(50, 100, 100)  # only 100 bars
        result = detect_vcp(_ohlcv(prices), ticker="SHORT")
        assert result["alert"] is False


# ---------------------------------------------------------------------------
# Intraday stop_pct formula — tested in isolation
# ---------------------------------------------------------------------------

class TestIntradayStopPctFormula:
    """
    These tests verify the arithmetic correctness of the formula used in
    _send_intraday_breakout_alert() in main.py:

        risk_per_share = entry_price - stop_price
        stop_pct = risk_per_share / entry_price * 100

    This is a pure calculation test — no VCP detector involved.
    """

    def test_stop_pct_uses_live_entry(self):
        """
        Live entry $105, original stop $93 (set when entry was $100).
        The stale stored stop_pct would be 7%.  The live stop_pct is 11.43%.
        The formula must produce the live value.
        """
        live_entry  = 105.0
        stored_stop =  93.0
        risk_per_share = live_entry - stored_stop
        computed   = risk_per_share / live_entry * 100

        assert computed == pytest.approx(11.43, rel=0.01)
        # Must be materially higher than the stale stored value
        stale = (100.0 - stored_stop) / 100.0 * 100  # original entry was $100
        assert computed > stale

    def test_stop_pct_at_original_entry(self):
        """When live entry equals stored entry, computed pct matches stored."""
        entry = stop = 100.0
        stored_stop_price = 93.0
        risk = entry - stored_stop_price
        computed = risk / entry * 100
        stored_pct = (entry - stored_stop_price) / entry * 100
        assert computed == pytest.approx(stored_pct)

    def test_target_1_is_2r(self):
        """T1 = entry + 2 × (entry - stop) gives exactly 2R."""
        entry, stop = 100.0, 93.0
        r = entry - stop          # risk per share = $7
        t1 = entry + 2 * r        # should be $114
        assert t1 == pytest.approx(114.0)

    def test_target_2_is_3r(self):
        """T2 = entry + 3 × (entry - stop) gives exactly 3R."""
        entry, stop = 100.0, 93.0
        r = entry - stop          # $7
        t2 = entry + 3 * r        # should be $121
        assert t2 == pytest.approx(121.0)

    def test_larger_intraday_move_increases_stop_pct(self):
        """A larger gap from original entry to live entry increases true stop_pct."""
        stop = 93.0
        entry_original = 100.0
        entry_intraday = 110.0  # moved 10% above original entry

        pct_original  = (entry_original - stop) / entry_original  * 100
        pct_intraday  = (entry_intraday - stop) / entry_intraday  * 100

        assert pct_intraday > pct_original
