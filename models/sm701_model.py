from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.model_api import BaseModel, ModelContext, ModelError
from core.params import ModelMeta
from models.registry import register_model
from models.sm701_features import FEATURES_SM701, compute_features


HORIZON_FILES = {
    "h1": "lgbm_model.txt",
    "h5": "lgbm_model_h5.txt",
    "h10": "lgbm_model_h10.txt",
}


@register_model
class Sm701Model(BaseModel):
    meta = ModelMeta(
        slug="sm701",
        name="SM701 LightGBM",
        version="1.2",
        description="40 维量价技术特征，输出 T+1/T+5/T+10 上涨概率。",
        adjust="hfq",
    )

    def __init__(self, artifact_dir: Path | None = None):
        root = Path(artifact_dir) if artifact_dir else Path(__file__).parent / "artifacts" / "sm701"
        super().__init__(root)
        self.model_paths = {name: root / filename for name, filename in HORIZON_FILES.items()}
        self.boosters: dict[str, Any] = {}
        self.feature_names: list[str] = []
        try:
            import lightgbm as lgb
        except Exception as exc:
            raise ModelError(f"SM701 模型加载失败: {exc}") from exc
        for name, path in self.model_paths.items():
            if not path.exists():
                continue
            try:
                self.boosters[name] = lgb.Booster(model_file=str(path))
            except Exception as exc:
                raise ModelError(f"SM701 模型加载失败 ({path.name}): {exc}") from exc
        booster = self.boosters.get("h1")
        if booster is not None:
            self.feature_names = list(booster.feature_name() or FEATURES_SM701)

    def available(self) -> bool:
        return all(name in self.boosters for name in HORIZON_FILES)

    def predict(self, bars: pd.DataFrame, params: dict[str, Any], context: ModelContext) -> pd.DataFrame:
        if not self.available():
            missing = [path.name for name, path in self.model_paths.items() if name not in self.boosters]
            raise ModelError(f"SM701 模型文件不存在: {', '.join(missing)}")
        features = compute_features(bars, context.benchmark if context else None)
        if features.empty:
            return pd.DataFrame(
                columns=["date", "score", "score_h1", "score_h5", "score_h10", "reason"]
            )
        missing = [name for name in self.feature_names if name not in features.columns]
        if missing:
            raise ModelError(f"SM701 特征缺失: {', '.join(missing)}")
        matrix = features[self.feature_names].astype(np.float32)
        scores = {
            name: np.asarray(booster.predict(matrix), dtype=float).clip(0.0, 1.0)
            for name, booster in self.boosters.items()
        }
        out = pd.DataFrame(
            {
                "date": pd.to_datetime(features["date"]).dt.normalize(),
                "score": scores["h1"],
                "score_h1": scores["h1"],
                "score_h5": scores["h5"],
                "score_h10": scores["h10"],
            }
        )
        out["reason"] = [
            f"SM701 P(1/5/10日上涨)={h1:.3f}/{h5:.3f}/{h10:.3f}"
            for h1, h5, h10 in zip(scores["h1"], scores["h5"], scores["h10"])
        ]
        return out.reset_index(drop=True)
