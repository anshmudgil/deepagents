"""Tests for yfinance-backed tools (all data faked via the `market` fixture)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from investment_agent.tools import market_data as md
from investment_agent.tools._common import normalize_ticker, safe_tool, to_jsonable


def _call(tool: object, **kwargs: object) -> dict:
    result = tool.invoke(kwargs)  # type: ignore[attr-defined]
    json.dumps(result)  # every tool output must be JSON-serializable
    return result


def test_get_quote(market: dict) -> None:
    q = _call(md.get_quote, ticker=" acme ")
    assert q["ticker"] == "ACME"
    assert q["name"] == "Acme Corp"
    closes = market["ACME"]["closes"]
    assert q["price"] == pytest.approx(closes.iloc[-1], rel=1e-6)
    assert q["change"] == pytest.approx(closes.iloc[-1] - closes.iloc[-2], abs=1e-5)
    assert q["sector"] == "Technology"


def test_get_quote_unknown_ticker(market: dict) -> None:
    assert "No price history" in _call(md.get_quote, ticker="ZZZZ")["error"]


def test_get_quote_invalid_symbol(market: dict) -> None:
    assert "Invalid ticker" in _call(md.get_quote, ticker="AAPL; rm -rf /")["error"]


def test_get_price_history_summarizes(market: dict) -> None:
    h = _call(md.get_price_history, ticker="ACME", period="2y")
    assert h["observations"] == len(market["ACME"]["closes"])
    assert len(h["sampled_closes"]) == 20
    for key in (
        "total_return",
        "cagr",
        "annualized_volatility",
        "max_drawdown",
        "high",
        "low",
    ):
        assert key in h
    assert h["low"] <= h["end"] <= h["high"]


def test_get_financial_statements_trims(market: dict) -> None:
    s = _call(
        md.get_financial_statements, ticker="ACME", statement="income", max_periods=2
    )
    items = s["line_items"]
    assert set(items) == {"Total Revenue", "Net Income", "Diluted EPS"}
    assert list(items["Total Revenue"]) == ["2025-12-31", "2024-12-31"]
    assert items["Total Revenue"]["2025-12-31"] == pytest.approx(1000.0)  # millions
    assert items["Diluted EPS"]["2025-12-31"] == pytest.approx(
        2.5
    )  # per-share rows unscaled


def test_get_financial_statements_quarterly(market: dict) -> None:
    s = _call(md.get_financial_statements, ticker="ACME", frequency="quarterly")
    assert s["frequency"] == "quarterly" and len(s["line_items"]["Net Income"]) == 4


def test_get_financial_statements_missing(market: dict) -> None:
    market["ACME"]["cashflow"] = pd.DataFrame()
    assert "error" in _call(
        md.get_financial_statements, ticker="ACME", statement="cashflow"
    )


def test_get_key_metrics(market: dict) -> None:
    m = _call(md.get_key_metrics, ticker="ACME")
    assert m["valuation"]["trailingPE"] == 25.0
    assert m["valuation"]["enterpriseToEbitda"] is None  # NaN -> null
    assert m["derived"]["fcf_yield"] == pytest.approx(0.04)
    assert m["derived"]["net_debt"] == pytest.approx(2e9)


def test_get_key_metrics_when_info_fails(market: dict) -> None:
    market["ACME"]["info"] = RuntimeError("yahoo down")
    assert "No fundamental data" in _call(md.get_key_metrics, ticker="ACME")["error"]


def test_get_analyst_estimates_tolerates_partial_failures(market: dict) -> None:
    market["ACME"].update(
        {
            "analyst_price_targets": {"mean": 60.0, "low": 45.0, "high": 75.0},
            "recommendations_summary": pd.DataFrame(
                {"period": ["0m"], "buy": [10], "hold": [5], "sell": [1]}
            ),
            "earnings_estimate": RuntimeError("boom"),
            "revenue_estimate": None,
            "upgrades_downgrades": pd.DataFrame(
                {"Firm": ["X"], "ToGrade": ["Buy"]},
                index=pd.to_datetime(["2026-01-05"]).rename("GradeDate"),
            ),
        }
    )
    e = _call(md.get_analyst_estimates, ticker="ACME")
    assert e["price_targets"]["mean"] == 60.0
    assert e["recommendations"][0]["buy"] == 10
    assert e["earnings_estimate"] == [] and e["revenue_estimate"] == []
    assert e["recent_rating_changes"][0]["GradeDate"] == "2026-01-05"


def test_compare_peers(market: dict) -> None:
    c = _call(md.compare_peers, tickers=["acme", "peer", "nodata"])
    assert c["target"] == "ACME"
    assert c["peers"]["NODATA"] == {"error": "no data"}
    assert c["median"]["trailingPE"] == pytest.approx(20.0)


def test_compare_peers_requires_two(market: dict) -> None:
    assert "between 2 and 10" in _call(md.compare_peers, tickers=["ACME"])["error"]


def test_safe_tool_converts_exceptions() -> None:
    @safe_tool
    def broken() -> dict:
        raise KeyError("x")

    assert broken() == {"error": "broken failed: KeyError: 'x'"}


@pytest.mark.parametrize("symbol", ["BRK-B", "BRK.B", "^GSPC", "7203.T"])
def test_normalize_ticker_accepts_real_symbols(symbol: str) -> None:
    assert normalize_ticker(symbol.lower()) == symbol.upper()


def test_to_jsonable_handles_numpy_and_dates() -> None:
    import numpy as np

    out = to_jsonable(
        {
            "a": np.float64(1.5),
            "b": pd.Timestamp("2026-01-02"),
            "c": [np.int64(3), float("inf")],
        }
    )
    assert out == {"a": 1.5, "b": "2026-01-02", "c": [3, None]}


def test_get_key_metrics_normalizes_percent_fields(market: dict) -> None:
    market["ACME"]["info"].update(debtToEquity=150.0, dividendYield=0.41)
    m = _call(md.get_key_metrics, ticker="ACME")
    assert m["balance_sheet"]["debtToEquity"] == pytest.approx(1.5)
    assert m["shareholder"]["dividendYield"] == pytest.approx(0.0041)
    assert "debtToEquity is a multiple" in m["units"]


def test_get_key_metrics_currency_mismatch_skips_fcf_yield(market: dict) -> None:
    market["ACME"]["info"]["financialCurrency"] = "CNY"
    m = _call(md.get_key_metrics, ticker="ACME")
    assert m["currency"] == "USD" and m["financial_currency"] == "CNY"
    assert "fcf_yield" not in m["derived"]
    assert "CNY" in m["derived"]["fcf_yield_note"]


def test_get_key_metrics_financial_currency_defaults_to_trading(market: dict) -> None:
    m = _call(md.get_key_metrics, ticker="ACME")
    assert m["financial_currency"] == m["currency"] == "USD"
