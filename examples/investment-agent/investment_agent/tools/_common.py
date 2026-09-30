"""Shared helpers for tool implementations: error capture and JSON shaping."""

from __future__ import annotations

import functools
import logging
import math
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import ParamSpec, TypeVar

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]

_TICKER_RE = re.compile(r"^\^?[A-Z0-9][A-Z0-9.\-=]{0,14}$")


def safe_tool(func: Callable[P, R]) -> Callable[P, R | dict[str, str]]:
    """Wrap a tool function so exceptions become `{"error": ...}` payloads.

    Tools must never raise into the agent loop: a structured error lets the
    model retry with different arguments or degrade gracefully.

    Args:
        func: Tool implementation.

    Returns:
        The wrapped function with the same signature and docstring.
    """

    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | dict[str, str]:
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001  # tools report failures to the model instead of raising
            logger.warning("Tool %s failed: %s", func.__name__, exc)
            return {"error": f"{func.__name__} failed: {type(exc).__name__}: {exc}"}

    return wrapper


def normalize_ticker(ticker: str) -> str:
    """Upper-case and validate a ticker symbol.

    Args:
        ticker: Raw ticker (e.g. `" aapl "`, `"BRK-B"`, `"^GSPC"`).

    Returns:
        The normalized ticker.

    Raises:
        ValueError: If the ticker contains unexpected characters.
    """
    symbol = ticker.strip().upper()
    if not _TICKER_RE.match(symbol):
        msg = f"Invalid ticker symbol: {ticker!r}"
        raise ValueError(msg)
    return symbol


def to_jsonable(value: object) -> JSONValue:
    """Convert numpy/pandas/datetime scalars and containers to JSON-safe values.

    NaN and infinities become `None`; floats are rounded to 6 significant
    decimals to keep tool output compact.

    Args:
        value: Arbitrary value.

    Returns:
        A JSON-serializable equivalent.
    """
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    if isinstance(value, pd.Timestamp | datetime | date):
        return (
            value.date().isoformat()
            if isinstance(value, datetime)
            else value.isoformat()
        )
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return None if not math.isfinite(value) else round(value, 6)
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)


def column_label(col: object) -> str:
    """Render a DataFrame column label (often a period-end timestamp) as text.

    Args:
        col: Column label.

    Returns:
        ISO date for timestamps, `str(col)` otherwise.
    """
    if isinstance(col, pd.Timestamp | datetime):
        return col.date().isoformat()
    return str(col)


def frame_to_records(
    df: pd.DataFrame | None, max_rows: int = 10
) -> list[dict[str, JSONValue]]:
    """Convert a row-oriented DataFrame into a list of JSON-safe records.

    Args:
        df: DataFrame. A meaningful (non-range) index is kept as a field.
        max_rows: Maximum rows to keep.

    Returns:
        List of record dicts (empty for `None` or empty frames).
    """
    if df is None or getattr(df, "empty", True):
        return []
    trimmed = df.head(max_rows)
    if not isinstance(trimmed.index, pd.RangeIndex):
        trimmed = trimmed.reset_index()
    return [to_jsonable(rec) for rec in trimmed.to_dict(orient="records")]  # type: ignore[misc]


def statement_to_dict(
    df: pd.DataFrame | None,
    rows: list[str],
    max_periods: int,
    scale: float = 1e6,
) -> dict[str, dict[str, JSONValue]]:
    """Trim a yfinance financial statement to key rows and recent periods.

    Args:
        df: Statement with line items as index and period-end dates as columns.
        rows: Preferred line items; missing ones are skipped. When none match,
            the first 20 rows are used.
        max_periods: Most recent periods to keep.
        scale: Divisor for values (default reports in millions). Per-share
            rows (containing "EPS") are never scaled.

    Returns:
        `{line_item: {period: value}}`.
    """
    if df is None or df.empty:
        return {}
    cols = sorted(df.columns, reverse=True)[:max_periods]
    selected = [r for r in rows if r in df.index] or list(df.index[:20])
    out: dict[str, dict[str, JSONValue]] = {}
    for row in selected:
        series = df.loc[row, cols]
        divisor = 1.0 if "EPS" in str(row) else scale
        out[str(row)] = {
            column_label(c): to_jsonable(_scaled(series[c], divisor)) for c in cols
        }
    return out


def _scaled(value: object, scale: float) -> object:
    """Divide numeric values by `scale`, passing other values through.

    Args:
        value: Cell value.
        scale: Divisor.

    Returns:
        Scaled number or original value.
    """
    if isinstance(value, int | float | np.number) and not isinstance(value, bool):
        return float(value) / scale
    return value
