from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from core.backtest import BacktestConfig, run_backtest, run_backtest_native
from core.params import normalize_params
from core.rust_engine import available
from core.strategy_api import StrategyContext
from strategies.registry import create_strategy, get_strategy_meta


ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(available(), "Rust 回测扩展未构建")
class NativeBacktestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = pd.read_csv(ROOT / "sample_data" / "sz000001_qfq.csv")
        cls.bench = pd.read_csv(ROOT / "sample_data" / "sh000001.csv")
        meta = get_strategy_meta("dual_ma")
        cls.signals = create_strategy("dual_ma").generate(
            cls.bars,
            normalize_params(meta.params, meta.defaults()),
            StrategyContext(benchmark=cls.bench),
        )

    def _compare(self, config):
        py = run_backtest(self.bars, self.signals, self.bench, config)
        native = run_backtest_native(self.bars, self.signals, self.bench, config)
        self.assertEqual(len(py.trades), len(native.trades))
        for key in ("total_return_pct", "annual_return_pct", "max_drawdown_pct", "sharpe", "trade_count", "win_rate_pct"):
            self.assertAlmostEqual(py.metrics[key], native.metrics[key], places=7, msg=key)
        if len(py.trades):
            self.assertTrue(
                py.trades[["entry_date", "exit_date", "shares", "pnl", "exit_reason"]]
                .reset_index(drop=True)
                .equals(native.trades[["entry_date", "exit_date", "shares", "pnl", "exit_reason"]].reset_index(drop=True))
            )

    def test_default_config(self):
        self._compare(BacktestConfig(start_date="2023-01-03", end_date="2026-10-16"))

    def test_risk_controls(self):
        self._compare(
            BacktestConfig(
                start_date="2023-01-03",
                end_date="2026-10-16",
                stop_loss_pct=5.0,
                take_profit_pct=12.0,
                max_hold_days=15,
            )
        )

    def test_no_end_close(self):
        self._compare(
            BacktestConfig(
                start_date="2023-01-03",
                end_date="2026-10-16",
                close_at_end=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
