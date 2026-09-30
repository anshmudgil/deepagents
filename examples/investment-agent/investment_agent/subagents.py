"""Specialist subagent specs for the investment research desk.

Each specialist gets only the tools its role needs (least privilege). The
names below are part of the public contract. The server and UI display them,
and they appear as `lc_agent_name` in streamed metadata.
"""

from __future__ import annotations

from deepagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from investment_agent import prompts
from investment_agent.tools import (
    analyze_portfolio,
    compare_peers,
    compute_risk_metrics,
    fetch_filing_section,
    get_analyst_estimates,
    get_financial_statements,
    get_key_metrics,
    get_price_history,
    get_quote,
    run_dcf,
    search_filings,
    search_news,
    technical_indicators,
)

FUNDAMENTAL_ANALYST = "fundamental-analyst"
QUANT_ANALYST = "quant-analyst"
NEWS_SENTIMENT_ANALYST = "news-sentiment-analyst"
RISK_MANAGER = "risk-manager"
PORTFOLIO_STRATEGIST = "portfolio-strategist"

SUBAGENT_NAMES = (FUNDAMENTAL_ANALYST, QUANT_ANALYST, NEWS_SENTIMENT_ANALYST, RISK_MANAGER, PORTFOLIO_STRATEGIST)

SUBAGENT_TOOLS: dict[str, list[BaseTool]] = {
    FUNDAMENTAL_ANALYST: [
        get_quote,
        get_key_metrics,
        get_financial_statements,
        get_analyst_estimates,
        compare_peers,
        search_filings,
        fetch_filing_section,
        run_dcf,
    ],
    QUANT_ANALYST: [get_quote, get_price_history, technical_indicators, compute_risk_metrics],
    NEWS_SENTIMENT_ANALYST: [search_news, search_filings, get_quote],
    RISK_MANAGER: [
        compute_risk_metrics,
        get_price_history,
        get_key_metrics,
        search_filings,
        fetch_filing_section,
        run_dcf,
        analyze_portfolio,
    ],
    PORTFOLIO_STRATEGIST: [analyze_portfolio, compute_risk_metrics, get_quote, get_key_metrics, compare_peers],
}

_DESCRIPTIONS = {
    FUNDAMENTAL_ANALYST: (
        "Fundamental equity analyst. Give it one ticker (or a small peer set), the horizon and specific questions. "
        "Returns business-quality and valuation findings: financial statements, earnings quality, moat, DCF "
        "(bull/base/bear with sensitivity), peer multiples, consensus, and 10-K/10-Q evidence."
    ),
    QUANT_ANALYST: (
        "Quantitative/technical analyst. Give it one or more tickers. Returns trend, momentum (SMA50/200, RSI, MACD), "
        "52-week positioning, and the return/volatility profile (CAGR, vol, beta, Sharpe, Sortino, drawdown)."
    ),
    NEWS_SENTIMENT_ANALYST: (
        "News and sentiment analyst. Give it a ticker or theme and lookback window. Returns material recent "
        "developments, 8-K-confirmed events, dated upcoming catalysts, and sentiment direction."
    ),
    RISK_MANAGER: (
        "Risk manager and designated skeptic. Give it a ticker or holdings and the horizon. Returns quantified "
        "downside (bear value, stress losses, VaR/CVaR, max drawdown), balance-sheet and 10-K risk factors, "
        "and measurable thesis breakers."
    ),
    PORTFOLIO_STRATEGIST: (
        "Portfolio strategist. Give it holdings (ticker -> weight or dollar value) and the investor's horizon/risk "
        "posture. Returns weights, correlation, concentration (HHI), risk contribution, and rebalancing "
        "considerations framed as trade-offs (never trade instructions)."
    ),
}

_PROMPTS = {
    FUNDAMENTAL_ANALYST: prompts.FUNDAMENTAL_ANALYST_PROMPT,
    QUANT_ANALYST: prompts.QUANT_ANALYST_PROMPT,
    NEWS_SENTIMENT_ANALYST: prompts.NEWS_SENTIMENT_ANALYST_PROMPT,
    RISK_MANAGER: prompts.RISK_MANAGER_PROMPT,
    PORTFOLIO_STRATEGIST: prompts.PORTFOLIO_STRATEGIST_PROMPT,
}


def build_subagents(date: str, fast_model: str | BaseChatModel | None = None) -> list[SubAgent]:
    """Build the five specialist subagent specs.

    Args:
        date: Current date (ISO format) injected into every prompt.
        fast_model: Optional cheaper model for the high-volume, low-reasoning
            news role. When `None`, every specialist inherits the main model.

    Returns:
        Subagent specs in display order.
    """
    specs: list[SubAgent] = []
    for name in SUBAGENT_NAMES:
        spec: SubAgent = {
            "name": name,
            "description": _DESCRIPTIONS[name],
            "system_prompt": _PROMPTS[name].format(date=date),
            "tools": SUBAGENT_TOOLS[name],
        }
        if name == NEWS_SENTIMENT_ANALYST and fast_model is not None:
            spec["model"] = fast_model
        specs.append(spec)
    return specs
