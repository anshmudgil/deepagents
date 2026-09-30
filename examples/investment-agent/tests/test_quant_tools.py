"""Tests for tools wrapping analytics (risk, technicals, DCF, portfolio)."""

from __future__ import annotations

import json

import pytest

from investment_agent import analytics
from investment_agent.tools import quant
from tests.conftest import make_closes


def _call(tool: object, **kwargs: object) -> dict:
    result = tool.invoke(kwargs)  # type: ignore[attr-defined]
    json.dumps(result)
    return result


def test_compute_risk_metrics(market: dict) -> None:
    r = _call(quant.compute_risk_metrics, ticker="ACME")
    m = r["metrics"]
    assert r["benchmark"] == "SPY"
    for key in ("annualized_volatility", "beta_vs_benchmark", "sharpe_ratio", "sortino_ratio", "max_drawdown", "var_95_1d", "cvar_95_1d"):
        assert m[key] is not None
    assert m["cvar_95_1d"] >= m["var_95_1d"] > 0
    assert 0 <= m["max_drawdown"] < 1


def test_compute_risk_metrics_beta_of_benchmark_is_one(market: dict) -> None:
    market["SPY2"] = {"closes": market["SPY"]["closes"]}
    r = _call(quant.compute_risk_metrics, ticker="SPY2")
    assert r["metrics"]["beta_vs_benchmark"] == pytest.approx(1.0)


def test_compute_risk_metrics_short_history(market: dict) -> None:
    market["NEW"] = {"closes": make_closes("NEW", n=10)}
    assert "overlapping observations" in _call(quant.compute_risk_metrics, ticker="NEW")["error"]


def test_technical_indicators(market: dict) -> None:
    t = _call(quant.technical_indicators, ticker="ACME")
    assert t["ticker"] == "ACME"
    assert t["trend"] in {"uptrend", "downtrend", "mixed"}
    assert 0 <= t["rsi_14"] <= 100
    assert 0 <= t["range_position_52w"] <= 1


def test_run_dcf_with_fetched_inputs(market: dict) -> None:
    d = _call(quant.run_dcf, ticker="ACME", growth_rate=0.05, discount_rate=0.09, terminal_growth=0.025)
    expected = analytics.dcf_valuation(2e9, 0.05, 0.09, 0.025, 5, 2e9, 1e9)["value_per_share"]
    assert d["valuation"]["value_per_share"] == pytest.approx(expected, rel=1e-5)
    assert d["upside_vs_price"] == pytest.approx(expected / 50.0 - 1, rel=1e-5)
    grid = d["sensitivity"]
    assert grid["rows"] == [0.07, 0.08, 0.09, 0.1, 0.11]
    assert grid["columns"] == [0.015, 0.02, 0.025, 0.03, 0.035]
    assert grid["values"][2][2] == pytest.approx(expected, rel=1e-5)


def test_run_dcf_with_overrides_needs_no_data(market: dict) -> None:
    d = _call(quant.run_dcf, ticker="NODATA", base_fcf=100.0, net_debt=0.0, shares_outstanding=10.0, growth_rate=0.0, discount_rate=0.1, terminal_growth=0.0, years=1)
    assert d["valuation"]["value_per_share"] == pytest.approx(100.0)
    assert d["upside_vs_price"] is None


def test_run_dcf_negative_fcf(market: dict) -> None:
    market["ACME"]["info"]["freeCashflow"] = -5e8
    assert "No positive free cash flow" in _call(quant.run_dcf, ticker="ACME")["error"]


def test_run_dcf_invalid_rates(market: dict) -> None:
    assert "must exceed" in _call(quant.run_dcf, ticker="ACME", discount_rate=0.02, terminal_growth=0.03)["error"]


def test_analyze_portfolio(market: dict) -> None:
    p = _call(quant.analyze_portfolio, holdings={"acme": 60, "peer": 30, "spy": 10})
    assert p["weights"] == {"ACME": 0.6, "PEER": 0.3, "SPY": 0.1}
    assert sum(p["risk_contribution"].values()) == pytest.approx(1.0, abs=1e-5)
    assert p["concentration"]["hhi"] == pytest.approx(0.46)
    assert p["correlation"]["ACME"]["ACME"] == pytest.approx(1.0)
    assert p["annualized_volatility"] > 0 and p["cvar_95_1d"] >= p["var_95_1d"]


def test_analyze_portfolio_rejects_shorts(market: dict) -> None:
    assert "Negative weights" in _call(quant.analyze_portfolio, holdings={"ACME": 1, "PEER": -1})["error"]


def test_analyze_portfolio_limits(market: dict) -> None:
    assert "between 1 and 25" in _call(quant.analyze_portfolio, holdings={})["error"]


def test_analyze_portfolio_unknown_ticker(market: dict) -> None:
    assert "No price history" in _call(quant.analyze_portfolio, holdings={"ACME": 1, "ZZZZ": 1})["error"]
