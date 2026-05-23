"""
Performance metrics for backtesting (Section 14).
Computes: Sharpe, Sortino, CAGR, Max Drawdown, Win Rate, Expectancy, Avg Win/Loss.
All functions take a pandas Series of daily portfolio values.
"""

import numpy as np
import pandas as pd


def compute_cagr(portfolio: pd.Series) -> float:
    """Compound Annual Growth Rate as a percentage."""
    if len(portfolio) < 2 or float(portfolio.iloc[0]) == 0:
        return 0.0
    years = len(portfolio) / 252
    total_return = float(portfolio.iloc[-1]) / float(portfolio.iloc[0])
    return (total_return ** (1 / years) - 1) * 100


def compute_sharpe(portfolio: pd.Series, risk_free_rate: float = 0.05) -> float:
    """Annualised Sharpe ratio. risk_free_rate is annual (e.g. 0.05 = 5%)."""
    returns = portfolio.pct_change().dropna()
    if returns.std() == 0:
        return 0.0
    excess = returns - risk_free_rate / 252
    return float(excess.mean() / excess.std() * np.sqrt(252))


def compute_sortino(portfolio: pd.Series, risk_free_rate: float = 0.05) -> float:
    """Annualised Sortino ratio (uses downside deviation only)."""
    returns = portfolio.pct_change().dropna()
    excess = returns - risk_free_rate / 252
    downside = excess[excess < 0]
    if len(downside) == 0 or downside.std() == 0:
        return 0.0
    return float(excess.mean() / downside.std() * np.sqrt(252))


def compute_max_drawdown(portfolio: pd.Series) -> float:
    """Maximum peak-to-trough drawdown as a percentage (positive number)."""
    rolling_max = portfolio.cummax()
    drawdown = (portfolio - rolling_max) / rolling_max
    return float(abs(drawdown.min()) * 100)


def compute_win_rate(trade_returns: list[float]) -> float:
    """Percentage of trades with positive return."""
    if not trade_returns:
        return 0.0
    wins = sum(1 for r in trade_returns if r > 0)
    return wins / len(trade_returns) * 100


def compute_expectancy(trade_returns: list[float]) -> float:
    """
    Expectancy in R-multiples.
    Expectancy = (Win Rate × Avg Win) - (Loss Rate × Avg Loss)
    Positive = edge; negative = no edge.
    """
    if not trade_returns:
        return 0.0
    wins = [r for r in trade_returns if r > 0]
    losses = [r for r in trade_returns if r <= 0]
    win_rate = len(wins) / len(trade_returns)
    loss_rate = 1 - win_rate
    avg_win = np.mean(wins) if wins else 0.0
    avg_loss = abs(np.mean(losses)) if losses else 0.0
    return float(win_rate * avg_win - loss_rate * avg_loss)


def compute_avg_win_loss_ratio(trade_returns: list[float]) -> float:
    """Average win / average loss ratio."""
    wins = [r for r in trade_returns if r > 0]
    losses = [abs(r) for r in trade_returns if r <= 0]
    if not wins or not losses:
        return 0.0
    return float(np.mean(wins) / np.mean(losses))


def compute_all_metrics(
    portfolio: pd.Series,
    trade_returns: list[float],
    benchmark: pd.Series = None,
) -> dict:
    """
    Compute the full metrics suite and return as a dict.
    portfolio: daily portfolio value series
    trade_returns: list of per-trade % returns
    benchmark: SPY daily value series (same index as portfolio)
    """
    metrics = {
        "cagr": round(compute_cagr(portfolio), 2),
        "sharpe": round(compute_sharpe(portfolio), 3),
        "sortino": round(compute_sortino(portfolio), 3),
        "max_drawdown": round(compute_max_drawdown(portfolio), 2),
        "win_rate": round(compute_win_rate(trade_returns), 1),
        "expectancy": round(compute_expectancy(trade_returns), 3),
        "avg_win_loss": round(compute_avg_win_loss_ratio(trade_returns), 2),
        "total_trades": len(trade_returns),
        "total_return": round((portfolio.iloc[-1] / portfolio.iloc[0] - 1) * 100, 2)
            if len(portfolio) > 1 else 0.0,
    }

    if benchmark is not None and len(benchmark) == len(portfolio):
        metrics["benchmark_return"] = round(
            (benchmark.iloc[-1] / benchmark.iloc[0] - 1) * 100, 2
        )
        metrics["alpha"] = round(metrics["total_return"] - metrics["benchmark_return"], 2)

    return metrics


if __name__ == "__main__":
    # Self-test with synthetic data
    dates = pd.date_range("2020-01-01", periods=252 * 3, freq="B")
    np.random.seed(42)
    returns = np.random.normal(0.0005, 0.01, len(dates))
    portfolio = pd.Series(50000 * np.cumprod(1 + returns), index=dates)
    trade_returns = list(np.random.normal(0.05, 0.08, 100))

    m = compute_all_metrics(portfolio, trade_returns)
    print("metrics.py: self-test:")
    for k, v in m.items():
        print(f"  {k}: {v}")
