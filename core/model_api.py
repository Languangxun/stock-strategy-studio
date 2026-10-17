from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from core.params import ModelMeta


MODEL_API_VERSION = "1.0"


class ModelError(RuntimeError):
    pass


@dataclass
class ModelContext:
    benchmark: pd.DataFrame | None = None
    universe: dict[str, pd.DataFrame] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


class BaseModel(ABC):
    meta: ModelMeta

    def __init__(self, artifact_dir: Path | None = None):
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    @abstractmethod
    def predict(self, bars: pd.DataFrame, params: dict[str, Any], context: ModelContext) -> pd.DataFrame:
        """
        Return a DataFrame with columns date, score and an optional reason.

        The score must be a causal estimate available at the close of each
        date. bars contains date/open/high/low/close/volume/amount columns.
        """

    def available(self) -> bool:
        return True
