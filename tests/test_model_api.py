from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from core.model_api import ModelContext
from models.registry import create_model, list_models
from models.sm701_features import FEATURES_SM701, compute_features


ROOT = Path(__file__).resolve().parent.parent


class ModelApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = pd.read_csv(ROOT / "sample_data" / "sz000001_hfq.csv")
        cls.bench = pd.read_csv(ROOT / "sample_data" / "sh000001.csv")
        cls.context = ModelContext(benchmark=cls.bench, universe={"sz000001": cls.bars})

    def test_registry_and_model_loads(self):
        slugs = {meta.slug for meta in list_models()}
        self.assertIn("sm701", slugs)
        model = create_model("sm701")
        self.assertTrue(model.available())
        self.assertEqual(model.meta.adjust, "hfq")

    def test_features_are_complete_and_causal_length(self):
        features = compute_features(self.bars, self.bench)
        self.assertEqual(len(features.columns), len(FEATURES_SM701) + 1)
        self.assertEqual(list(features.columns), ["date", *FEATURES_SM701])
        self.assertFalse(features[FEATURES_SM701].isna().any().any())
        self.assertGreater(len(features), 1500)
        first = features.iloc[0]
        self.assertAlmostEqual(float(first["r_ret5"]), 0.5)
        self.assertTrue(pd.Timestamp(features.iloc[-1]["date"]) <= pd.Timestamp("2026-10-16"))

    def test_predictions(self):
        model = create_model("sm701")
        pred = model.predict(self.bars, {}, self.context)
        self.assertFalse(pred.empty)
        self.assertEqual(set(pred.columns), {"date", "score", "reason"})
        self.assertTrue((pred["score"] >= 0).all())
        self.assertTrue((pred["score"] <= 1).all())
        self.assertTrue(pred["reason"].str.contains("SM701").all())
        again = model.predict(self.bars, {}, self.context)
        self.assertAlmostEqual(float(pred.iloc[-1]["score"]), float(again.iloc[-1]["score"]))

    def test_mixed_datetime_resolution(self):
        bars = self.bars.copy()
        bench = self.bench.copy()
        bars["date"] = pd.to_datetime(bars["date"]).astype("datetime64[us]")
        bench["date"] = pd.to_datetime(bench["date"]).astype("datetime64[s]")
        features = compute_features(bars, bench)
        self.assertGreater(len(features), 1500)
        model = create_model("sm701")
        pred = model.predict(bars, {}, ModelContext(benchmark=bench))
        self.assertFalse(pred.empty)

    def test_short_history_returns_empty(self):
        model = create_model("sm701")
        pred = model.predict(self.bars.head(20), {}, self.context)
        self.assertTrue(pred.empty)


if __name__ == "__main__":
    unittest.main()
