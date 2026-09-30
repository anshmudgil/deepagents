"""Shared fixtures: network-free fakes for yfinance, HTTP, and chat models."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np
import pandas as pd
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from investment_agent.tools import filings, market_data


def make_closes(symbol: str, n: int = 520, drift: float = 0.0005, vol: float = 0.015) -> pd.Series:
    """Deterministic geometric random-walk closes seeded by the symbol."""
    rng = np.random.default_rng(sum(ord(c) for c in symbol))
    rets = rng.normal(drift, vol, n)
    prices = 100 * np.cumprod(1 + rets)
    index = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(prices, index=index, name="Close")


def income_statement() -> pd.DataFrame:
    cols = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31", "2022-12-31", "2021-12-31"])
    return pd.DataFrame(
        {
            c: {"Total Revenue": 1_000e6 * (1.1**-i), "Net Income": 200e6 * (1.1**-i), "Diluted EPS": 2.5 - 0.2 * i}
            for i, c in enumerate(cols)
        }
    )


DEFAULT_INFO: dict[str, object] = {
    "longName": "Acme Corp",
    "shortName": "Acme",
    "currency": "USD",
    "sector": "Technology",
    "industry": "Software",
    "marketCap": 50e9,
    "enterpriseValue": 52e9,
    "trailingPE": 25.0,
    "forwardPE": 20.0,
    "grossMargins": 0.6,
    "revenueGrowth": 0.12,
    "freeCashflow": 2e9,
    "totalDebt": 5e9,
    "totalCash": 3e9,
    "sharesOutstanding": 1e9,
    "currentPrice": 50.0,
    "fiftyTwoWeekHigh": 60.0,
    "fiftyTwoWeekLow": 40.0,
    "beta": 1.1,
    "enterpriseToEbitda": float("nan"),
}


class FakeTicker:
    """Stand-in for `yfinance.Ticker` with configurable datasets."""

    def __init__(self, symbol: str, registry: dict[str, dict[str, object]]) -> None:
        self.symbol = symbol
        self._data = registry.get(symbol, {})

    def history(self, period: str = "1y", interval: str = "1d", auto_adjust: bool = True) -> pd.DataFrame:
        closes = self._data.get("closes")
        if closes is None:
            return pd.DataFrame()
        return pd.DataFrame({"Close": closes})

    def __getattr__(self, name: str) -> object:
        data = self.__dict__["_data"]
        if name not in data:
            raise AttributeError(name)
        value = data[name]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def market(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, object]]:
    """Registry of fake tickers; tests add/modify entries before calling tools."""
    registry: dict[str, dict[str, object]] = {
        "ACME": {
            "closes": make_closes("ACME"),
            "info": dict(DEFAULT_INFO),
            "income_stmt": income_statement(),
            "quarterly_income_stmt": income_statement(),
            "news": [],
        },
        "PEER": {"closes": make_closes("PEER"), "info": {**DEFAULT_INFO, "shortName": "Peer", "trailingPE": 15.0}},
        "SPY": {"closes": make_closes("SPY", drift=0.0003, vol=0.01), "info": {"longName": "SPDR S&P 500"}},
    }

    def fake_get_ticker(symbol: str) -> FakeTicker:
        return FakeTicker(market_data.normalize_ticker(symbol), registry)

    monkeypatch.setattr(market_data, "get_ticker", fake_get_ticker)
    return registry


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test reaches for the real network or real keys."""

    def blocked(*args: object, **kwargs: object) -> None:
        msg = "Network access attempted in a unit test"
        raise AssertionError(msg)

    monkeypatch.setattr("httpx.get", blocked)
    monkeypatch.setattr("yfinance.Ticker", blocked)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("INVESTMENT_AGENT_FAST_MODEL", raising=False)
    filings._ticker_map.cache_clear()


class ScriptedChatModel(BaseChatModel):
    """Chat model that replays scripted replies (shared by orchestrator and subagents)."""

    replies: Iterator[AIMessage] = Field(exclude=True)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: object, **kwargs: object) -> ScriptedChatModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=next(self.replies))])


@pytest.fixture
def scripted_model() -> Callable[[list[AIMessage]], ScriptedChatModel]:
    def factory(replies: list[AIMessage]) -> ScriptedChatModel:
        return ScriptedChatModel(replies=iter(replies))

    return factory
