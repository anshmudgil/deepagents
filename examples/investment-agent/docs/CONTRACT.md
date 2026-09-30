# Investment Agent — build contract

Shared interface between the core agent package, the API server, and the web UI.
Every component MUST conform to this document.

## Layout

```
examples/investment-agent/
  investment_agent/          # Python package (core agent)       — Engineering: AI/agents
    __init__.py              # exports create_investment_agent
    agent.py                 # create_investment_agent(); module-level `agent` for langgraph.json
    prompts.py               # orchestrator + subagent prompts
    subagents.py             # subagent specs (list[SubAgent])
    tools/market_data.py     # yfinance-backed tools
    tools/filings.py         # SEC EDGAR tools (free, needs SEC_USER_AGENT env)
    tools/news.py            # Tavily news search (optional; degrades gracefully without TAVILY_API_KEY)
    analytics.py             # pure-python finance math (no network), fully unit-tested
  server.py                  # FastAPI app: API + serves web/ at /       — Engineering: backend
  web/                       # static SPA (index.html, styles.css, app.js; no build step) — Design + Frontend
  tests/                     # pytest; no network (mock yfinance / httpx)
  langgraph.json             # {"graphs": {"investment": "./investment_agent/agent.py:agent"}}
  pyproject.toml, README.md, AGENTS.md, .env.example
```

## Python entry point

```python
from investment_agent import create_investment_agent
agent = create_investment_agent(model: str | BaseChatModel | None = None, checkpointer=None)
# returns a compiled LangGraph (deepagents.create_deep_agent)
```
Default model: `anthropic:claude-sonnet-5-5` (override with env `INVESTMENT_AGENT_MODEL`).
The agent writes its deliverables into its virtual filesystem (state `files`), notably
`/reports/<TICKER>_research.md` and `/reports/portfolio_review.md`.

Subagent names (the server/UI display these): `fundamental-analyst`, `quant-analyst`,
`news-sentiment-analyst`, `risk-manager`, `portfolio-strategist`.

The agent is RESEARCH-ONLY: no order execution. Every report ends with a
"not investment advice" disclaimer.

## HTTP API (server.py)

- `GET  /api/health` -> `{"status":"ok","model":"<model id>"}`
- `POST /api/threads` -> `{"thread_id":"<uuid>"}`
- `POST /api/threads/{thread_id}/runs/stream`  body `{"message":"<text>"}` -> `text/event-stream`
  Each SSE message is `event: <type>\ndata: <json>\n\n`. Types:
  - `token`      `{"text": str, "agent": str}`             # streamed assistant text; agent = "orchestrator" or subagent name
  - `tool_start` `{"id": str, "name": str, "args": object, "agent": str}`
  - `tool_end`   `{"id": str, "name": str, "output": str, "agent": str}`   # output truncated to 2000 chars
  - `todos`      `{"todos": [{"content": str, "status": "pending"|"in_progress"|"completed"}]}`
  - `files`      `{"paths": [str]}`                        # current file paths in agent filesystem
  - `done`       `{}`
  - `error`      `{"message": str}`
- `GET  /api/threads/{thread_id}/files` -> `{"files": {"<path>": "<content str>"}}`
- `GET  /api/threads/{thread_id}/messages` -> `{"messages":[{"role":"user"|"assistant","content":str}]}`
- `GET  /api/quote/{ticker}` -> `{"ticker","name","price","change","change_pct","currency","sparkline":[float,...30 closes]}`  (404 JSON `{"detail":...}` on unknown ticker)
- `GET  /` and static assets -> files from `web/`

Threads are kept in-memory via LangGraph `InMemorySaver` (example-grade).

## UI requirements (web/)

Single-page research workstation, vanilla HTML/CSS/JS, no build step, no external JS
except optionally `marked` + `DOMPurify` from cdnjs/jsdelivr for markdown rendering.
Panels: chat with streaming, live plan (todos), agent activity feed (tool calls grouped
by subagent), watchlist with sparklines (uses /api/quote), report viewer (files tab, markdown),
suggested prompt chips on empty state. Light + dark themes, responsive to 375px, WCAG AA,
keyboard accessible, `prefers-reduced-motion` respected. Prominent "research only — not investment advice" footer.
