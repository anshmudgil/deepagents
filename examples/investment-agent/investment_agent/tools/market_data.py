"""Market data tools backed by yfinance (free, no API key).

All tools return compact JSON-serializable dicts. Monetary statement values
are reported in millions of the reporting currency to save context.
Failures come back as `{"error": "..."}` rather than raising.
"""

from __future__ import annotations

from statistics import median
from typing import Literal

import pandas as pd
import yfinance as yf
from langchain_core.tools import tool

from investment_agent import analytics
from investment_agent.tools._common import (
    JSONValue,
    frame_to_records,
    normalize_ticker,
    safe_tool,
    statement_to_dict,
    to_jsonable,
)

Period = Literal["1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "max"]

INCOME_ROWS = [
    "Total Revenue",
    "Cost Of Revenue",
    "Gross Profit",
    "Research And Development",
    "Selling General And Administration",
    "Operating Income",
    "EBITDA",
    "Interest Expense",
    "Pretax Income",
    "Tax Provision",
    "Net Income",
    "Diluted EPS",
    "Diluted Average Shares",
]
BALANCE_ROWS = [
    "Cash And Cash Equivalents",
    "Cash Cash Equivalents And Short Term Investments",
    "Current Assets",
    "Total Assets",
    "Current Liabilities",
    "Total Debt",
    "Net Debt",
    "Total Liabilities Net Minority Interest",
    "Stockholders Equity",
    "Working Capital",
    "Invested Capital",
    "Ordinary Shares Number",
]
CASHFLOW_ROWS = [
    "Operating Cash Flow",
    "Capital Expenditure",
    "Free Cash Flow",
    "Stock Based Compensation",
    "Depreciation And Amortization",
    "Change In Working Capital",
    "Repurchase Of Capital Stock",
    "Cash Dividends Paid",
    "Acquisitions Net",
]

METRIC_KEYS = {
    "valuation": [
        "marketCap",
        "enterpriseValue",
        "trailingPE",
        "forwardPE",
        "trailingPegRatio",
        "priceToBook",
        "priceToSalesTrailing12Months",
        "enterpriseToRevenue",
        "enterpriseToEbitda",
    ],
    "profitability": [
        "grossMargins",
        "operatingMargins",
        "ebitdaMargins",
        "profitMargins",
        "returnOnEquity",
        "returnOnAssets",
    ],
    "growth": ["revenueGrowth", "earningsGrowth", "earningsQuarterlyGrowth"],
    "balance_sheet": ["totalCash", "totalDebt", "debtToEquity", "currentRatio", "quickRatio"],
    "cash_flow": ["freeCashflow", "operatingCashflow"],
    "shareholder": ["dividendYield", "payoutRatio", "sharesOutstanding", "heldPercentInsiders", "shortPercentOfFloat"],
    "risk": ["beta"],
}

PEER_KEYS = [
    "marketCap",
    "trailingPE",
    "forwardPE",
    "enterpriseToEbitda",
    "enterpriseToRevenue",
    "priceToSalesTrailing12Months",
    "grossMargins",
    "operatingMargins",
    "revenueGrowth",
    "returnOnEquity",
]


# ---------------------------------------------------------------------------
# Data access helpers (monkeypatched in tests)
# ---------------------------------------------------------------------------


def get_ticker(symbol: str) -> yf.Ticker:
    """Create a yfinance `Ticker` for a validated symbol.

    Args:
        symbol: Ticker symbol.

    Returns:
        A lazily-loading yfinance `Ticker` (no network until attributes are read).
    """
    return yf.Ticker(normalize_ticker(symbol))


def get_info(ticker: yf.Ticker) -> dict[str, object]:
    """Read `Ticker.info`, returning an empty dict when Yahoo fails.

    Args:
        ticker: yfinance ticker.

    Returns:
        The info mapping (possibly empty).
    """
    try:
        info = ticker.info
    except Exception:  # noqa: BLE001  # yfinance raises assorted errors for delisted/unknown symbols
        return {}
    return dict(info or {})


def fetch_closes(symbol: str, period: str = "1y", interval: str = "1d") -> pd.Series:
    """Fetch adjusted closing prices for a symbol.

    Args:
        symbol: Ticker symbol.
        period: yfinance period string.
        interval: Bar interval.

    Returns:
        Close prices indexed by date, NaNs dropped.

    Raises:
        ValueError: If no price data is returned (unknown or delisted ticker).
    """
    hist = get_ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
    if hist is None or hist.empty or "Close" not in hist:
        msg = f"No price history for {symbol!r} (unknown or delisted ticker?)"
        raise ValueError(msg)
    closes = hist["Close"].dropna()
    closes.index = pd.to_datetime(closes.index).tz_localize(None) if closes.index.tz else closes.index
    return closes


def _pick(info: dict[str, object], keys: list[str]) -> dict[str, JSONValue]:
    """Select keys from an info mapping, dropping missing values.

    Args:
        info: yfinance info mapping.
        keys: Keys to keep.

    Returns:
        JSON-safe subset.
    """
    return {k: to_jsonable(info[k]) for k in keys if info.get(k) is not None}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool(parse_docstring=True)
@safe_tool
def get_quote(ticker: str) -> dict[str, JSONValue]:
    """Get the latest price snapshot and company profile for a ticker.

    Args:
        ticker: Stock ticker symbol, e.g. "AAPL".

    Returns:
        Price, daily change, market cap, 52-week range, sector and industry.
    """
    symbol = normalize_ticker(ticker)
    closes = fetch_closes(symbol, period="5d")
    info = get_info(get_ticker(symbol))
    price = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else price
    return to_jsonable(
        {
            "ticker": symbol,
            "name": info.get("longName") or info.get("shortName") or symbol,
            "price": price,
            "change": price - prev,
            "change_pct": (price / prev - 1.0) if prev else None,
            "currency": info.get("currency", "USD"),
            "as_of": closes.index[-1],
            "market_cap": info.get("marketCap"),
            "high_52w": info.get("fiftyTwoWeekHigh"),
            "low_52w": info.get("fiftyTwoWeekLow"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "source": "Yahoo Finance via yfinance",
        }
    )  # type: ignore[return-value]


@tool(parse_docstring=True)
@safe_tool
def get_price_history(ticker: str, period: Period = "1y") -> dict[str, JSONValue]:
    """Summarize price performance over a period (stats, not a raw dump).

    Args:
        ticker: Stock ticker symbol.
        period: Lookback window: 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y or max.

    Returns:
        Start/end/high/low, total return, CAGR, annualized volatility, max
        drawdown, and ~20 evenly spaced closes for shape.
    """
    symbol = normalize_ticker(ticker)
    closes = fetch_closes(symbol, period=period)
    summary = analytics.price_summary(closes.tolist())
    return to_jsonable(
        {
            "ticker": symbol,
            "period": period,
            "start_date": closes.index[0],
            "end_date": closes.index[-1],
            "observations": len(closes),
            **summary,
            "sampled_closes": analytics.downsample(closes.tolist(), 20),
            "source": "Yahoo Finance via yfinance (adjusted closes)",
        }
    )  # type: ignore[return-value]


@tool(parse_docstring=True)
@safe_tool
def get_financial_statements(
    ticker: str,
    statement: Literal["income", "balance", "cashflow"] = "income",
    frequency: Literal["annual", "quarterly"] = "annual",
    max_periods: int = 4,
) -> dict[str, JSONValue]:
    """Get key line items from a company's financial statements.

    Args:
        ticker: Stock ticker symbol.
        statement: "income", "balance", or "cashflow".
        frequency: "annual" or "quarterly".
        max_periods: Number of most recent periods (1-8).

    Returns:
        Line items by period-end date, values in millions of reporting currency.
    """
    symbol = normalize_ticker(ticker)
    t = get_ticker(symbol)
    attr, rows = {
        "income": ("income_stmt", INCOME_ROWS),
        "balance": ("balance_sheet", BALANCE_ROWS),
        "cashflow": ("cashflow", CASHFLOW_ROWS),
    }[statement]
    df = getattr(t, f"quarterly_{attr}" if frequency == "quarterly" else attr)
    data = statement_to_dict(df, rows, max(1, min(max_periods, 8)))
    if not data:
        return {"error": f"No {frequency} {statement} statement available for {symbol}"}
    return {
        "ticker": symbol,
        "statement": statement,
        "frequency": frequency,
        "units": "millions of reporting currency; EPS rows in currency per share; share counts in millions",
        "line_items": data,
        "source": "Company filings via Yahoo Finance",
    }


def _derived_metrics(info: dict[str, object]) -> dict[str, JSONValue]:
    """Compute metrics yfinance does not provide directly.

    Args:
        info: yfinance info mapping.

    Returns:
        FCF yield and net debt when inputs exist.
    """
    out: dict[str, JSONValue] = {}
    fcf, mcap = info.get("freeCashflow"), info.get("marketCap")
    if isinstance(fcf, int | float) and isinstance(mcap, int | float) and mcap:
        out["fcf_yield"] = to_jsonable(fcf / mcap)
    debt, cash = info.get("totalDebt"), info.get("totalCash")
    if isinstance(debt, int | float) and isinstance(cash, int | float):
        out["net_debt"] = to_jsonable(debt - cash)
    return out


@tool(parse_docstring=True)
@safe_tool
def get_key_metrics(ticker: str) -> dict[str, JSONValue]:
    """Get valuation ratios, margins, growth, balance-sheet and risk metrics.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Metrics grouped by category (ratios as fractions, e.g. 0.25 = 25%).
    """
    symbol = normalize_ticker(ticker)
    info = get_info(get_ticker(symbol))
    if not info:
        return {"error": f"No fundamental data for {symbol}"}
    groups = {name: _pick(info, keys) for name, keys in METRIC_KEYS.items()}
    groups["derived"] = _derived_metrics(info)
    return {"ticker": symbol, "name": to_jsonable(info.get("longName")), **groups, "source": "Yahoo Finance via yfinance"}


def _safe_attr(t: yf.Ticker, name: str) -> object:
    """Read a yfinance attribute, returning `None` when it errors.

    Args:
        t: yfinance ticker.
        name: Attribute name.

    Returns:
        The attribute value or `None`.
    """
    try:
        return getattr(t, name)
    except Exception:  # noqa: BLE001  # optional datasets fail independently; keep the rest
        return None


@tool(parse_docstring=True)
@safe_tool
def get_analyst_estimates(ticker: str) -> dict[str, JSONValue]:
    """Get consensus price targets, ratings, EPS/revenue estimates and recent rating changes.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        Price targets, recommendation counts, forward estimates and the latest
        upgrades/downgrades (sell-side consensus - treat as a sentiment input,
        not a primary source).
    """
    symbol = normalize_ticker(ticker)
    t = get_ticker(symbol)
    upgrades = _safe_attr(t, "upgrades_downgrades")
    return to_jsonable(
        {
            "ticker": symbol,
            "price_targets": _safe_attr(t, "analyst_price_targets") or {},
            "recommendations": frame_to_records(_safe_attr(t, "recommendations_summary"), 4),  # type: ignore[arg-type]
            "earnings_estimate": frame_to_records(_safe_attr(t, "earnings_estimate"), 4),  # type: ignore[arg-type]
            "revenue_estimate": frame_to_records(_safe_attr(t, "revenue_estimate"), 4),  # type: ignore[arg-type]
            "recent_rating_changes": frame_to_records(upgrades, 8),  # type: ignore[arg-type]
            "source": "Sell-side consensus via Yahoo Finance",
        }
    )  # type: ignore[return-value]


def _peer_row(symbol: str) -> dict[str, JSONValue]:
    """Fetch the comparison metrics for one peer.

    Args:
        symbol: Ticker symbol.

    Returns:
        Metrics dict, or `{"error": ...}` if the peer has no data.
    """
    info = get_info(get_ticker(symbol))
    if not info:
        return {"error": "no data"}
    return {"name": to_jsonable(info.get("shortName") or symbol), **_pick(info, PEER_KEYS)}


def _peer_medians(rows: dict[str, dict[str, JSONValue]]) -> dict[str, JSONValue]:
    """Compute the median of each numeric metric across peers.

    Args:
        rows: Peer metrics keyed by ticker.

    Returns:
        Median per metric (metrics with no data are omitted).
    """
    medians: dict[str, JSONValue] = {}
    for key in PEER_KEYS:
        values = [r[key] for r in rows.values() if isinstance(r.get(key), int | float)]
        if values:
            medians[key] = to_jsonable(median(values))  # type: ignore[type-var]
    return medians


@tool(parse_docstring=True)
@safe_tool
def compare_peers(tickers: list[str]) -> dict[str, JSONValue]:
    """Compare valuation, margins and growth across a set of peer companies.

    Args:
        tickers: 2-10 ticker symbols; put the target company first.

    Returns:
        Metrics per ticker plus the peer median for each metric.
    """
    if not 2 <= len(tickers) <= 10:
        return {"error": "Provide between 2 and 10 tickers"}
    symbols = [normalize_ticker(t) for t in tickers]
    rows = {s: _peer_row(s) for s in symbols}
    return {
        "target": symbols[0],
        "peers": rows,  # type: ignore[dict-item]
        "median": _peer_medians(rows),
        "source": "Yahoo Finance via yfinance",
    }


MARKET_DATA_TOOLS = [get_quote, get_price_history, get_financial_statements, get_key_metrics, get_analyst_estimates, compare_peers]
