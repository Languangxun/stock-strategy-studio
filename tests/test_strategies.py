from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from core.model_api import ModelContext
from core.params import normalize_params
from core.strategy_api import StrategyContext
from core.runner import RunRequest, execute
from models.registry import create_model
from strategies.registry import create_strategy, get_strategy_meta, list_strategies


ROOT = Path(__file__).resolve().parent.parent


class StrategyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = pd.read_csv(ROOT / "sample_data" / "sz000001_qfq.csv")
        cls.bars_hfq = pd.read_csv(ROOT / "sample_data" / "sz000001_hfq.csv")
        cls.bench = pd.read_csv(ROOT / "sample_data" / "sh000001.csv")

    def test_registry(self):
        slugs = {meta.slug for meta in list_strategies()}
        self.assertTrue({"dual_ma", "breakout", "sm701", "example_rsi"}.issubset(slugs))
        self.assertIsNotNone(get_strategy_meta("dual_ma"))

    def test_rule_strategies_signal_shape(self):
        context = StrategyContext(benchmark=self.bench)
        for slug in ("dual_ma", "breakout"):
            meta = get_strategy_meta(slug)
            strategy = create_strategy(slug)
            params = normalize_params(meta.params, meta.defaults())
            signals = strategy.generate(self.bars, params, context)
            self.assertEqual(len(signals), len(self.bars))
            self.assertTrue(str(signals.index.dtype).startswith("datetime64"))
            self.assertEqual(set(["entry", "exit", "score"]).issubset(signals.columns), True)
            self.assertFalse(signals["entry"].isna().any())
            self.assertFalse(signals["exit"].isna().any())
            self.assertTrue(signals["score"].between(0, 100).all())
            self.assertGreater(int(signals["entry"].sum()), 0)

    def test_plugin_strategy(self):
        meta = get_strategy_meta("example_rsi")
        strategy = create_strategy("example_rsi")
        params = normalize_params(meta.params, {**meta.defaults(), "trend_filter": False})
        signals = strategy.generate(self.bars, params, StrategyContext())
        self.assertGreater(int(signals["entry"].sum()), 0)

    def test_sm701_strategy_with_model(self):
        meta = get_strategy_meta("sm701")
        strategy = create_strategy("sm701")
        model = create_model("sm701")
        context = StrategyContext(
            benchmark=self.bench,
            model=model,
            model_bars=self.bars_hfq,
            model_context=ModelContext(benchmark=self.bench),
        )
        params = normalize_params(meta.params, meta.defaults())
        signals = strategy.generate(self.bars, params, context)
        self.assertEqual(len(signals), len(self.bars))
        self.assertGreater(int(signals["entry"].sum()), 0)
        self.assertGreater(int(signals["exit"].sum()), 0)

    def test_offline_runner(self):
        output = execute(
            RunRequest(
                symbol="sz000001",
                start="2023-01-01",
                end="2026-10-16",
                strategy="sm701",
                strategy_params={},
                offline=True,
            ),
            progress=lambda _message: None,
        )
        self.assertGreater(output.result.metrics["trade_count"], 0)
        self.assertIsNotNone(output.model_meta)
        self.assertGreater(output.result.metrics["n_days"], 900)
        self.assertEqual(len(output.bars), output.result.metrics["n_days"])
        self.assertTrue(output.latest_signal is not None)


if __name__ == "__main__":
    unittest.main()
