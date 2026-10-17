from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from core.indicator_catalog import all_indicators, compute_indicator
from core.strategy_api import StrategyContext
from strategies import custom_store
from strategies.custom import CustomRuleStrategy, normalize_definition
from strategies.custom_store import delete_definition, get_definition, list_definitions, save_definition
from strategies.registry import create_strategy, list_strategies


ROOT = Path(__file__).resolve().parent.parent


def sample_definition() -> dict:
    return {
        "slug": "",
        "name": "测试条件策略",
        "description": "放量突破",
        "entry_logic": "all",
        "exit_logic": "all",
        "entry_conditions": [
            {
                "left": {"indicator": "close", "params": {}},
                "op": "gt",
                "right": {"type": "indicator", "indicator": "ma", "params": {"period": 20}},
            },
            {
                "left": {"indicator": "volume_ratio", "params": {"period": 5}},
                "op": "ge",
                "right": {"type": "value", "value": 1.0},
            },
        ],
        "exit_conditions": [
            {
                "left": {"indicator": "close", "params": {}},
                "op": "lt",
                "right": {"type": "indicator", "indicator": "ma", "params": {"period": 20}},
            }
        ],
    }


class CustomStrategyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = pd.read_csv(ROOT / "sample_data" / "sz000001_qfq.csv")

    def test_all_indicators_compute(self):
        self.assertGreaterEqual(len(all_indicators()), 40)
        for spec in all_indicators():
            series = compute_indicator({"indicator": spec.key, "params": spec.defaults()}, self.bars)
            self.assertEqual(len(series), len(self.bars), spec.key)
            self.assertGreater(series.notna().sum(), 0, spec.key)

    def test_custom_strategy_signals(self):
        definition = normalize_definition({**sample_definition(), "slug": "custom_test"})
        strategy = CustomRuleStrategy(definition)
        signals = strategy.generate(self.bars, {}, StrategyContext())
        self.assertEqual(len(signals), len(self.bars))
        self.assertGreater(int(signals["entry"].sum()), 0)
        self.assertGreater(int(signals["exit"].sum()), 0)
        self.assertFalse(signals["entry"].isna().any())
        self.assertFalse(signals["exit"].isna().any())
        self.assertTrue(signals.loc[signals["entry"], "entry_reason"].str.len().gt(0).all())
        self.assertTrue(signals["score"].between(0, 100).all())

    def test_store_roundtrip_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom"
            with patch.object(custom_store, "CUSTOM_DIR", target):
                saved = save_definition(sample_definition())
                self.assertTrue(saved["slug"].startswith("custom_"))
                self.assertTrue((target / f"{saved['slug']}.json").exists())
                definitions, errors = list_definitions()
                self.assertFalse(errors)
                self.assertEqual(len(definitions), 1)
                loaded = get_definition(saved["slug"])
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["name"], "测试条件策略")
                delete_definition(saved["slug"])
                self.assertIsNone(get_definition(saved["slug"]))

    def test_registry_discovers_custom_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "custom"
            with patch.object(custom_store, "CUSTOM_DIR", target):
                saved = save_definition({**sample_definition(), "name": "注册表测试策略"})
                metas = list_strategies()
                found = [meta for meta in metas if meta.slug == saved["slug"]]
                self.assertTrue(found)
                self.assertTrue(found[0].name.startswith("自定义 ·"))
                strategy = create_strategy(saved["slug"])
                signals = strategy.generate(self.bars, {}, StrategyContext())
                self.assertGreater(int(signals["entry"].sum()), 0)

    def test_between_and_cross(self):
        definition = {
            "slug": "custom_test_cross",
            "name": "区间与交叉",
            "entry_conditions": [
                {
                    "left": {"indicator": "rsi", "params": {"period": 14}},
                    "op": "between",
                    "right": {"type": "value", "value": 0, "value2": 100},
                },
                {
                    "left": {"indicator": "close", "params": {}},
                    "op": "cross_above",
                    "right": {"type": "indicator", "indicator": "ma", "params": {"period": 20}},
                },
            ],
            "exit_conditions": [],
        }
        strategy = CustomRuleStrategy(definition)
        signals = strategy.generate(self.bars, {}, StrategyContext())
        self.assertGreater(int(signals["entry"].sum()), 0)
        self.assertTrue(signals["entry"].any())


if __name__ == "__main__":
    unittest.main()
