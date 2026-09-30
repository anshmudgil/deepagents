"""Network-free tests for the FastAPI server (`server.py`)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from deepagents import create_deep_agent
from fastapi.testclient import TestClient
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

import server


class ScriptedModel(BaseChatModel):
    """Chat model that replays scripted replies; shared by orchestrator and subagent."""

    replies: Iterator[AIMessage] = Field(exclude=True)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools: object, **kwargs: object) -> ScriptedModel:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: object = None,
        **kwargs: object,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=next(self.replies))])


def scripted_agent() -> object:
    """Build a real deep agent: plan, delegate to a subagent, write a report, answer."""
    model = ScriptedModel(
        replies=iter(
            [
                AIMessage(
                    content="Planning.",
                    tool_calls=[
                        {
                            "name": "write_todos",
                            "args": {
                                "todos": [
                                    {"content": "Analyze AAPL", "status": "in_progress"}
                                ]
                            },
                            "id": "call_todo",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {
                                "description": "Run the numbers",
                                "subagent_type": "quant-analyst",
                            },
                            "id": "call_task",
                        }
                    ],
                ),
                AIMessage(content="Volatility is 25%."),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {
                                "file_path": "/reports/AAPL_research.md",
                                "content": "# AAPL\nNot investment advice.",
                            },
                            "id": "call_write",
                        }
                    ],
                ),
                AIMessage(content="Report written."),
            ]
        )
    )
    subagent = {
        "name": "quant-analyst",
        "description": "Quant work",
        "system_prompt": "You are a quant.",
        "model": model,
        "tools": [],
    }
    return create_deep_agent(
        model=model,
        subagents=[subagent],
        middleware=[TodoListMiddleware()],
        checkpointer=InMemorySaver(),
    )


class StubAgent:
    """Minimal `AgentGraph` with canned state; optionally fails mid-stream."""

    def __init__(
        self, values: dict[str, object] | None = None, fail: bool = False
    ) -> None:
        self.values = values or {}
        self.fail = fail

    async def aget_state(self, config: dict[str, object]) -> SimpleNamespace:
        return SimpleNamespace(values=self.values)

    async def astream(
        self, graph_input: object, config: object, **kwargs: object
    ) -> AsyncIterator[object]:
        yield ((), "messages", (AIMessage(content="partial"), {}))
        if self.fail:
            msg = "secret internal detail /etc/passwd"
            raise RuntimeError(msg)


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events = []
    for frame in body.split("\n\n"):
        if not frame.strip():
            continue
        lines = frame.split("\n")
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")
        events.append(
            (lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :]))
        )
    return events


def make_client(agent: object, tmp_path: Path) -> TestClient:
    return TestClient(server.create_app(agent, web_dir=tmp_path / "missing-web"))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return make_client(scripted_agent(), tmp_path)


def run(
    client: TestClient, thread_id: str, message: str = "Research AAPL"
) -> list[tuple[str, dict[str, object]]]:
    response = client.post(
        f"/api/threads/{thread_id}/runs/stream", json={"message": message}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    return parse_sse(response.text)


def test_health_reports_model(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INVESTMENT_AGENT_MODEL", "anthropic:test-model")
    assert client.get("/api/health").json() == {
        "status": "ok",
        "model": "anthropic:test-model",
    }


def test_module_app_is_lazy() -> None:
    # Importing `server` must not build the agent (no API key needed); health stays cheap.
    response = TestClient(server.app).get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_lazy_factory_called_once_on_first_run(tmp_path: Path) -> None:
    calls: list[int] = []

    def factory() -> StubAgent:
        calls.append(1)
        return StubAgent()

    client = TestClient(server.create_app(agent_factory=factory, web_dir=tmp_path))
    assert client.get("/api/threads/t1/files").json() == {"files": {}}
    assert calls == []
    run(client, "t1")
    run(client, "t1")
    assert calls == [1]


def test_create_thread_returns_uuid(client: TestClient) -> None:
    first = client.post("/api/threads").json()["thread_id"]
    second = client.post("/api/threads").json()["thread_id"]
    assert len(first) == 36
    assert first != second


def test_stream_event_framing_and_order(client: TestClient) -> None:
    thread_id = client.post("/api/threads").json()["thread_id"]
    events = run(client, thread_id)
    types = [kind for kind, _ in events]

    assert types[-1] == "done"
    assert events[-1][1] == {}
    assert "error" not in types
    assert {"token", "tool_start", "tool_end", "todos", "files"} <= set(types)

    tokens = [data for kind, data in events if kind == "token"]
    assert {"text": "Planning.", "agent": "orchestrator"} in tokens
    assert {"text": "Volatility is 25%.", "agent": "quant-analyst"} in tokens
    assert tokens[-1] == {"text": "Report written.", "agent": "orchestrator"}

    starts = {data["id"]: data for kind, data in events if kind == "tool_start"}
    ends = {data["id"]: data for kind, data in events if kind == "tool_end"}
    assert set(starts) == set(ends) == {"call_todo", "call_task", "call_write"}
    assert starts["call_task"]["args"]["subagent_type"] == "quant-analyst"
    assert starts["call_task"]["agent"] == "orchestrator"
    assert ends["call_task"]["output"] == "Volatility is 25%."
    assert types.index("tool_start") < types.index("tool_end")

    todos = next(data for kind, data in events if kind == "todos")
    assert todos == {"todos": [{"content": "Analyze AAPL", "status": "in_progress"}]}
    files = next(data for kind, data in events if kind == "files")
    assert files == {"paths": ["/reports/AAPL_research.md"]}


def test_files_and_messages_after_run(client: TestClient) -> None:
    run(client, "thread-a")
    files = client.get("/api/threads/thread-a/files").json()
    assert files == {
        "files": {"/reports/AAPL_research.md": "# AAPL\nNot investment advice."}
    }
    messages = client.get("/api/threads/thread-a/messages").json()["messages"]
    assert messages[0] == {"role": "user", "content": "Research AAPL"}
    assert messages[-1] == {"role": "assistant", "content": "Report written."}
    assert all(m["role"] in {"user", "assistant"} and m["content"] for m in messages)


def test_unknown_thread_returns_empty(client: TestClient) -> None:
    run(client, "known")
    assert client.get("/api/threads/unknown/files").json() == {"files": {}}
    assert client.get("/api/threads/unknown/messages").json() == {"messages": []}


def test_files_normalizes_legacy_and_current_formats(tmp_path: Path) -> None:
    values = {
        "files": {
            "/new.md": {"content": "line1\nline2", "encoding": "utf-8"},
            "/legacy.md": {
                "content": ["a", "b"],
                "created_at": "x",
                "modified_at": "y",
            },
        },
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content=[{"type": "text", "text": "hello"}]),
            ToolMessage(content="x", tool_call_id="1"),
        ],
    }
    client = make_client(StubAgent(values), tmp_path)
    assert client.get("/api/threads/t/files").json() == {
        "files": {"/new.md": "line1\nline2", "/legacy.md": "a\nb"}
    }
    assert client.get("/api/threads/t/messages").json() == {
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    }


@pytest.mark.parametrize("message", ["", "   ", "x" * 8001])
def test_stream_rejects_invalid_message(client: TestClient, message: str) -> None:
    assert (
        client.post("/api/threads/t/runs/stream", json={"message": message}).status_code
        == 422
    )


def test_stream_rejects_invalid_thread_id(client: TestClient) -> None:
    assert (
        client.post(
            "/api/threads/bad%20id/runs/stream", json={"message": "hi"}
        ).status_code
        == 422
    )


def test_stream_error_is_sanitized(tmp_path: Path) -> None:
    events = run(make_client(StubAgent(fail=True), tmp_path), "t")
    assert [kind for kind, _ in events] == ["token", "error", "done"]
    assert events[1][1] == {"message": server.RUN_FAILED_MSG}
    assert "passwd" not in json.dumps(events)


def test_stream_reports_agent_build_failure(tmp_path: Path) -> None:
    def factory() -> StubAgent:
        msg = "ANTHROPIC_API_KEY=sk-secret missing"
        raise RuntimeError(msg)

    client = TestClient(server.create_app(agent_factory=factory, web_dir=tmp_path))
    events = run(client, "t")
    assert events == [
        ("error", {"message": server.AGENT_UNAVAILABLE_MSG}),
        ("done", {}),
    ]


def test_cors_allows_localhost_only(client: TestClient) -> None:
    ok = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    denied = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_serves_web_index(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>workstation</h1>")
    client = TestClient(server.create_app(StubAgent(), web_dir=tmp_path))
    assert "workstation" in client.get("/").text
    assert client.get("/api/health").status_code == 200


def test_missing_web_dir_is_tolerated(client: TestClient) -> None:
    assert client.get("/").status_code == 404


class FakeTicker:
    """Stand-in for `yfinance.Ticker`; counts history lookups."""

    calls = 0
    closes: dict[str, list[float]] = {"AAPL": [float(100 + i) for i in range(35)]}

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.fast_info = SimpleNamespace(currency="USD")
        self.info = {"shortName": "Apple Inc."}

    def history(self, period: str) -> pd.DataFrame:
        FakeTicker.calls += 1
        return pd.DataFrame({"Close": self.closes.get(self.symbol, [])})


@pytest.fixture
def fake_yf(monkeypatch: pytest.MonkeyPatch) -> type[FakeTicker]:
    FakeTicker.calls = 0
    monkeypatch.setattr(server.yf, "Ticker", FakeTicker)
    return FakeTicker


def test_quote_payload_and_cache(client: TestClient, fake_yf: type[FakeTicker]) -> None:
    body = client.get("/api/quote/aapl").json()
    assert body["ticker"] == "AAPL"
    assert body["name"] == "Apple Inc."
    assert body["price"] == 134.0
    assert body["change"] == 1.0
    assert body["change_pct"] == pytest.approx(1 / 133 * 100, abs=1e-3)
    assert body["currency"] == "USD"
    assert len(body["sparkline"]) == 30
    assert body["sparkline"][-1] == 134.0
    client.get("/api/quote/AAPL")
    assert fake_yf.calls == 1


def test_quote_unknown_ticker_404(
    client: TestClient, fake_yf: type[FakeTicker]
) -> None:
    response = client.get("/api/quote/ZZZZ")
    assert response.status_code == 404
    assert "detail" in response.json()


@pytest.mark.parametrize("ticker", ["BAD!", "A" * 16, "x y"])
def test_quote_rejects_invalid_ticker(
    client: TestClient, fake_yf: type[FakeTicker], ticker: str
) -> None:
    assert client.get(f"/api/quote/{ticker}").status_code == 422
    assert fake_yf.calls == 0


def test_quote_accepts_index_symbols(
    client: TestClient, fake_yf: type[FakeTicker]
) -> None:
    fake_yf.closes["^GSPC"] = [5000.0, 5010.0]
    assert client.get("/api/quote/%5EGSPC").json()["change"] == 10.0
