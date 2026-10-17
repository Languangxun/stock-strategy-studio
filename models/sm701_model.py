from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.model_api import BaseModel, ModelContext, ModelError
from core.params import ModelMeta
from models.registry import register_model
from models.sm701_features import FEATURES_SM701, compute_features


@register_model
class Sm701Model(BaseModel):
    meta = ModelMeta(
        slug="sm701",
        name="SM701 LightGBM",
        version="1.0",
        description="44 维量价/市场环境特征，预测下一交易日上涨概率。",
        adjust="hfq",
    )

    def __init__(self, artifact_dir: Path | None = None):
        root = Path(artifact_dir) if artifact_dir else Path(__file__).parent / "artifacts" / "sm701"
        super().__init__(root)
        self.model_path = root / "lgbm_model.txt"
        self.booster = None
        self.feature_names: list[str] = []
        if self.model_path.exists():
            try:
                import lightgbm as lgb

                self.booster = lgb.Booster(model_file=str(self.model_path))
                self.feature_names = list(self.booster.feature_name() or FEATURES_SM701)
            except Exception as exc:
                raise ModelError(f"SM701 模型加载失败: {exc}") from exc

    def available(self) -> bool:
        return self.booster is not None and self.model_path.exists()

    def predict(self, bars: pd.DataFrame, params: dict[str, Any], context: ModelContext) -> pd.DataFrame:
        if not self.available():
            raise ModelError(f"SM701 模型文件不存在: {self.model_path}")
        features = compute_features(bars, context.benchmark if context else None)
        if features.empty:
            return pd.DataFrame(columns=["date", "score", "reason"])
        missing = [name for name in self.feature_names if name not in features.columns]
        if missing:
            raise ModelError(f"SM701 特征缺失: {', '.join(missing)}")
        matrix = features[self.feature_names].astype(np.float32)
        scores = np.asarray(self.booster.predict(matrix), dtype=float).clip(0.0, 1.0)
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(features["date"]).dt.normalize(),
                "score": scores,
                "reason": [f"SM701 P(次日上涨)={value:.3f}" for value in scores],
            }
        )
        return out.reset_index(drop=True)
