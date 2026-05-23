"""
Pivot point, stop, and target calculator.
Takes VCP detection output and enriches it with R-multiples and position sizing hints.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def compute_targets(
    entry_price: float,
    stop_price: float,
    r_multiples: list[float] = None,
) -> dict:
    """
    Compute price targets at given R-multiples.

    Args:
        entry_price:  Planned entry price
        stop_price:   Stop-loss price
        r_multiples:  List of R multiples to compute [2, 3] by default

    Returns:
        {
          "risk_per_share": float,
          "targets": {2: price, 3: price, ...},
          "stop_pct": float,
        }
    """
    if r_multiples is None:
        r_multiples = [2.0, 3.0]

    risk_per_share = entry_price - stop_price
    stop_pct = risk_per_share / entry_price * 100

    targets = {}
    for r in r_multiples:
        targets[r] = round(entry_price + risk_per_share * r, 2)

    return {
        "risk_per_share": round(risk_per_share, 2),
        "stop_pct": round(stop_pct, 2),
        "targets": targets,
    }


def enrich_vcp_result(vcp: dict) -> dict:
    """
    Add target labels and R-multiple calculations to a VCP result dict.
    Modifies in place and returns the same dict.
    """
    if not vcp.get("entry_price") or not vcp.get("stop_price"):
        return vcp

    calc = compute_targets(vcp["entry_price"], vcp["stop_price"])
    vcp["risk_per_share"] = calc["risk_per_share"]
    vcp["target_1"] = calc["targets"].get(2.0)
    vcp["target_2"] = calc["targets"].get(3.0)

    entry = vcp["entry_price"]
    if vcp["target_1"]:
        vcp["target_1_pct"] = round((vcp["target_1"] / entry - 1) * 100, 1)
    if vcp["target_2"]:
        vcp["target_2_pct"] = round((vcp["target_2"] / entry - 1) * 100, 1)

    return vcp


if __name__ == "__main__":
    result = compute_targets(entry_price=100.0, stop_price=93.0)
    print(f"pivot_calculator.py: risk={result['risk_per_share']}, "
          f"T1={result['targets'][2.0]}, T2={result['targets'][3.0]}")
