"""Investment research Deep Agent example.

Exports `create_investment_agent` (see `investment_agent.agent`).
"""

from investment_agent.agent import (
    DEFAULT_MODEL,
    create_investment_agent,
    resolve_model_id,
)
from investment_agent.subagents import SUBAGENT_NAMES

__all__ = [
    "DEFAULT_MODEL",
    "SUBAGENT_NAMES",
    "create_investment_agent",
    "resolve_model_id",
]
