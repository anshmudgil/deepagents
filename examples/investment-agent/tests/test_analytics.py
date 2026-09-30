"""Unit tests for the pure finance math in `investment_agent.analytics`."""

from __future__ import annotations

import math

import numpy as np
import pytest

from investment_agent import analytics as a

# --- returns & summary stats -------------------------------------------------


def test_simple_returns() -> None:
    assert a.simple_returns([100, 110, 99]) == pytest.approx([0.1, -0.1])


@pytest.mark.parametrize("bad", [[100], [100, 0, 5], [100, float("nan")], [100, -1]])
def test_simple_returns_rejects_bad_prices(bad: list[float]) -> None:
    with pytest.raises(ValueError):
        a.simple_returns(bad)


def test_annualized_return() -> None:
    assert a.annualized_return([100, 121], periods_per_year=1) == pytest.approx(0.21)
    # Two annual periods, +21% in total = 10% CAGR
    assert a.annualized_return([100, 110, 121], periods_per_year=1) == pytest.approx(0.10)
    # The same two periods squeezed into one year = 21% CAGR
    assert a.annualized_return([100, 110, 121], periods_per_year=2) == pytest.approx(0.21)


def test_annualized_volatility() -> None:
    rets = [0.01, -0.01, 0.01, -0.01]
    expected = math.sqrt(4 * 0.0001 / 3) * math.sqrt(252)
    assert a.annualized_volatility(rets) == pytest.approx(expected)
    assert a.annualized_volatility([0.02, 0.02, 0.02]) == 0.0


def test_sharpe_ratio() -> None:
    assert a.sharpe_ratio([0.02, 0.0], periods_per_year=1) == pytest.approx(0.01 / math.sqrt(0.0002))
    assert a.sharpe_ratio([0.01, 0.01, 0.01]) is None


def test_sharpe_ratio_risk_free_lowers_ratio() -> None:
    rets = [0.01, -0.005, 0.007, 0.002, -0.001]
    assert a.sharpe_ratio(rets, risk_free_rate=0.05) < a.sharpe_ratio(rets)  # type: ignore[operator]


def test_sortino_ratio() -> None:
    assert a.sortino_ratio([0.02, -0.01], periods_per_year=1) == pytest.approx(0.005 / math.sqrt(0.0001 / 2))
    assert a.sortino_ratio([0.01, 0.02, 0.03]) is None


def test_sortino_exceeds_sharpe_for_positive_skew() -> None:
    rets = [0.05, -0.01, 0.04, -0.01, 0.03, -0.01]
    assert a.sortino_ratio(rets) > a.sharpe_ratio(rets)  # type: ignore[operator]


def test_beta() -> None:
    bench = [0.01, -0.02, 0.015, 0.003, -0.007]
    assert a.beta([2 * r for r in bench], bench) == pytest.approx(2.0)
    assert a.beta([-r for r in bench], bench) == pytest.approx(-1.0)
    assert a.beta(bench, [0.01] * 5) is None
    with pytest.raises(ValueError, match="aligned"):
        a.beta([0.1, 0.2, 0.3], [0.1, 0.2])


def test_max_drawdown() -> None:
    dd = a.max_drawdown([100, 120, 60, 90, 130])
    assert dd == {"max_drawdown": pytest.approx(0.5), "peak_index": 1, "trough_index": 2, "current_drawdown": 0.0}
    assert a.max_drawdown([1, 2, 3])["max_drawdown"] == 0.0
    assert a.max_drawdown([100, 80])["current_drawdown"] == pytest.approx(0.2)


def test_historical_var_and_cvar() -> None:
    rets = np.round(np.arange(-0.10, 0.10, 0.01), 4).tolist()  # 20 values
    assert a.historical_var(rets, 0.95) == pytest.approx(0.0905)
    assert a.historical_cvar(rets, 0.95) == pytest.approx(0.10)
    assert a.historical_cvar(rets, 0.95) >= a.historical_var(rets, 0.95)


def test_var_is_zero_when_all_gains() -> None:
    assert a.historical_var([0.01, 0.02, 0.03]) == 0.0
    assert a.historical_cvar([0.01, 0.02, 0.03]) == 0.0


@pytest.mark.parametrize("conf", [0.0, 1.0, 1.5, -0.1])
def test_var_rejects_bad_confidence(conf: float) -> None:
    with pytest.raises(ValueError, match="confidence"):
        a.historical_var([0.01, -0.02], conf)


def test_price_summary() -> None:
    s = a.price_summary([100, 120, 60, 90, 130])
    assert s["start"] == 100 and s["end"] == 130
    assert s["high"] == 130 and s["low"] == 60
    assert s["total_return"] == pytest.approx(0.3)
    assert s["max_drawdown"] == pytest.approx(0.5)
    assert s["annualized_volatility"] > 0


def test_downsample_keeps_endpoints() -> None:
    values = list(range(100))
    out = a.downsample(values, 10)
    assert len(out) == 10 and out[0] == 0 and out[-1] == 99
    assert a.downsample([1, 2, 3], 10) == [1.0, 2.0, 3.0]


# --- technicals --------------------------------------------------------------


def test_sma() -> None:
    assert a.sma(list(range(1, 11)), 5) == pytest.approx(8.0)
    assert a.sma([1, 2], 5) is None
    with pytest.raises(ValueError):
        a.sma([1, 2, 3], 0)


def test_ema_series() -> None:
    assert a.ema_series([1, 2, 3], 1) == pytest.approx([1, 2, 3])
    assert a.ema_series([1, 2, 3], 3) == pytest.approx([1, 1.5, 2.25])


def test_rsi_known_values() -> None:
    assert a.rsi([10, 11, 10, 11, 10], period=2) == pytest.approx(37.5)
    assert a.rsi(list(range(1, 30))) == 100.0
    assert a.rsi(list(range(30, 1, -1))) == pytest.approx(0.0)
    assert a.rsi([5.0] * 20) == 50.0
    assert a.rsi([1, 2, 3]) is None


def test_macd() -> None:
    assert a.macd([10.0] * 40) == {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    rising = a.macd([100 * 1.01**i for i in range(60)])
    assert rising is not None and rising["macd"] > 0
    assert a.macd(list(range(1, 30))) is None


def test_range_position() -> None:
    assert a.range_position(5, 0, 10) == 0.5
    assert a.range_position(12, 0, 10) == 1.0
    assert a.range_position(-1, 0, 10) == 0.0
    assert a.range_position(5, 5, 5) is None
    with pytest.raises(ValueError):
        a.range_position(5, 10, 0)


def test_technical_snapshot_uptrend() -> None:
    snap = a.technical_snapshot([100 * 1.002**i for i in range(300)])
    assert snap["trend"] == "uptrend"
    assert snap["sma_50"] is not None and snap["sma_200"] is not None
    assert snap["range_position_52w"] == 1.0
    assert snap["rsi_14"] == 100.0


def test_technical_snapshot_short_history() -> None:
    snap = a.technical_snapshot([100 - 0.1 * i for i in range(60)])
    assert snap["sma_200"] is None
    assert snap["trend"] == "insufficient_data"
    assert snap["price_vs_sma_200"] is None


def test_technical_snapshot_downtrend() -> None:
    assert a.technical_snapshot([300 * 0.998**i for i in range(300)])["trend"] == "downtrend"


# --- DCF ---------------------------------------------------------------------


def test_dcf_zero_growth_equals_perpetuity() -> None:
    res = a.dcf_valuation(100, 0.0, 0.10, 0.0, years=1, net_debt=200, shares_outstanding=10)
    assert res["enterprise_value"] == pytest.approx(1000)
    assert res["equity_value"] == pytest.approx(800)
    assert res["value_per_share"] == pytest.approx(80)


def test_dcf_constant_growth_matches_gordon() -> None:
    res = a.dcf_valuation(100, 0.02, 0.08, 0.02, years=7)
    assert res["enterprise_value"] == pytest.approx(102 / 0.06)
    assert len(res["projected_fcf"]) == 7  # type: ignore[arg-type]
    assert 0 < res["terminal_value_share"] < 1  # type: ignore[operator]


def test_dcf_without_shares() -> None:
    assert a.dcf_valuation(100, 0.05, 0.1, 0.02)["value_per_share"] is None


@pytest.mark.parametrize(("r", "tg", "years"), [(0.03, 0.03, 5), (0.02, 0.03, 5), (0.1, 0.02, 0)])
def test_dcf_rejects_invalid_inputs(r: float, tg: float, years: int) -> None:
    with pytest.raises(ValueError):
        a.dcf_valuation(100, 0.05, r, tg, years=years)


def test_dcf_sensitivity_shape_and_monotonicity() -> None:
    waccs = a.symmetric_grid(0.09, 0.01)
    tgs = a.symmetric_grid(0.025, 0.005)
    grid = a.dcf_sensitivity(100, 0.05, waccs, tgs, shares_outstanding=10)
    values = grid["values"]
    assert grid["metric"] == "value_per_share"
    assert len(values) == 5 and all(len(r) == 5 for r in values)  # type: ignore[arg-type]
    col = [row[2] for row in values]  # type: ignore[index]
    assert col == sorted(col, reverse=True)  # higher WACC -> lower value
    row = values[2]  # type: ignore[index]
    assert row == sorted(row)  # higher terminal growth -> higher value
    center = a.dcf_valuation(100, 0.05, 0.09, 0.025, shares_outstanding=10)["value_per_share"]
    assert values[2][2] == pytest.approx(center)  # type: ignore[index]


def test_dcf_sensitivity_undefined_cells() -> None:
    grid = a.dcf_sensitivity(100, 0.05, [0.02, 0.08], [0.03])
    assert grid["values"] == [[None], [pytest.approx(a.dcf_valuation(100, 0.05, 0.08, 0.03)["equity_value"])]]
    assert grid["metric"] == "equity_value"


def test_symmetric_grid() -> None:
    assert a.symmetric_grid(0.09, 0.01) == [0.07, 0.08, 0.09, 0.1, 0.11]
    assert a.symmetric_grid(1.0, 0.5, 3) == [0.5, 1.0, 1.5]


# --- portfolio ---------------------------------------------------------------


def test_normalize_weights() -> None:
    assert a.normalize_weights({"A": 30, "B": 70}) == {"A": 0.3, "B": 0.7}


@pytest.mark.parametrize("bad", [{}, {"A": -1, "B": 2}, {"A": 0, "B": 0}])
def test_normalize_weights_rejects(bad: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        a.normalize_weights(bad)


def test_herfindahl_index() -> None:
    eq = a.herfindahl_index({k: 1 for k in "ABCD"})
    assert eq == {"hhi": pytest.approx(0.25), "effective_positions": pytest.approx(4.0), "max_weight": 0.25}
    assert a.herfindahl_index({"A": 1})["hhi"] == 1.0
    assert a.herfindahl_index({"A": 90, "B": 10})["hhi"] == pytest.approx(0.82)


def test_correlation_matrix() -> None:
    x = [0.01, -0.02, 0.03, 0.0, 0.005]
    corr = a.correlation_matrix({"X": x, "Y": [2 * v for v in x], "Z": [-v for v in x]})
    assert corr["X"]["Y"] == pytest.approx(1.0)
    assert corr["X"]["Z"] == pytest.approx(-1.0)
    assert corr["Z"]["Z"] == pytest.approx(1.0)
    assert a.correlation_matrix({"X": x}) == {"X": {"X": 1.0}}


def test_correlation_matrix_errors() -> None:
    with pytest.raises(ValueError, match="same length"):
        a.correlation_matrix({"X": [0.1, 0.2, 0.3], "Y": [0.1, 0.2]})


def test_portfolio_risk_single_asset() -> None:
    x = [0.01, -0.02, 0.03, 0.0, 0.005]
    res = a.portfolio_risk({"X": 1.0}, {"X": x})
    assert res["annualized_volatility"] == pytest.approx(a.annualized_volatility(x))
    assert res["risk_contribution"] == {"X": pytest.approx(1.0)}
    assert res["diversification_ratio"] == pytest.approx(1.0)


def test_portfolio_risk_contributions_sum_to_one() -> None:
    rng = np.random.default_rng(7)
    rets = {k: rng.normal(0, s, 250).tolist() for k, s in [("A", 0.01), ("B", 0.02), ("C", 0.03)]}
    res = a.portfolio_risk({"A": 50, "B": 30, "C": 20}, rets)
    assert sum(res["risk_contribution"].values()) == pytest.approx(1.0)  # type: ignore[attr-defined]
    assert res["diversification_ratio"] > 1.0  # type: ignore[operator]


def test_portfolio_risk_perfect_hedge() -> None:
    x = [0.01, -0.02, 0.03, 0.0, 0.005]
    res = a.portfolio_risk({"X": 1, "Y": 1}, {"X": x, "Y": [-v for v in x]})
    assert res["annualized_volatility"] == pytest.approx(0.0, abs=1e-12)
    assert res["diversification_ratio"] is None


def test_portfolio_risk_missing_series() -> None:
    with pytest.raises(ValueError, match="Missing"):
        a.portfolio_risk({"X": 1, "Y": 1}, {"X": [0.01, 0.02]})
