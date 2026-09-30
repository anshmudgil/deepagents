"""Tests for agent assembly, subagent contracts, prompts, and a scripted end-to-end run."""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from investment_agent import SUBAGENT_NAMES, create_investment_agent, prompts
from investment_agent import agent as agent_module
from investment_agent.subagents import SUBAGENT_TOOLS, build_subagents

EXPECTED_NAMES = (
    "fundamental-analyst",
    "quant-analyst",
    "news-sentiment-analyst",
    "risk-manager",
    "portfolio-strategist",
)


def _tool_names(graph: object) -> set[str]:
    return set(graph.nodes["tools"].bound.tools_by_name)  # type: ignore[attr-defined]


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Capture `create_deep_agent` kwargs without compiling a graph."""
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> str:
        captured.update(kwargs)
        return "graph"

    monkeypatch.setattr(agent_module, "create_deep_agent", fake_create_deep_agent)
    return captured


def test_subagent_names_match_contract() -> None:
    assert SUBAGENT_NAMES == EXPECTED_NAMES
    assert [s["name"] for s in build_subagents("2026-09-30")] == list(EXPECTED_NAMES)


def test_builds_with_fake_model_and_exposes_planning_and_delegation(
    scripted_model: Callable,
) -> None:
    graph = create_investment_agent(model=scripted_model([]))
    names = _tool_names(graph)
    assert {"write_todos", "task", "get_quote", "write_file", "read_file"} <= names
    task_description = graph.nodes["tools"].bound.tools_by_name["task"].description  # type: ignore[attr-defined]
    for name in EXPECTED_NAMES:
        assert f"- {name}:" in task_description


def test_orchestrator_is_least_privilege(spy: dict, scripted_model: Callable) -> None:
    create_investment_agent(model=scripted_model([]))
    assert [t.name for t in spy["tools"]] == ["get_quote"]  # type: ignore[attr-defined]
    assert spy["name"] == "investment-research"
    assert any(type(m).__name__ == "TodoListMiddleware" for m in spy["middleware"])  # type: ignore[attr-defined]


def test_subagent_tool_scoping(spy: dict, scripted_model: Callable) -> None:
    create_investment_agent(model=scripted_model([]))
    tools = {s["name"]: {t.name for t in s["tools"]} for s in spy["subagents"]}  # type: ignore[attr-defined]
    assert tools == {
        name: {t.name for t in SUBAGENT_TOOLS[name]} for name in EXPECTED_NAMES
    }
    assert (
        "search_news" in tools["news-sentiment-analyst"]
        and "run_dcf" not in tools["news-sentiment-analyst"]
    )
    assert (
        "run_dcf" in tools["fundamental-analyst"]
        and "fetch_filing_section" in tools["risk-manager"]
    )
    assert "analyze_portfolio" in tools["portfolio-strategist"]


def test_explicit_model_is_inherited_by_all_subagents(
    spy: dict, scripted_model: Callable
) -> None:
    model = scripted_model([])
    create_investment_agent(model=model)
    assert spy["model"] is model
    assert all("model" not in s for s in spy["subagents"])  # type: ignore[attr-defined]


def test_default_models_and_env_override(
    spy: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INVESTMENT_AGENT_MODEL", raising=False)
    create_investment_agent()
    assert spy["model"] == "anthropic:claude-sonnet-5-5"
    news = next(s for s in spy["subagents"] if s["name"] == "news-sentiment-analyst")  # type: ignore[attr-defined]
    assert news["model"] == "anthropic:claude-haiku-4-5"

    monkeypatch.setenv("INVESTMENT_AGENT_MODEL", "openai:gpt-5.5")
    monkeypatch.setenv("INVESTMENT_AGENT_FAST_MODEL", "openai:gpt-5.5-mini")
    create_investment_agent()
    assert spy["model"] == "openai:gpt-5.5"
    news = next(s for s in spy["subagents"] if s["name"] == "news-sentiment-analyst")  # type: ignore[attr-defined]
    assert news["model"] == "openai:gpt-5.5-mini"


def test_checkpointer_is_passed_through(spy: dict, scripted_model: Callable) -> None:
    saver = InMemorySaver()
    create_investment_agent(model=scripted_model([]), checkpointer=saver)
    assert spy["checkpointer"] is saver


def test_module_level_agent_needs_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    module = importlib.reload(agent_module)
    assert module.agent.name == "investment-research"


# --- prompts -----------------------------------------------------------------


def test_orchestrator_prompt_enforces_research_standards() -> None:
    prompt = agent_module.build_system_prompt("2026-09-30")
    for required in (
        "write_todos",
        "ONE assistant message",
        "/reports/<TICKER>_research.md",
        "/reports/portfolio_review.md",
        "## Bull Case",
        "## Bear Case",
        "## Thesis Breakers",
        "Downside quantified",
        "**Conviction:**",
        "**Horizon:**",
        "## Sources",
        "not investment advice",
        "never claim you can",
        "2026-09-30",
    ):
        assert required in prompt, required
    assert prompt.count(prompts.DISCLAIMER) == 2  # both templates end with it
    assert not re.search(r"\{date\}", prompt)


@pytest.mark.parametrize("spec", build_subagents("2026-09-30"), ids=lambda s: s["name"])
def test_subagent_prompts_follow_contract(spec: dict) -> None:
    prompt = spec["system_prompt"]
    for required in (
        "## Responsibility",
        "## Not responsible for",
        "## Output contract",
        "**Confidence:**",
        "Data gaps",
        "Research only",
        "2026-09-30",
    ):
        assert required in prompt, (spec["name"], required)
    assert '{"error": ...}' in prompt and "{date}" not in prompt
    # Every tool a prompt tells the specialist to call must actually be granted.
    granted = {t.name for t in spec["tools"]}
    mentioned = set(re.findall(r"`([a-z_]+)`", prompt)) & {
        t.name for tools in SUBAGENT_TOOLS.values() for t in tools
    }
    assert mentioned <= granted, (spec["name"], mentioned - granted)


def test_house_style_memory_is_loaded(tmp_path: Path) -> None:
    style = tmp_path / "AGENTS.md"
    style.write_text("# House style\nUse tables.", encoding="utf-8")
    assert "Use tables." in agent_module.load_house_style(style)
    assert agent_module.load_house_style(tmp_path / "missing.md") == ""


# --- scripted end-to-end run -------------------------------------------------


def _script() -> list[AIMessage]:
    todos = [
        {"content": "Delegate quant analysis", "status": "in_progress"},
        {"content": "Write report", "status": "pending"},
    ]
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "write_todos", "args": {"todos": todos}, "id": "c1"},
                {
                    "name": "task",
                    "args": {
                        "subagent_type": "quant-analyst",
                        "description": "Analyze ACME trend. Notes: /research/ACME/quant.md",
                    },
                    "id": "c2",
                },
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/research/ACME/quant.md",
                        "content": "uptrend",
                    },
                    "id": "s1",
                }
            ],
        ),
        AIMessage(
            content="### Quant analyst: ACME\n**Verdict:** uptrend\n**Confidence:** Medium"
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {
                        "file_path": "/reports/ACME_research.md",
                        "content": "# ACME\n" + prompts.DISCLAIMER,
                    },
                    "id": "c3",
                }
            ],
        ),
        AIMessage(
            content="Report saved to /reports/ACME_research.md. Research only, not investment advice."
        ),
    ]


def test_end_to_end_delegation_files_and_stream_attribution(
    scripted_model: Callable,
) -> None:
    graph = create_investment_agent(
        model=scripted_model(_script()), checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "t1"}}
    subagent_names: set[str] = set()
    for namespace, mode, payload in graph.stream(
        {"messages": [HumanMessage(content="Research ACME")]},
        config,
        stream_mode=["messages", "updates"],
        subgraphs=True,
    ):
        if mode == "messages" and namespace:
            subagent_names.add(payload[1].get("lc_agent_name"))
    assert subagent_names == {"quant-analyst"}

    state = graph.get_state(config).values
    assert set(state["files"]) == {
        "/research/ACME/quant.md",
        "/reports/ACME_research.md",
    }
    assert state["files"]["/reports/ACME_research.md"]["content"].endswith(
        prompts.DISCLAIMER
    )
    assert [t["status"] for t in state["todos"]] == ["in_progress", "pending"]
    assert "Research only" in state["messages"][-1].content
