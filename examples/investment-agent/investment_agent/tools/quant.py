"""Quantitative tools that wrap `investment_agent.analytics` with market data.

Each tool fetches prices or fundamentals through `market_data`, runs the pure
math in `analytics`, and returns a compact dict with its assumptions stated.
"""

from __future__ import annotations

import pandas as pd
from langchain_core.tools import tool

from investment_agent import analytics
from investment_agent.tools import market_data
from investment_agent.tools._common import (
    JSONValue,
    normalize_ticker,
    safe_tool,
    to_jsonable,
)

BENCHMARK = "SPY"
MIN_OBSERVATIONS = 30


def _aligned_returns(symbols: list[str], period: str) -> pd.DataFrame:
    """Fetch closes for several symbols and return aligned daily returns.

    Args:
        symbols: Ticker symbols.
        period: yfinance period string.

    Returns:
        DataFrame of simple returns (one column per symbol, common dates only).

    Raises:
        ValueError: If fewer than `MIN_OBSERVATIONS` common observations exist.
    """
    closes = pd.concat({s: market_data.fetch_closes(s, period=period) for s in symbols}, axis=1).dropna()
    returns = closes.pct_change().dropna()
    if len(returns) < MIN_OBSERVATIONS:
        msg = f"Only {len(returns)} overlapping observations for {symbols}; need {MIN_OBSERVATIONS}"
        raise ValueError(msg)
    return returns


def _risk_block(asset: list[float], bench: list[float], closes: list[float], rf: float) -> dict[str, JSONValue]:
    """Compute the standard risk metric set for one asset.

    Args:
        asset: Asset daily returns.
        bench: Benchmark daily returns (aligned).
        closes: Asset closes used for drawdown.
        rf: Annual risk-free rate.

    Returns:
        Risk metrics dict.
    """
    dd = analytics.max_drawdown(closes)
    return to_jsonable(
        {
            "annualized_volatility": analytics.annualized_volatility(asset),
            "beta_vs_benchmark": analytics.beta(asset, bench),
            "sharpe_ratio": analytics.sharpe_ratio(asset, rf),
            "sortino_ratio": analytics.sortino_ratio(asset, rf),
            "max_drawdown": dd["max_drawdown"],
            "current_drawdown": dd["current_drawdown"],
            "var_95_1d": analytics.historical_var(asset, 0.95),
            "cvar_95_1d": analytics.historical_cvar(asset, 0.95),
        }
    )  # type: ignore[return-value]


@tool(parse_docstring=True)
@safe_tool
def compute_risk_metrics(
    ticker: str,
    period: str = "2y",
    benchmark: str = BENCHMARK,
    risk_free_rate: float = 0.04,
) -> dict[str, JSONValue]:
    """Compute volatility, beta, Sharpe, Sortino, max drawdown and historical VaR/CVaR.

    Args:
        ticker: Stock ticker symbol.
        period: Lookback window, e.g. "1y", "2y", "5y".
        benchmark: Benchmark ticker for beta.
        risk_free_rate: Annual risk-free rate as a fraction (0.04 = 4%).

    Returns:
        Risk metrics (fractions; VaR/CVaR are 1-day 95% historical losses) and
        the benchmark's own volatility/drawdown for context.
    """
    symbol, bench = normalize_ticker(ticker), normalize_ticker(benchmark)
    rets = _aligned_returns([symbol, bench], period)
    closes = (1 + rets).cumprod()
    metrics = _risk_block(rets[symbol].tolist(), rets[bench].tolist(), closes[symbol].tolist(), risk_free_rate)
    return {
        "ticker": symbol,
        "benchmark": bench,
        "period": period,
        "observations": len(rets),
        "metrics": metrics,  # type: ignore[dict-item]
        "benchmark_metrics": to_jsonable(
            {
                "annualized_volatility": analytics.annualized_volatility(rets[bench].tolist()),
                "max_drawdown": analytics.max_drawdown(closes[bench].tolist())["max_drawdown"],
            }
        ),
        "assumptions": f"Daily simple returns, 252 periods/yr, rf={risk_free_rate:.2%}, historical (non-parametric) VaR",
    }


@tool(parse_docstring=True)
@safe_tool
def technical_indicators(ticker: str) -> dict[str, JSONValue]:
    """Compute SMA 50/200, RSI(14), MACD(12,26,9) and 52-week range position.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Latest indicator values and a coarse trend label (uptrend/downtrend/mixed).
    """
    symbol = normalize_ticker(ticker)
    closes = market_data.fetch_closes(symbol, period="2y")
    snapshot = analytics.technical_snapshot(closes.tolist())
    return {"ticker": symbol, "as_of": to_jsonable(closes.index[-1]), **to_jsonable(snapshot)}  # type: ignore[dict-item]


def _dcf_inputs(symbol: str) -> dict[str, float | None]:
    """Pull base FCF, net debt and share count for a DCF from yfinance.

    Args:
        symbol: Ticker symbol.

    Returns:
        Dict with `base_fcf`, `net_debt`, `shares`, `price` (values may be None).
    """
    info = market_data.get_info(market_data.get_ticker(symbol))

    def num(key: str) -> float | None:
        value = info.get(key)
        return float(value) if isinstance(value, int | float) else None

    debt, cash = num("totalDebt") or 0.0, num("totalCash") or 0.0
    return {
        "base_fcf": num("freeCashflow"),
        "net_debt": debt - cash,
        "shares": num("sharesOutstanding"),
        "price": num("currentPrice") or num("regularMarketPrice"),
    }


def _resolve_dcf_inputs(
    symbol: str,
    base_fcf: float | None,
    net_debt: float | None,
    shares_outstanding: float | None,
) -> dict[str, float | None]:
    """Merge caller overrides with fetched DCF inputs.

    Args:
        symbol: Ticker symbol.
        base_fcf: Override for base free cash flow.
        net_debt: Override for net debt.
        shares_outstanding: Override for share count.

    Returns:
        Resolved inputs.

    Raises:
        ValueError: If no positive base FCF is available.
    """
    fetched = _dcf_inputs(symbol) if None in (base_fcf, net_debt, shares_outstanding) else {"price": None}
    resolved = {
        "base_fcf": base_fcf if base_fcf is not None else fetched.get("base_fcf"),
        "net_debt": net_debt if net_debt is not None else fetched.get("net_debt") or 0.0,
        "shares": shares_outstanding if shares_outstanding is not None else fetched.get("shares"),
        "price": fetched.get("price"),
    }
    if not resolved["base_fcf"] or resolved["base_fcf"] <= 0:
        msg = f"No positive free cash flow for {symbol}; a DCF is not meaningful - pass `base_fcf` or use multiples"
        raise ValueError(msg)
    return resolved


@tool(parse_docstring=True)
@safe_tool
def run_dcf(
    ticker: str,
    growth_rate: float = 0.08,
    discount_rate: float = 0.09,
    terminal_growth: float = 0.025,
    years: int = 5,
    base_fcf: float | None = None,
    net_debt: float | None = None,
    shares_outstanding: float | None = None,
) -> dict[str, JSONValue]:
    """Run a two-stage DCF with a WACC x terminal-growth sensitivity table.

    Base FCF, net debt and shares default to the latest Yahoo Finance values;
    override any of them to test your own assumptions. Run it for bull, base
    and bear growth assumptions rather than trusting a single point estimate.

    Args:
        ticker: Stock ticker symbol.
        growth_rate: Annual FCF growth for the explicit period (0.08 = 8%).
        discount_rate: WACC (0.09 = 9%).
        terminal_growth: Perpetual growth after the explicit period; must be below WACC.
        years: Explicit forecast years (1-15).
        base_fcf: Optional override of trailing free cash flow (currency units, not millions).
        net_debt: Optional override of debt minus cash (currency units).
        shares_outstanding: Optional override of diluted share count.

    Returns:
        Valuation (EV, equity value, value per share, upside vs price,
        terminal value share), stated assumptions, and a 5x5 sensitivity grid.
    """
    symbol = normalize_ticker(ticker)
    inputs = _resolve_dcf_inputs(symbol, base_fcf, net_debt, shares_outstanding)
    years = max(1, min(years, 15))
    args = (inputs["base_fcf"], growth_rate)
    kwargs = {"years": years, "net_debt": inputs["net_debt"], "shares_outstanding": inputs["shares"]}
    result = analytics.dcf_valuation(*args, discount_rate, terminal_growth, **kwargs)  # type: ignore[arg-type]
    grid = analytics.dcf_sensitivity(
        *args,  # type: ignore[arg-type]
        discount_rates=analytics.symmetric_grid(discount_rate, 0.01),
        terminal_growths=analytics.symmetric_grid(terminal_growth, 0.005),
        **kwargs,  # type: ignore[arg-type]
    )
    per_share, price = result["value_per_share"], inputs["price"]
    upside = per_share / price - 1.0 if isinstance(per_share, float) and price else None
    return to_jsonable(
        {
            "ticker": symbol,
            "assumptions": {**inputs, "growth_rate": growth_rate, "discount_rate": discount_rate, "terminal_growth": terminal_growth, "years": years},
            "valuation": {k: v for k, v in result.items() if k != "projected_fcf"},
            "projected_fcf": result["projected_fcf"],
            "current_price": price,
            "upside_vs_price": upside,
            "sensitivity": grid,
            "caveat": "Model output is only as good as its assumptions; terminal value share above ~75% means the answer is mostly the terminal assumption.",
        }
    )  # type: ignore[return-value]


def _portfolio_payload(weights: dict[str, float], rets: pd.DataFrame) -> dict[str, JSONValue]:
    """Assemble portfolio analytics from normalized weights and returns.

    Args:
        weights: Normalized weights.
        rets: Aligned daily returns including every weighted asset.

    Returns:
        Analytics payload.
    """
    series = {s: rets[s].tolist() for s in weights}
    risk = analytics.portfolio_risk(weights, series)
    port_rets = (rets[list(weights)] * pd.Series(weights)).sum(axis=1).tolist()
    return to_jsonable(
        {
            "weights": weights,
            "concentration": analytics.herfindahl_index(weights),
            "correlation": analytics.correlation_matrix(series),
            **risk,
            "sharpe_ratio": analytics.sharpe_ratio(port_rets, 0.04),
            "max_drawdown": analytics.max_drawdown((1 + pd.Series(port_rets)).cumprod().tolist())["max_drawdown"],
            "var_95_1d": analytics.historical_var(port_rets),
            "cvar_95_1d": analytics.historical_cvar(port_rets),
        }
    )  # type: ignore[return-value]


@tool(parse_docstring=True)
@safe_tool
def analyze_portfolio(holdings: dict[str, float], period: str = "1y") -> dict[str, JSONValue]:
    """Analyze a long-only portfolio: weights, correlation, concentration (HHI), volatility and risk contribution.

    Args:
        holdings: Mapping of ticker to position size (dollar value or percent; normalized to weights), e.g. {"AAPL": 40, "MSFT": 35, "TLT": 25}.
        period: Lookback window for returns, e.g. "1y" or "3y".

    Returns:
        Normalized weights, HHI/effective positions, correlation matrix,
        annualized volatility, each position's share of total risk, Sharpe,
        max drawdown, and 1-day 95% historical VaR/CVaR.
    """
    if not 1 <= len(holdings) <= 25:
        return {"error": "Provide between 1 and 25 holdings"}
    weights = analytics.normalize_weights({normalize_ticker(k): v for k, v in holdings.items()})
    rets = _aligned_returns(list(weights), period)
    return {"period": period, "observations": len(rets), **_portfolio_payload(weights, rets)}  # type: ignore[dict-item]


QUANT_TOOLS = [compute_risk_metrics, technical_indicators, run_dcf, analyze_portfolio]
