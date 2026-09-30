"""Investment research Deep Agent: an orchestrator plus five specialist analysts.

`agent` at module level is the graph referenced by `langgraph.json`. Building
it needs no network or API key: chat models authenticate lazily on their
first call, and tools create their HTTP clients per invocation.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from deepagents import create_deep_agent
from dotenv import load_dotenv
from langchain.agents.middleware import TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from investment_agent.prompts import ORCHESTRATOR_PROMPT
from investment_agent.subagents import build_subagents
from investment_agent.tools import get_quote

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
DEFAULT_FAST_MODEL = "anthropic:claude-haiku-4-5"
MODEL_ENV = "INVESTMENT_AGENT_MODEL"
FAST_MODEL_ENV = "INVESTMENT_AGENT_FAST_MODEL"
AGENT_NAME = "investment-research"
HOUSE_STYLE_PATH = Path(__file__).resolve().parent.parent / "AGENTS.md"

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def resolve_model_id() -> str:
    """Return the orchestrator model id, honoring `INVESTMENT_AGENT_MODEL`.

    Returns:
        A `provider:model` string.
    """
    return os.environ.get(MODEL_ENV) or DEFAULT_MODEL


def _fast_model(model: str | BaseChatModel | None) -> str | None:
    """Choose the cheaper model for high-volume subagents.

    It is only used when the caller relies on defaults. An explicitly passed
    model (for example a test double or another provider) is inherited by
    every subagent, so no second provider is required.

    Args:
        model: The model argument passed to `create_investment_agent`.

    Returns:
        A `provider:model` string, or `None` to inherit the main model.
    """
    if model is not None:
        return os.environ.get(FAST_MODEL_ENV)
    return os.environ.get(FAST_MODEL_ENV) or DEFAULT_FAST_MODEL


def load_house_style(path: Path = HOUSE_STYLE_PATH) -> str:
    """Load the desk's house research style (`AGENTS.md`) if it exists.

    Args:
        path: Location of the memory file.

    Returns:
        A prompt section, or an empty string when the file is missing.
    """
    if not path.is_file():
        return ""
    return f'\n\n<memory source="AGENTS.md">\n{path.read_text(encoding="utf-8").strip()}\n</memory>'


def build_system_prompt(today: str | None = None) -> str:
    """Assemble the orchestrator system prompt.

    Args:
        today: ISO date to inject. Defaults to the current date.

    Returns:
        The full system prompt, including house style memory.
    """
    return (
        ORCHESTRATOR_PROMPT.format(date=today or date.today().isoformat())
        + load_house_style()
    )


def create_investment_agent(
    model: str | BaseChatModel | None = None,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """Create the investment research Deep Agent.

    Args:
        model: Orchestrator model, as a `provider:model` string or a chat model
            instance. Defaults to `INVESTMENT_AGENT_MODEL` or
            `anthropic:claude-sonnet-5-5`. When you pass a model explicitly,
            all specialists use it too, unless `INVESTMENT_AGENT_FAST_MODEL`
            is set.
        checkpointer: Optional LangGraph checkpointer for multi-turn threads
            (for example `InMemorySaver()`).

    Returns:
        A compiled LangGraph graph. Deliverables are written to the state
        `files` channel under `/reports/`, and working notes under `/research/`.
    """
    today = date.today().isoformat()
    return create_deep_agent(
        model=model if model is not None else resolve_model_id(),
        tools=[get_quote],
        system_prompt=build_system_prompt(today),
        middleware=[TodoListMiddleware()],
        subagents=build_subagents(today, _fast_model(model)),
        checkpointer=checkpointer,
        name=AGENT_NAME,
    )


agent = create_investment_agent()
