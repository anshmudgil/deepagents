"""FastAPI server for the investment research Deep Agent.

Exposes the HTTP API described in `docs/CONTRACT.md`: thread management, an SSE
run stream (tokens, tool calls, todos, files), thread inspection endpoints, a
cached market quote endpoint, and the static web UI from `web/`.

Run locally with:

    uvicorn server:app --reload --port 8000

Research only. Nothing served here executes trades or constitutes investment advice.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Protocol

import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

logger = logging.getLogger("investment_agent.server")

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
"""Default model id reported by `/api/health`; mirrors the agent package default."""

WEB_DIR = Path(__file__).resolve().parent / "web"
TOOL_OUTPUT_LIMIT = 2000
QUOTE_TTL_SECONDS = 60.0
QUOTE_CACHE_MAX = 512
SPARKLINE_POINTS = 30
ORCHESTRATOR = "orchestrator"
UNKNOWN_SUBAGENT = "subagent"
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9.\-^=]{1,15}$")
THREAD_ID_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"
LOCALHOST_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}
RUN_FAILED_MSG = "The agent run failed. See server logs for details."
AGENT_UNAVAILABLE_MSG = (
    "The agent is unavailable. Check the server configuration (model and API keys)."
)

ThreadId = Annotated[str, PathParam(pattern=THREAD_ID_PATTERN)]
StreamChunk = tuple[tuple[str, ...], str, object]
JsonDict = dict[str, object]


class AgentGraph(Protocol):
    """The subset of a compiled LangGraph that the server relies on."""

    def astream(
        self,
        graph_input: JsonDict,
        config: JsonDict,
        *,
        stream_mode: list[str],
        subgraphs: bool,
    ) -> AsyncGenerator[StreamChunk, None]:
        """Stream `(namespace, mode, payload)` tuples for a run."""
        ...

    async def aget_state(self, config: JsonDict) -> object:
        """Return the thread's `StateSnapshot`."""
        ...


class RunRequest(BaseModel):
    """Body of `POST /api/threads/{thread_id}/runs/stream`."""

    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            msg = "message must not be blank"
            raise ValueError(msg)
        return value


# --------------------------------------------------------------------------- helpers


def model_id() -> str:
    """Return the configured model id (env `INVESTMENT_AGENT_MODEL` or the default)."""
    return os.environ.get("INVESTMENT_AGENT_MODEL") or DEFAULT_MODEL


def sse(event: str, data: Mapping[str, object]) -> str:
    """Frame one server-sent event: an `event:` line, a JSON `data:` line, then a blank line."""
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def message_text(message: BaseMessage) -> str:
    """Extract plain text from a message whose content may be a string or block list."""
    return str(message.text)


def truncate(text: str, limit: int = TOOL_OUTPUT_LIMIT) -> str:
    """Clip `text` to `limit` characters, marking the cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def file_content(data: object) -> str:
    """Normalize a filesystem entry to a string.

    Current `FileData` stores `content` as a string; legacy entries stored a list
    of lines. Plain strings are accepted defensively.
    """
    if isinstance(data, str):
        return data
    content = data.get("content", "") if isinstance(data, Mapping) else ""
    if isinstance(content, list):
        return "\n".join(str(line) for line in content)
    return content if isinstance(content, str) else ""


def as_messages(value: object) -> list[BaseMessage]:
    """Coerce a node update's `messages` value into a list of messages."""
    items = (
        value if isinstance(value, Sequence) and not isinstance(value, str) else [value]
    )
    return [item for item in items if isinstance(item, BaseMessage)]


def normalize_todos(value: object) -> list[JsonDict]:
    """Return todos restricted to the contract's `content`/`status` fields."""
    if not isinstance(value, Sequence):
        return []
    return [
        {
            "content": str(todo.get("content", "")),
            "status": str(todo.get("status", "pending")),
        }
        for todo in value
        if isinstance(todo, Mapping)
    ]


def state_values(snapshot: object) -> Mapping[str, object]:
    """Return a `StateSnapshot`'s values, or an empty mapping for unknown threads."""
    values = getattr(snapshot, "values", None)
    return values if isinstance(values, Mapping) else {}


def thread_config(thread_id: str) -> JsonDict:
    """Build the LangGraph config for a thread."""
    return {"configurable": {"thread_id": thread_id}}


# --------------------------------------------------------------------------- agent holder


class AgentHolder:
    """Holds the agent, building it lazily so importing the server needs no API key."""

    def __init__(
        self,
        agent: AgentGraph | None = None,
        factory: Callable[[], AgentGraph] | None = None,
    ) -> None:
        """Create the holder.

        Args:
            agent: A pre-built agent (tests inject a fake here).
            factory: Builds the agent on first use when `agent` is `None`.
        """
        self._agent = agent
        self._factory = factory or build_default_agent
        self._lock = asyncio.Lock()

    @property
    def built(self) -> bool:
        """Whether an agent exists yet (no agent means no threads have state)."""
        return self._agent is not None

    async def get(self) -> AgentGraph:
        """Return the agent, building it once under a lock."""
        if self._agent is None:
            async with self._lock:
                if self._agent is None:
                    self._agent = await run_in_threadpool(self._factory)
        return self._agent


def build_default_agent() -> AgentGraph:
    """Build the investment agent with an in-memory checkpointer."""
    # Deferred imports: importing the server must not require the agent package or an API key.
    from langgraph.checkpoint.memory import InMemorySaver  # noqa: PLC0415

    from investment_agent import create_investment_agent  # noqa: PLC0415

    return create_investment_agent(checkpointer=InMemorySaver())


# --------------------------------------------------------------------------- run streaming


class RunStreamer:
    """Translates LangGraph stream chunks into contract SSE events for one run."""

    def __init__(self, paths: set[str]) -> None:
        """Start tracking from the thread's existing file paths."""
        self.paths = set(paths)
        self.agents: dict[str, str] = {}

    def agent_for(
        self, namespace: tuple[str, ...], metadata: Mapping[str, object] | None = None
    ) -> str:
        """Resolve the display agent name for a stream namespace.

        The root namespace `()` is the orchestrator. A subagent runs inside the
        parent's `tools` node, so its namespace starts with `tools:<task id>`;
        its model chunks carry `lc_agent_name` (set by `create_agent(name=...)`),
        which is remembered per top-level namespace segment for later updates.
        """
        if not namespace:
            return ORCHESTRATOR
        name = (metadata or {}).get("lc_agent_name")
        if isinstance(name, str) and name:
            self.agents.setdefault(namespace[0], name)
        return self.agents.get(namespace[0], UNKNOWN_SUBAGENT)

    def on_chunk(self, chunk: StreamChunk) -> list[str]:
        """Dispatch one `(namespace, mode, payload)` chunk."""
        namespace, mode, payload = chunk
        if mode == "messages" and isinstance(payload, tuple) and len(payload) == 2:  # noqa: PLR2004  # (message, metadata)
            return self.on_message(namespace, payload[0], payload[1])
        if mode == "updates" and isinstance(payload, Mapping):
            return self.on_updates(namespace, payload)
        return []

    def on_message(
        self, namespace: tuple[str, ...], message: object, metadata: object
    ) -> list[str]:
        """Emit a `token` event for streamed assistant text."""
        meta = metadata if isinstance(metadata, Mapping) else {}
        agent = self.agent_for(namespace, meta)
        if not isinstance(message, AIMessage):
            return []
        text = message_text(message)
        return [sse("token", {"text": text, "agent": agent})] if text else []

    def on_updates(
        self, namespace: tuple[str, ...], updates: Mapping[str, object]
    ) -> list[str]:
        """Emit tool, todo and file events from per-node state updates."""
        agent = self.agent_for(namespace)
        events: list[str] = []
        for update in updates.values():
            if not isinstance(update, Mapping):
                continue
            events.extend(self.tool_events(update.get("messages"), agent))
            if not namespace:
                events.extend(self.state_events(update))
        return events

    def tool_events(self, value: object, agent: str) -> list[str]:
        """Emit `tool_start` for AI tool calls and `tool_end` for tool results."""
        events: list[str] = []
        for message in as_messages(value):
            if isinstance(message, AIMessage):
                events.extend(
                    sse(
                        "tool_start",
                        {
                            "id": call.get("id") or "",
                            "name": call["name"],
                            "args": call.get("args", {}),
                            "agent": agent,
                        },
                    )
                    for call in message.tool_calls
                )
            elif isinstance(message, ToolMessage):
                output = truncate(message_text(message))
                events.append(
                    sse(
                        "tool_end",
                        {
                            "id": message.tool_call_id,
                            "name": message.name or "",
                            "output": output,
                            "agent": agent,
                        },
                    )
                )
        return events

    def state_events(self, update: Mapping[str, object]) -> list[str]:
        """Emit `todos`/`files` events when the orchestrator's state changes them."""
        events: list[str] = []
        if "todos" in update:
            events.append(sse("todos", {"todos": normalize_todos(update["todos"])}))
        files = update.get("files")
        if isinstance(files, Mapping) and files:
            for path, data in files.items():
                if data is None:
                    self.paths.discard(str(path))
                else:
                    self.paths.add(str(path))
            events.append(sse("files", {"paths": sorted(self.paths)}))
        return events


async def stream_run(
    holder: AgentHolder, thread_id: str, message: str
) -> AsyncGenerator[str, None]:
    """Yield SSE frames for one agent run; always ends with `done`."""
    try:
        agent = await holder.get()
    except Exception:
        logger.exception("Failed to build the investment agent")
        yield sse("error", {"message": AGENT_UNAVAILABLE_MSG})
        yield sse("done", {})
        return
    try:
        async for frame in agent_frames(agent, thread_id, message):
            yield frame
    except Exception:
        logger.exception("Agent run failed for thread %s", thread_id)
        yield sse("error", {"message": RUN_FAILED_MSG})
    yield sse("done", {})


async def agent_frames(
    agent: AgentGraph, thread_id: str, message: str
) -> AsyncGenerator[str, None]:
    """Run the agent and yield contract frames, closing the graph stream deterministically.

    The explicit `aclose()` matters on client disconnect: it stops the run now
    instead of whenever the abandoned generator is garbage-collected.
    """
    config = thread_config(thread_id)
    values = state_values(await agent.aget_state(config))
    streamer = RunStreamer(set(dict(values.get("files") or {})))
    graph_input: JsonDict = {"messages": [{"role": "user", "content": message}]}
    stream = agent.astream(
        graph_input, config, stream_mode=["messages", "updates"], subgraphs=True
    )
    try:
        async for chunk in stream:
            for frame in streamer.on_chunk(chunk):
                yield frame
    finally:
        await stream.aclose()


# --------------------------------------------------------------------------- thread inspection


async def read_values(holder: AgentHolder, thread_id: str) -> Mapping[str, object]:
    """Return a thread's state values; empty for unknown threads or an unbuilt agent."""
    if not holder.built:
        return {}
    agent = await holder.get()
    return state_values(await agent.aget_state(thread_config(thread_id)))


def serialize_messages(values: Mapping[str, object]) -> list[JsonDict]:
    """Convert conversation state to user/assistant text turns (tool traffic omitted)."""
    turns: list[JsonDict] = []
    for message in as_messages(values.get("messages") or []):
        if isinstance(message, HumanMessage):
            turns.append({"role": "user", "content": message_text(message)})
        elif isinstance(message, AIMessage) and (text := message_text(message)):
            turns.append({"role": "assistant", "content": text})
    return turns


# --------------------------------------------------------------------------- quotes


class QuoteCache:
    """Tiny TTL cache for quote payloads (including negative `None` results)."""

    def __init__(
        self,
        ttl: float = QUOTE_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a cache whose entries expire after `ttl` seconds."""
        self.ttl = ttl
        self.clock = clock
        self.entries: dict[str, tuple[float, JsonDict | None]] = {}

    def get(self, key: str) -> tuple[bool, JsonDict | None]:
        """Return `(hit, value)` for a non-expired entry."""
        entry = self.entries.get(key)
        if entry is None or entry[0] <= self.clock():
            return False, None
        return True, entry[1]

    def put(self, key: str, value: JsonDict | None) -> None:
        """Store `value`, evicting everything if the cache grows too large."""
        if len(self.entries) >= QUOTE_CACHE_MAX:
            self.entries.clear()
        self.entries[key] = (self.clock() + self.ttl, value)


def _finite(values: Sequence[object]) -> list[float]:
    """Keep finite floats only (yfinance pads missing sessions with NaN)."""
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    return [n for n in numbers if math.isfinite(n)]


def _ticker_meta(ticker: yf.Ticker, symbol: str) -> tuple[str, str]:
    """Return `(name, currency)`, degrading to defaults when metadata is unavailable."""
    try:
        currency = str(ticker.fast_info.currency or "USD")
    except Exception:  # noqa: BLE001  # metadata is best-effort; price data already succeeded
        currency = "USD"
    try:
        info = ticker.info or {}
        name = str(info.get("shortName") or info.get("longName") or symbol)
    except Exception:  # noqa: BLE001  # `info` scraping is flaky; the symbol is a fine fallback
        name = symbol
    return name, currency


def fetch_quote(symbol: str) -> JsonDict | None:
    """Fetch a quote synchronously from yfinance; `None` when the ticker has no data."""
    ticker = yf.Ticker(symbol)
    history = ticker.history(period="1mo")
    closes = [] if history is None or history.empty else _finite(list(history["Close"]))
    if not closes:
        return None
    price = closes[-1]
    previous = closes[-2] if len(closes) > 1 else price
    change = price - previous
    name, currency = _ticker_meta(ticker, symbol)
    return {
        "ticker": symbol,
        "name": name,
        "price": round(price, 4),
        "change": round(change, 4),
        "change_pct": round(change / previous * 100, 4) if previous else 0.0,
        "currency": currency,
        "sparkline": [round(c, 4) for c in closes[-SPARKLINE_POINTS:]],
    }


async def get_quote(cache: QuoteCache, raw_ticker: str) -> JsonDict:
    """Validate, look up (cached) and return a quote, raising `HTTPException` on failure."""
    if not TICKER_PATTERN.match(raw_ticker):
        raise HTTPException(status_code=422, detail="Invalid ticker symbol.")
    symbol = raw_ticker.upper()
    hit, quote = cache.get(symbol)
    if not hit:
        try:
            quote = await run_in_threadpool(fetch_quote, symbol)
        except Exception as exc:
            logger.exception("Quote lookup failed for %s", symbol)
            raise HTTPException(
                status_code=502, detail="Quote provider unavailable."
            ) from exc
        cache.put(symbol, quote)
    if quote is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {symbol}")
    return quote


# --------------------------------------------------------------------------- app


def cors_origins() -> list[str]:
    """Return extra allowed origins from env `INVESTMENT_AGENT_CORS_ORIGINS` (comma-separated)."""
    raw = os.environ.get("INVESTMENT_AGENT_CORS_ORIGINS", "")
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def register_api(app: FastAPI, holder: AgentHolder, cache: QuoteCache) -> None:
    """Attach the `/api` routes to `app`."""
    runs = RunRegistry()

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model": model_id()}

    @app.post("/api/threads")
    async def create_thread() -> dict[str, str]:
        return {"thread_id": str(uuid.uuid4())}

    @app.post("/api/threads/{thread_id}/runs/stream")
    async def run_stream(thread_id: ThreadId, body: RunRequest) -> StreamingResponse:
        # Claim the thread before any await so concurrent requests cannot both pass.
        release = runs.acquire(thread_id)
        if release is None:
            raise HTTPException(
                status_code=409, detail="A run is already in progress for this thread."
            )
        try:
            frames = stream_run(holder, thread_id, body.message)
            return ReleasingStreamingResponse(
                _guarded(frames, release),
                release=release,
                media_type="text/event-stream",
                headers=SSE_HEADERS,
            )
        except Exception:
            release()
            raise

    @app.get("/api/threads/{thread_id}/files")
    async def thread_files(thread_id: ThreadId) -> dict[str, dict[str, str]]:
        files = (await read_values(holder, thread_id)).get("files") or {}
        return {
            "files": {
                str(path): file_content(data)
                for path, data in dict(files).items()
                if data is not None
            }
        }

    @app.get("/api/threads/{thread_id}/messages")
    async def thread_messages(thread_id: ThreadId) -> dict[str, list[JsonDict]]:
        return {"messages": serialize_messages(await read_values(holder, thread_id))}

    @app.get("/api/quote/{ticker}")
    async def quote(ticker: str) -> JsonDict:
        return await get_quote(cache, ticker)


class RunRegistry:
    """Tracks the single active run per thread."""

    def __init__(self) -> None:
        """Start with no active runs."""
        self._active: dict[str, object] = {}

    def acquire(self, thread_id: str) -> Callable[[], None] | None:
        """Claim `thread_id`; return an idempotent release callback, or `None` if busy.

        The release only frees the claim it created, so a late release from a
        finished run can never free a newer run's claim.
        """
        if thread_id in self._active:
            return None
        token = object()
        self._active[thread_id] = token

        def release() -> None:
            if self._active.get(thread_id) is token:
                del self._active[thread_id]

        return release


class ReleasingStreamingResponse(StreamingResponse):
    """`StreamingResponse` that runs `release` once the response is finished.

    This covers the case where the body iterator never starts (for example, the
    client disconnects first), so its own `finally` would never run.
    """

    def __init__(
        self,
        content: AsyncGenerator[str, None],
        *,
        release: Callable[[], None],
        media_type: str,
        headers: Mapping[str, str],
    ) -> None:
        """Wrap `content`, remembering the callback that frees the thread."""
        super().__init__(content, media_type=media_type, headers=headers)
        self._release = release

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send the response, then release the thread whatever happened."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


async def _guarded(
    frames: AsyncGenerator[str, None], release: Callable[[], None]
) -> AsyncGenerator[str, None]:
    """Stream `frames`, then close them and release the thread's run claim."""
    try:
        async for frame in frames:
            yield frame
    finally:
        try:
            await frames.aclose()
        finally:
            release()


def mount_web(app: FastAPI, web_dir: Path) -> None:
    """Serve the SPA at `/`; mounted last so it never shadows `/api` routes."""
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
        return

    @app.get("/", include_in_schema=False)
    async def web_missing() -> JSONResponse:
        return JSONResponse(
            {"detail": "Web UI not found; the API is available under /api."},
            status_code=404,
        )


def create_app(
    agent: AgentGraph | None = None,
    *,
    agent_factory: Callable[[], AgentGraph] | None = None,
    web_dir: Path | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Args:
        agent: Pre-built agent graph. When `None`, the agent is built lazily on
            the first run so importing this module needs no API key.
        agent_factory: Override for how the lazy agent is built.
        web_dir: Directory of static UI assets; defaults to `web/` next to this file.

    Returns:
        The configured application.
    """
    app = FastAPI(
        title="Investment Research Agent",
        version="0.1.0",
        description="Research only. Not investment advice.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_origin_regex=LOCALHOST_ORIGIN_REGEX,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    register_api(app, AgentHolder(agent, agent_factory), QuoteCache())
    mount_web(app, web_dir or WEB_DIR)
    return app


app = create_app()

__all__: list[str] = ["AgentGraph", "RunRequest", "app", "create_app"]
