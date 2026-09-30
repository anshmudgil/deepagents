"""LangChain tools for market data, SEC filings, news and quantitative analysis."""

from investment_agent.tools.filings import (
    FILINGS_TOOLS,
    fetch_filing_section,
    search_filings,
)
from investment_agent.tools.market_data import (
    MARKET_DATA_TOOLS,
    compare_peers,
    get_analyst_estimates,
    get_financial_statements,
    get_key_metrics,
    get_price_history,
    get_quote,
)
from investment_agent.tools.news import NEWS_TOOLS, search_news
from investment_agent.tools.quant import (
    QUANT_TOOLS,
    analyze_portfolio,
    compute_risk_metrics,
    run_dcf,
    technical_indicators,
)

ALL_TOOLS = [*MARKET_DATA_TOOLS, *FILINGS_TOOLS, *NEWS_TOOLS, *QUANT_TOOLS]

__all__ = [
    "ALL_TOOLS",
    "FILINGS_TOOLS",
    "MARKET_DATA_TOOLS",
    "NEWS_TOOLS",
    "QUANT_TOOLS",
    "analyze_portfolio",
    "compare_peers",
    "compute_risk_metrics",
    "fetch_filing_section",
    "get_analyst_estimates",
    "get_financial_statements",
    "get_key_metrics",
    "get_price_history",
    "get_quote",
    "run_dcf",
    "search_filings",
    "search_news",
    "technical_indicators",
]
