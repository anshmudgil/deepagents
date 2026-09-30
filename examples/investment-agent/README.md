# Investment Research Deep Agent

An investment research desk built on [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview). An orchestrator ("Chief Investment Research Officer") plans the work with `write_todos` and hands it to five specialist analysts in parallel. It then reconciles what they found and publishes an institutional-style report to its virtual filesystem.

The example has three parts: a Python agent package, a FastAPI server, and a no-build web workstation UI.

> **Research only.** The agent never executes, routes, or simulates trades. Every report ends with a "not investment advice" disclaimer.

## Architecture

```
                     ┌─ fundamental-analyst     statements, moat, DCF, comps, 10-K/10-Q
                     ├─ quant-analyst           trend, SMA/RSI/MACD, vol, beta, Sharpe
User ─▶ Orchestrator ┼─ news-sentiment-analyst  news, 8-K events, catalysts, sentiment
  (plans, delegates, ├─ risk-manager            VaR/CVaR, drawdown, stress, 1A risks, thesis breakers
   synthesizes)      └─ portfolio-strategist    weights, correlation, HHI, risk contribution
        │
        └─▶ /reports/<TICKER>_research.md | /reports/portfolio_review.md
            (specialist working notes go to /research/<TICKER>/<role>.md)
```

- **Hierarchical fan-out/fan-in.** Independent `task` calls go out in one turn, so the specialists run at the same time.
- **Least privilege.**
  - The orchestrator has only `get_quote` plus the Deep Agents built-ins: planning, files, and `task`.
  - Each specialist gets only the tools its role needs. See `investment_agent/subagents.py`.
- **Context discipline.**
  - Specialists write their full notes to files.
  - Each returns a summary of 250 words or fewer, with a confidence level and a list of data gaps.
- **Graceful degradation.**
  - Every tool catches its own failures and returns `{"error": ...}`.
  - Prompts tell each agent to retry once, record the gap, and lower its conviction.
- **Research quality gate.** Before publishing, the orchestrator checks that the report has:
  - a bull case and a bear case
  - a quantified downside
  - measurable thesis breakers
  - a conviction level and a horizon
  - an assumptions table plus sensitivity
  - cited sources

## Tools

| Tool | Source | Notes |
|---|---|---|
| `get_quote`, `get_price_history` | yfinance | History returns summary stats plus about 20 sampled closes, not a raw dump |
| `get_financial_statements` | yfinance | Income, balance, or cashflow; annual or quarterly; key rows; values in $M |
| `get_key_metrics`, `compare_peers` | yfinance | Valuation ratios, margins, growth, leverage; peer medians |
| `get_analyst_estimates` | yfinance | Targets, ratings, EPS/revenue estimates, recent up/downgrades |
| `search_filings`, `fetch_filing_section` | SEC EDGAR | Ticker to CIK, recent 10-K/10-Q/8-K URLs, HTML-stripped sections ("Item 1A", "Item 7"). Fetches from `https://*.sec.gov` on the default port only, and re-checks every redirect hop. Filing text comes back wrapped in untrusted-content markers |
| `search_news` | Tavily | Falls back to Yahoo Finance headlines when `TAVILY_API_KEY` is unset |
| `run_dcf` | analytics | Two-stage DCF, plus a 5x5 WACC x terminal-growth sensitivity grid |
| `compute_risk_metrics` | analytics | Vol, beta vs SPY, Sharpe, Sortino, max drawdown, historical VaR/CVaR 95% |
| `technical_indicators` | analytics | SMA 50/200, RSI 14, MACD 12/26/9, 52-week range position, trend label |
| `analyze_portfolio` | analytics | Weights, correlation, HHI, portfolio vol, contribution to risk, VaR/CVaR |

The math lives in `investment_agent/analytics.py`. It is pure, deterministic, network-free, and fully unit-tested.

## Setup

```bash
cd examples/investment-agent
uv venv && uv pip install -e ../../libs/deepagents -e '.[dev]'   # or: uv sync
cp .env.example .env    # then fill in the keys below
```

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | yes | Default models are Claude |
| `SEC_USER_AGENT` | recommended | EDGAR requires `"Name email@domain"` |
| `TAVILY_API_KEY` | optional | Full news search. Without it, the agent uses Yahoo headlines |
| `INVESTMENT_AGENT_MODEL` | optional | Orchestrator model (default `anthropic:claude-sonnet-5-5`) |
| `INVESTMENT_AGENT_FAST_MODEL` | optional | News analyst model (default `anthropic:claude-haiku-4-5`) |

## Run the web workstation (server + UI)

```bash
uv run uvicorn server:app --reload
```

Then open <http://localhost:8000>. The UI shows:
- chat with streaming
- a live plan (todos)
- agent activity grouped by specialist
- a watchlist with sparklines
- a report viewer for `/reports/*.md`

The HTTP/SSE API is specified in [`docs/CONTRACT.md`](docs/CONTRACT.md).

## Other ways to run

**LangGraph dev server / Studio.** The graph is registered in `langgraph.json` as `investment`:

```bash
uv run langgraph dev
```

**From Python:**

```python
from langgraph.checkpoint.memory import InMemorySaver
from investment_agent import create_investment_agent

agent = create_investment_agent(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "demo"}}
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Deep dive on MSFT, 12-month horizon"}]},
    config,
)
print(result["files"]["/reports/MSFT_research.md"]["content"])
```

`create_investment_agent(model=None, checkpointer=None)`:
- `model` accepts a `provider:model` string or a `BaseChatModel`.
- When you pass a model explicitly, every specialist inherits it, unless you also set `INVESTMENT_AGENT_FAST_MODEL`.

## Example prompts

- "Deep dive on NVDA with a 12-month horizon. What is the bear case?"
- "Compare AAPL and MSFT on valuation and risk."
- "Review my portfolio: 40% AAPL, 25% NVDA, 20% JPM, 15% TLT. I'm a 5-year investor."
- "What's the downside for TSLA if the market falls 20%?"

## Customizing

- **House style.** Edit `AGENTS.md`. It is loaded into the orchestrator's prompt as memory.
- **Prompts.** `investment_agent/prompts.py` (versioned `v1`) holds the orchestrator workflow, the report templates, and one contract per specialist: responsibility, not-responsible-for, method, output contract, and failure behavior.
- **Specialists.** Add or re-scope them in `investment_agent/subagents.py`.

## Streaming notes (for integrators)

Stream with `graph.stream(..., stream_mode=["messages", "updates"], subgraphs=True)`.

- **Orchestrator chunks** have an empty namespace `()`.
- **Specialist chunks** run inside the parent's `tools` node, so their namespace starts with `tools:<id>`.
- **Message metadata** carries `lc_agent_name`: the specialist's name (for example `quant-analyst`), or `investment-research` for the orchestrator.
- **State channels:**
  - `todos` is a list of `{content, status}`.
  - `files` is a mapping of `path -> {content: str, encoding, created_at, modified_at}`.
  - Files that specialists write are merged back into the parent's `files`.

## Tests

```bash
uv run pytest -q      # network-free: yfinance, httpx, and Tavily are faked
uv run ruff check .
```

## Limitations

- Yahoo Finance data is unofficial, can be delayed, and sometimes has gaps. The agent reports gaps instead of filling them.
- EDGAR section extraction is heuristic. When an item cannot be found, the agent reads the full text instead.
- DCF outputs depend entirely on the assumptions. Reports show the sensitivity grid and the terminal-value share for that reason.
- Threads live only in process memory (`InMemorySaver`). They are never evicted, so memory grows with every thread until the server restarts, and a restart loses all threads. This is example-grade. For real use, switch to a persistent checkpointer with a retention policy.
- Yahoo reports cash-flow and balance-sheet values in the company's reporting currency, which can differ from its trading currency (TSM reports in TWD, BABA in CNY).
  - `get_key_metrics` returns both currencies and skips the FCF yield when they differ.
  - `run_dcf` refuses to mix them unless you pass converted `base_fcf` and `net_debt` values.
- `get_key_metrics` converts Yahoo's percent fields (`debtToEquity`, `dividendYield`) into multiples and fractions.

*For research and educational purposes only. Not investment advice.*
