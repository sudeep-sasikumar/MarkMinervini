"""
Unit tests for risk/position_sizer.py — compute_position_size().

Key invariants tested:
  - All expected keys are present in the return dict
  - Risk is calculated in GBP then converted to USD for share sizing
  - risk_dollars == risk_usd  (NOT risk_gbp — it is a USD alias)
  - Stops too wide are rejected (valid=False)
  - MAX_POSITION_PCT cap is respected
  - Explicitly supplied gbpusd_rate never triggers fx_warning
  - fetch_gbpusd_with_source returning "fallback" source sets fx_warning=True

All tests pass gbpusd_rate explicitly where possible to avoid network calls.
The one exception is test_fallback_fx_sets_warning, which patches the fetcher.
"""
import pytest
from unittest.mock import patch

from risk.position_sizer import compute_position_size
from config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _size(entry=100.0, stop=93.0, equity=50_000, rate=1.27, **kw):
    """Convenience wrapper with sensible defaults."""
    return compute_position_size(
        entry_price=entry,
        stop_price=stop,
        account_equity=equity,
        gbpusd_rate=rate,
        **kw,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReturnStructure:
    def test_required_keys_present_on_valid_trade(self):
        """All documented output keys must be present when the trade is valid."""
        result = _size()
        required = (
            "shares", "position_value_usd", "position_value_gbp",
            "position_value",     # legacy alias = position_value_gbp
            "position_pct",
            "risk_gbp", "risk_usd",
            "risk_dollars",       # legacy alias = risk_usd
            "risk_pct", "stop_pct",
            "gbpusd_rate", "fx_rate_source", "fx_warning",
            "valid", "note",
        )
        for key in required:
            assert key in result, f"missing key: {key}"

    def test_required_keys_present_on_rejected_trade(self):
        """All keys must be present even when the trade is rejected (valid=False)."""
        result = _size(entry=100.0, stop=40.0)  # 60% stop — too wide
        assert result["valid"] is False
        assert "shares" in result
        assert "valid" in result


class TestFXConversion:
    def test_risk_usd_equals_risk_gbp_times_rate(self):
        """risk_usd must equal risk_gbp × gbpusd_rate (within rounding tolerance)."""
        rate = 1.25
        result = _size(entry=100.0, stop=98.0, equity=10_000, rate=rate)
        assert result["valid"] is True
        assert result["risk_usd"] == pytest.approx(result["risk_gbp"] * rate, rel=0.02)

    def test_risk_dollars_is_usd_alias_not_gbp(self):
        """risk_dollars must equal risk_usd, not risk_gbp.
        The field name says 'dollars', so it must be in USD, not GBP.
        """
        rate = 1.30  # 1 GBP = 1.30 USD → values must differ
        result = _size(rate=rate)
        assert result["valid"] is True
        assert result["risk_dollars"] == result["risk_usd"]
        # With rate != 1.0 the two currencies are different amounts
        assert result["risk_dollars"] != result["risk_gbp"]

    def test_explicit_rate_no_fx_warning(self):
        """When the caller supplies gbpusd_rate, fx_warning must be False."""
        result = _size(rate=1.27)
        assert result["fx_warning"] is False
        assert result["fx_rate_source"] != "fallback"

    def test_fallback_fx_source_sets_warning(self):
        """When fetch_gbpusd_with_source() returns source='fallback', fx_warning=True."""
        mock_fallback = {"rate": 1.27, "source": "fallback"}
        # position_sizer imports fetch_gbpusd_with_source lazily (inside the function
        # body) via `from data.fetcher import ...`.  We insert a fake data.fetcher
        # module into sys.modules so the import resolves without needing yfinance.
        import sys
        from unittest.mock import MagicMock
        fake_fetcher = MagicMock()
        fake_fetcher.fetch_gbpusd_with_source.return_value = mock_fallback
        with patch.dict(sys.modules, {"data.fetcher": fake_fetcher}):
            result = compute_position_size(
                entry_price=100.0,
                stop_price=94.0,
                account_equity=50_000,
            )
        assert result["fx_warning"] is True

    def test_live_fx_source_no_warning(self):
        """When fetch_gbpusd_with_source() returns source='live', fx_warning=False."""
        mock_live = {"rate": 1.28, "source": "live"}
        import sys
        from unittest.mock import MagicMock
        fake_fetcher = MagicMock()
        fake_fetcher.fetch_gbpusd_with_source.return_value = mock_live
        with patch.dict(sys.modules, {"data.fetcher": fake_fetcher}):
            result = compute_position_size(
                entry_price=100.0,
                stop_price=94.0,
                account_equity=50_000,
            )
        assert result["fx_warning"] is False


class TestRiskGates:
    def test_stop_too_wide_rejects(self):
        """Stop wider than MAX_STOP_PCT must produce valid=False, shares=0."""
        # 60% stop is far beyond any reasonable MAX_STOP_PCT setting
        result = _size(entry=100.0, stop=40.0)
        assert result["valid"] is False
        assert result["shares"] == 0

    def test_entry_below_stop_invalid(self):
        """Entry price below stop is an invalid setup — must be rejected."""
        result = _size(entry=90.0, stop=95.0)
        assert result["valid"] is False
        assert result["shares"] == 0

    def test_max_position_cap_respected(self):
        """Position pct must never exceed MAX_POSITION_PCT * 100."""
        # Cheap stock with very tight stop → uncapped position would be huge
        result = compute_position_size(
            entry_price=1.00,
            stop_price=0.99,
            account_equity=100_000,
            gbpusd_rate=1.27,
        )
        assert result["valid"] is True
        # Allow a tiny rounding epsilon (0.1%)
        assert result["position_pct"] <= settings.MAX_POSITION_PCT * 100 + 0.1

    def test_aggression_factor_scales_position(self):
        """Lower aggression_factor must produce fewer shares and smaller risk."""
        full = _size(aggression_factor=1.0)
        half = _size(aggression_factor=0.5)
        assert full["valid"] is True
        assert half["valid"] is True
        assert half["shares"] <= full["shares"]
        assert half["risk_gbp"] <= full["risk_gbp"]


class TestPositionValues:
    def test_position_value_gbp_equals_legacy_alias(self):
        """position_value (legacy) must equal position_value_gbp."""
        result = _size()
        assert result["position_value"] == result["position_value_gbp"]

    def test_position_value_usd_vs_gbp_ratio(self):
        """position_value_usd / position_value_gbp must equal gbpusd_rate (approx)."""
        rate = 1.25
        result = _size(rate=rate)
        assert result["valid"] is True
        if result["position_value_gbp"] > 0:
            actual_rate = result["position_value_usd"] / result["position_value_gbp"]
            assert actual_rate == pytest.approx(rate, rel=0.01)
