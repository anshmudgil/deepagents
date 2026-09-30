"""Pure, deterministic finance math used by the investment agent's tools.

Every function here is network-free and side-effect-free so it can be unit
tested exhaustively. Inputs are plain sequences of floats (prices or periodic
returns, oldest first); outputs are floats, lists, or small dicts.

Conventions:
    - Returns are simple (arithmetic) periodic returns, e.g. 0.01 == +1%.
    - Annualization assumes `periods_per_year` observations per year
      (252 for daily trading data).
    - Loss measures (VaR, CVaR, max drawdown) are reported as positive
      fractions, e.g. 0.12 == a 12% loss.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _as_array(values: Sequence[float], *, name: str, min_len: int = 1) -> np.ndarray:
    """Convert a sequence to a finite float array, validating its length.

    Args:
        values: Numeric sequence.
        name: Argument name used in error messages.
        min_len: Minimum required number of observations.

    Returns:
        A 1-D float64 numpy array.

    Raises:
        ValueError: If the sequence is too short or contains non-finite values.
    """
    arr = np.asarray(list(values), dtype=float)
    if arr.ndim != 1 or arr.size < min_len:
        msg = f"`{name}` needs at least {min_len} observations, got {arr.size}"
        raise ValueError(msg)
    if not np.all(np.isfinite(arr)):
        msg = f"`{name}` contains NaN or infinite values"
        raise ValueError(msg)
    return arr


def _positive_prices(prices: Sequence[float], *, min_len: int = 2) -> np.ndarray:
    """Validate a price series (finite and strictly positive).

    Args:
        prices: Price observations, oldest first.
        min_len: Minimum required number of observations.

    Returns:
        A 1-D float64 numpy array of prices.

    Raises:
        ValueError: If any price is non-positive.
    """
    arr = _as_array(prices, name="prices", min_len=min_len)
    if np.any(arr <= 0):
        msg = "`prices` must be strictly positive"
        raise ValueError(msg)
    return arr


# ---------------------------------------------------------------------------
# Returns and summary statistics
# ---------------------------------------------------------------------------


def simple_returns(prices: Sequence[float]) -> list[float]:
    """Compute simple periodic returns from a price series.

    Args:
        prices: Price observations, oldest first.

    Returns:
        A list of `len(prices) - 1` returns.
    """
    arr = _positive_prices(prices)
    return (arr[1:] / arr[:-1] - 1.0).tolist()


def annualized_return(
    prices: Sequence[float], periods_per_year: int = TRADING_DAYS
) -> float:
    """Compute the compound annual growth rate implied by a price series.

    Args:
        prices: Price observations, oldest first.
        periods_per_year: Observations per year.

    Returns:
        The CAGR as a fraction.
    """
    arr = _positive_prices(prices)
    years = (arr.size - 1) / periods_per_year
    return float((arr[-1] / arr[0]) ** (1.0 / years) - 1.0)


def annualized_volatility(
    returns: Sequence[float], periods_per_year: int = TRADING_DAYS
) -> float:
    """Compute annualized volatility (sample standard deviation).

    Args:
        returns: Periodic simple returns.
        periods_per_year: Observations per year.

    Returns:
        Annualized volatility as a fraction.
    """
    arr = _as_array(returns, name="returns", min_len=2)
    return float(np.std(arr, ddof=1) * math.sqrt(periods_per_year))


def sharpe_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float | None:
    """Compute the annualized Sharpe ratio.

    Args:
        returns: Periodic simple returns.
        risk_free_rate: Annual risk-free rate as a fraction.
        periods_per_year: Observations per year.

    Returns:
        The Sharpe ratio, or `None` when volatility is zero.
    """
    arr = _as_array(returns, name="returns", min_len=2)
    excess = arr - risk_free_rate / periods_per_year
    std = np.std(excess, ddof=1)
    if std == 0:
        return None
    return float(np.mean(excess) / std * math.sqrt(periods_per_year))


def sortino_ratio(
    returns: Sequence[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float | None:
    """Compute the annualized Sortino ratio (downside deviation denominator).

    Downside deviation is the root-mean-square of negative excess returns over
    all observations (target = risk-free rate).

    Args:
        returns: Periodic simple returns.
        risk_free_rate: Annual risk-free rate as a fraction.
        periods_per_year: Observations per year.

    Returns:
        The Sortino ratio, or `None` when there are no downside observations.
    """
    arr = _as_array(returns, name="returns", min_len=2)
    excess = arr - risk_free_rate / periods_per_year
    downside = np.minimum(excess, 0.0)
    dd = math.sqrt(float(np.mean(downside**2)))
    if dd == 0:
        return None
    return float(np.mean(excess) / dd * math.sqrt(periods_per_year))


def beta(
    asset_returns: Sequence[float], benchmark_returns: Sequence[float]
) -> float | None:
    """Compute beta of an asset versus a benchmark.

    Args:
        asset_returns: Asset periodic returns.
        benchmark_returns: Benchmark periodic returns, aligned with the asset.

    Returns:
        Beta, or `None` when the benchmark has zero variance.

    Raises:
        ValueError: If the series lengths differ.
    """
    a = _as_array(asset_returns, name="asset_returns", min_len=2)
    b = _as_array(benchmark_returns, name="benchmark_returns", min_len=2)
    if a.size != b.size:
        msg = f"Return series must be aligned: {a.size} vs {b.size} observations"
        raise ValueError(msg)
    var = np.var(b, ddof=1)
    if var == 0:
        return None
    return float(np.cov(a, b, ddof=1)[0, 1] / var)


def max_drawdown(prices: Sequence[float]) -> dict[str, float | int]:
    """Compute the maximum peak-to-trough drawdown of a price series.

    Args:
        prices: Price observations, oldest first.

    Returns:
        Dict with `max_drawdown` (positive fraction), `peak_index`,
        `trough_index`, and `current_drawdown` (from the running peak).
    """
    arr = _positive_prices(prices, min_len=1)
    running_peak = np.maximum.accumulate(arr)
    drawdowns = 1.0 - arr / running_peak
    trough = int(np.argmax(drawdowns))
    peak = int(np.argmax(arr[: trough + 1]))
    return {
        "max_drawdown": float(drawdowns[trough]),
        "peak_index": peak,
        "trough_index": trough,
        "current_drawdown": float(drawdowns[-1]),
    }


def historical_var(returns: Sequence[float], confidence: float = 0.95) -> float:
    """Compute one-period historical Value at Risk.

    Args:
        returns: Periodic simple returns.
        confidence: Confidence level in (0, 1).

    Returns:
        VaR as a positive loss fraction (0 when the quantile is a gain).
    """
    arr = _validated_tail_inputs(returns, confidence)
    quantile = float(np.quantile(arr, 1.0 - confidence))
    return max(0.0, -quantile)


def historical_cvar(returns: Sequence[float], confidence: float = 0.95) -> float:
    """Compute one-period historical Conditional VaR (expected shortfall).

    Args:
        returns: Periodic simple returns.
        confidence: Confidence level in (0, 1).

    Returns:
        Mean loss in the tail at or beyond the VaR quantile, as a positive
        fraction (0 when the tail average is a gain).
    """
    arr = _validated_tail_inputs(returns, confidence)
    quantile = np.quantile(arr, 1.0 - confidence)
    tail = arr[arr <= quantile]
    return max(0.0, -float(np.mean(tail)))


def _validated_tail_inputs(returns: Sequence[float], confidence: float) -> np.ndarray:
    """Validate inputs shared by the VaR and CVaR functions.

    Args:
        returns: Periodic simple returns.
        confidence: Confidence level in (0, 1).

    Returns:
        Validated returns array.

    Raises:
        ValueError: If `confidence` is outside (0, 1).
    """
    if not 0.0 < confidence < 1.0:
        msg = f"`confidence` must be in (0, 1), got {confidence}"
        raise ValueError(msg)
    return _as_array(returns, name="returns", min_len=2)


def wealth_index(returns: Sequence[float]) -> list[float]:
    """Rebuild a growth-of-1 series from returns, including the starting point.

    The leading 1.0 matters: without it a peak on the first day is lost and
    drawdowns measured from the start are understated.

    Args:
        returns: Periodic simple returns.

    Returns:
        `len(returns) + 1` values starting at 1.0.
    """
    arr = _as_array(returns, name="returns")
    return [1.0, *np.cumprod(1.0 + arr).tolist()]


def price_summary(
    prices: Sequence[float], periods_per_year: int = TRADING_DAYS
) -> dict[str, float]:
    """Summarize a price series without dumping raw data.

    Args:
        prices: Price observations, oldest first.
        periods_per_year: Observations per year.

    Returns:
        Dict with start/end/high/low prices, total return, CAGR, annualized
        volatility and max drawdown.
    """
    arr = _positive_prices(prices, min_len=3)
    rets = simple_returns(arr)
    return {
        "start": float(arr[0]),
        "end": float(arr[-1]),
        "high": float(arr.max()),
        "low": float(arr.min()),
        "total_return": float(arr[-1] / arr[0] - 1.0),
        "cagr": annualized_return(arr, periods_per_year),
        "annualized_volatility": annualized_volatility(rets, periods_per_year),
        "max_drawdown": float(max_drawdown(arr)["max_drawdown"]),
    }


def downsample(values: Sequence[float], points: int = 20) -> list[float]:
    """Pick evenly spaced observations (always keeping first and last).

    Args:
        values: Series to thin out.
        points: Target number of points.

    Returns:
        At most `points` values in original order.
    """
    arr = list(values)
    if points < 2 or len(arr) <= points:
        return [float(v) for v in arr]
    idx = np.linspace(0, len(arr) - 1, points).round().astype(int)
    return [float(arr[i]) for i in idx]


# ---------------------------------------------------------------------------
# Technical indicators
# ---------------------------------------------------------------------------


def sma(prices: Sequence[float], window: int) -> float | None:
    """Compute the latest simple moving average.

    Args:
        prices: Price observations, oldest first.
        window: Lookback length.

    Returns:
        The SMA of the last `window` prices, or `None` if history is too short.
    """
    arr = _as_array(prices, name="prices")
    if window <= 0:
        msg = f"`window` must be positive, got {window}"
        raise ValueError(msg)
    if arr.size < window:
        return None
    return float(np.mean(arr[-window:]))


def ema_series(values: Sequence[float], span: int) -> list[float]:
    """Compute an exponential moving average series (alpha = 2 / (span + 1)).

    The EMA is seeded with the first observation.

    Args:
        values: Observations, oldest first.
        span: EMA span.

    Returns:
        EMA values aligned with the input.
    """
    arr = _as_array(values, name="values")
    if span <= 0:
        msg = f"`span` must be positive, got {span}"
        raise ValueError(msg)
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(arr)
    out[0] = arr[0]
    for i in range(1, arr.size):
        out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
    return out.tolist()


def rsi(prices: Sequence[float], period: int = 14) -> float | None:
    """Compute Wilder's Relative Strength Index for the latest bar.

    Args:
        prices: Price observations, oldest first.
        period: Lookback period.

    Returns:
        RSI in [0, 100], or `None` if fewer than `period + 1` prices.
    """
    arr = _as_array(prices, name="prices")
    if arr.size < period + 1:
        return None
    deltas = np.diff(arr)
    gains = np.clip(deltas, 0, None)
    losses = np.clip(-deltas, 0, None)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for g, loss in zip(gains[period:], losses[period:], strict=True):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def macd(
    prices: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float] | None:
    """Compute the latest MACD line, signal line, and histogram.

    Args:
        prices: Price observations, oldest first.
        fast: Fast EMA span.
        slow: Slow EMA span.
        signal: Signal EMA span.

    Returns:
        Dict with `macd`, `signal`, `histogram`, or `None` if fewer than
        `slow + signal` prices.
    """
    arr = _as_array(prices, name="prices")
    if arr.size < slow + signal:
        return None
    line = np.asarray(ema_series(arr, fast)) - np.asarray(ema_series(arr, slow))
    sig = ema_series(line, signal)
    return {
        "macd": float(line[-1]),
        "signal": float(sig[-1]),
        "histogram": float(line[-1] - sig[-1]),
    }


def range_position(price: float, low: float, high: float) -> float | None:
    """Locate a price within a low-high range.

    Args:
        price: Current price.
        low: Range low.
        high: Range high.

    Returns:
        0.0 at the low, 1.0 at the high (clipped), or `None` for a flat range.
    """
    if high < low:
        msg = f"`high` ({high}) must be >= `low` ({low})"
        raise ValueError(msg)
    if high == low:
        return None
    return float(min(1.0, max(0.0, (price - low) / (high - low))))


def technical_snapshot(prices: Sequence[float]) -> dict[str, float | str | None]:
    """Compute the standard technical indicator set for the latest bar.

    Uses the last 252 observations for the 52-week range.

    Args:
        prices: Daily closes, oldest first.

    Returns:
        Dict with SMA50/200, RSI14, MACD, 52-week range position, and a
        coarse `trend` label.
    """
    arr = _positive_prices(prices)
    last = float(arr[-1])
    window = arr[-TRADING_DAYS:]
    sma50, sma200 = sma(arr, 50), sma(arr, 200)
    macd_vals = macd(arr) or {}
    return {
        "price": last,
        "sma_50": sma50,
        "sma_200": sma200,
        "price_vs_sma_200": (last / sma200 - 1.0) if sma200 else None,
        "rsi_14": rsi(arr, 14),
        "macd": macd_vals.get("macd"),
        "macd_signal": macd_vals.get("signal"),
        "macd_histogram": macd_vals.get("histogram"),
        "high_52w": float(window.max()),
        "low_52w": float(window.min()),
        "range_position_52w": range_position(
            last, float(window.min()), float(window.max())
        ),
        "trend": _trend_label(last, sma50, sma200),
    }


def _trend_label(price: float, sma50: float | None, sma200: float | None) -> str:
    """Classify trend from price and moving-average alignment.

    Args:
        price: Latest price.
        sma50: 50-period SMA.
        sma200: 200-period SMA.

    Returns:
        One of `uptrend`, `downtrend`, `mixed`, or `insufficient_data`.
    """
    if sma50 is None or sma200 is None:
        return "insufficient_data"
    if price > sma50 > sma200:
        return "uptrend"
    if price < sma50 < sma200:
        return "downtrend"
    return "mixed"


# ---------------------------------------------------------------------------
# Discounted cash flow
# ---------------------------------------------------------------------------


def dcf_valuation(
    base_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth: float,
    years: int = 5,
    net_debt: float = 0.0,
    shares_outstanding: float | None = None,
) -> dict[str, float | list[float] | None]:
    """Value a business with a two-stage (explicit + Gordon growth) DCF.

    Free cash flow grows at `growth_rate` for `years` periods, then at
    `terminal_growth` forever. Cash flows are discounted at end of period.

    Args:
        base_fcf: Most recent annual free cash flow (year 0).
        growth_rate: Annual FCF growth during the explicit period.
        discount_rate: Discount rate (WACC).
        terminal_growth: Perpetual growth after the explicit period.
        years: Length of the explicit forecast period.
        net_debt: Debt minus cash, subtracted from enterprise value.
        shares_outstanding: Share count for per-share value.

    Returns:
        Dict with projected FCFs, PV components, enterprise/equity value,
        per-share value (or `None`) and terminal value share of EV.

    Raises:
        ValueError: If `discount_rate <= terminal_growth` or `years < 1`.
    """
    if discount_rate <= terminal_growth:
        msg = f"`discount_rate` ({discount_rate}) must exceed `terminal_growth` ({terminal_growth})"
        raise ValueError(msg)
    if years < 1:
        msg = f"`years` must be >= 1, got {years}"
        raise ValueError(msg)
    fcfs = [base_fcf * (1.0 + growth_rate) ** t for t in range(1, years + 1)]
    pv_fcfs = [f / (1.0 + discount_rate) ** t for t, f in enumerate(fcfs, start=1)]
    terminal_value = (
        fcfs[-1] * (1.0 + terminal_growth) / (discount_rate - terminal_growth)
    )
    pv_terminal = terminal_value / (1.0 + discount_rate) ** years
    ev = sum(pv_fcfs) + pv_terminal
    equity = ev - net_debt
    per_share = equity / shares_outstanding if shares_outstanding else None
    return {
        "projected_fcf": fcfs,
        "pv_fcf_sum": sum(pv_fcfs),
        "terminal_value": terminal_value,
        "pv_terminal_value": pv_terminal,
        "enterprise_value": ev,
        "equity_value": equity,
        "value_per_share": per_share,
        "terminal_value_share": pv_terminal / ev if ev else None,
    }


def dcf_sensitivity(
    base_fcf: float,
    growth_rate: float,
    discount_rates: Sequence[float],
    terminal_growths: Sequence[float],
    years: int = 5,
    net_debt: float = 0.0,
    shares_outstanding: float | None = None,
) -> dict[str, object]:
    """Build a WACC x terminal-growth sensitivity table of DCF values.

    Cells where WACC <= terminal growth are `None` (undefined).

    Args:
        base_fcf: Most recent annual free cash flow.
        growth_rate: Explicit-period FCF growth.
        discount_rates: WACC values (table rows).
        terminal_growths: Terminal growth values (table columns).
        years: Explicit forecast period.
        net_debt: Debt minus cash.
        shares_outstanding: Share count; per-share values when provided,
            equity values otherwise.

    Returns:
        Dict with `rows` (WACCs), `columns` (terminal growths), `metric`, and
        `values` (list of rows).
    """
    table: list[list[float | None]] = []
    for wacc in discount_rates:
        row: list[float | None] = []
        for tg in terminal_growths:
            if wacc <= tg:
                row.append(None)
                continue
            res = dcf_valuation(
                base_fcf, growth_rate, wacc, tg, years, net_debt, shares_outstanding
            )
            row.append(
                res["value_per_share"] if shares_outstanding else res["equity_value"]
            )  # type: ignore[arg-type]
        table.append(row)
    return {
        "rows": list(discount_rates),
        "columns": list(terminal_growths),
        "metric": "value_per_share" if shares_outstanding else "equity_value",
        "values": table,
    }


def symmetric_grid(center: float, step: float, count: int = 5) -> list[float]:
    """Build an evenly spaced grid centered on a value.

    Args:
        center: Middle value.
        step: Spacing between points.
        count: Number of points (odd numbers keep `center` in the grid).

    Returns:
        Grid values rounded to 6 decimals.
    """
    half = (count - 1) / 2
    return [round(center + (i - half) * step, 6) for i in range(count)]


# ---------------------------------------------------------------------------
# Portfolio analytics
# ---------------------------------------------------------------------------


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    """Scale non-negative weights so they sum to 1.

    Args:
        weights: Mapping of asset to raw weight (dollars, shares value, or %).

    Returns:
        Normalized weights.

    Raises:
        ValueError: If weights are empty, negative, or sum to zero.
    """
    if not weights:
        msg = "`weights` must not be empty"
        raise ValueError(msg)
    if any(w < 0 for w in weights.values()):
        msg = "Negative weights (short positions) are not supported"
        raise ValueError(msg)
    total = float(sum(weights.values()))
    if total <= 0:
        msg = "`weights` must sum to a positive number"
        raise ValueError(msg)
    return {k: float(v) / total for k, v in weights.items()}


def herfindahl_index(weights: Mapping[str, float]) -> dict[str, float]:
    """Compute concentration (Herfindahl-Hirschman index) of a portfolio.

    Args:
        weights: Asset weights (normalized internally).

    Returns:
        Dict with `hhi` (0-1), `effective_positions` (1 / HHI) and
        `max_weight`.
    """
    w = np.asarray(list(normalize_weights(weights).values()))
    hhi = float(np.sum(w**2))
    return {"hhi": hhi, "effective_positions": 1.0 / hhi, "max_weight": float(w.max())}


def _returns_matrix(
    returns: Mapping[str, Sequence[float]], assets: Sequence[str]
) -> np.ndarray:
    """Stack aligned return series into a (T x N) matrix.

    Args:
        returns: Mapping of asset to periodic returns.
        assets: Asset order for the columns.

    Returns:
        The returns matrix.

    Raises:
        ValueError: If an asset is missing or series lengths differ.
    """
    missing = [a for a in assets if a not in returns]
    if missing:
        msg = f"Missing return series for: {', '.join(missing)}"
        raise ValueError(msg)
    cols = [_as_array(returns[a], name=f"returns[{a}]", min_len=2) for a in assets]
    if len({c.size for c in cols}) != 1:
        msg = "All return series must have the same length"
        raise ValueError(msg)
    return np.column_stack(cols)


def correlation_matrix(
    returns: Mapping[str, Sequence[float]],
) -> dict[str, dict[str, float]]:
    """Compute the pairwise correlation matrix of aligned return series.

    Args:
        returns: Mapping of asset to periodic returns.

    Returns:
        Nested dict `corr[a][b]`.
    """
    assets = list(returns)
    mat = _returns_matrix(returns, assets)
    if len(assets) == 1:
        return {assets[0]: {assets[0]: 1.0}}
    corr = np.corrcoef(mat, rowvar=False)
    return {
        a: {b: float(corr[i, j]) for j, b in enumerate(assets)}
        for i, a in enumerate(assets)
    }


def portfolio_risk(
    weights: Mapping[str, float],
    returns: Mapping[str, Sequence[float]],
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, object]:
    """Compute portfolio volatility and each position's contribution to risk.

    Contribution to risk is `w_i * (Sigma w)_i / sigma_p`, expressed as a
    share of total portfolio volatility (shares sum to 1).

    Args:
        weights: Asset weights (normalized internally).
        returns: Aligned periodic returns per asset.
        periods_per_year: Observations per year.

    Returns:
        Dict with `annualized_volatility`, `risk_contribution` (share per
        asset), and `weighted_avg_volatility` / `diversification_ratio`.
    """
    w_map = normalize_weights(weights)
    assets = list(w_map)
    mat = _returns_matrix(returns, assets)
    w = np.asarray([w_map[a] for a in assets])
    cov = np.atleast_2d(np.cov(mat, rowvar=False, ddof=1)) * periods_per_year
    port_var = float(w @ cov @ w)
    port_vol = math.sqrt(port_var)
    marginal = cov @ w
    contrib = (w * marginal / port_var) if port_var > 0 else np.zeros_like(w)
    asset_vols = np.sqrt(np.diag(cov))
    weighted_avg = float(w @ asset_vols)
    return {
        "annualized_volatility": port_vol,
        "risk_contribution": {
            a: float(c) for a, c in zip(assets, contrib, strict=True)
        },
        "weighted_avg_volatility": weighted_avg,
        "diversification_ratio": weighted_avg / port_vol if port_vol > 0 else None,
    }
