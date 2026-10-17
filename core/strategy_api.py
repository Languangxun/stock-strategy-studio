from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from core.model_api import BaseModel, ModelContext
from core.params import StrategyMeta


STRATEGY_API_VERSION = "1.0"


class StrategyError(RuntimeError):
    pass


@dataclass
class StrategyContext:
    benchmark: pd.DataFrame | None = None
    model_bars: pd.DataFrame | None = None
    model: BaseModel | None = None
    model_context: ModelContext = field(default_factory=ModelContext)
    extras: dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    meta: StrategyMeta

    @abstractmethod
    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        """
        Return a DataFrame indexed by date with boolean columns entry and exit,
        a numeric score in [0, 100], and optional entry_reason/exit_reason.
        Signals are generated after the close and executed at the next open.
        """


def empty_signals(dates: pd.Index) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "entry": False,
            "exit": False,
            "score": 0.0,
            "entry_reason": "",
            "exit_reason": "",
        },
        index=pd.DatetimeIndex(dates),
    )


def normalize_signals(signals: pd.DataFrame, dates: pd.Index) -> pd.DataFrame:
    if signals is None:
        return empty_signals(dates)
    out = signals.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        if "date" in out.columns:
            out = out.set_index("date")
        out.index = pd.to_datetime(out.index)
    out.index = out.index.normalize()
    out = out[~out.index.duplicated(keep="last")]
    out = out.reindex(pd.DatetimeIndex(dates))
    for col, default in (
        ("entry", False),
        ("exit", False),
        ("score", 0.0),
        ("entry_reason", ""),
        ("exit_reason", ""),
    ):
        if col not in out.columns:
            out[col] = default
    out["entry"] = out["entry"].fillna(False).astype(bool)
    out["exit"] = out["exit"].fillna(False).astype(bool)
    out["score"] = pd.to_numeric(out["score"], errors="coerce").fillna(0.0).clip(0, 100)
    out["entry_reason"] = out["entry_reason"].fillna("").astype(str)
    out["exit_reason"] = out["exit_reason"].fillna("").astype(str)
    return out[["entry", "exit", "score", "entry_reason", "exit_reason"]]
