from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from core.backtest import BacktestConfig, run_backtest
from core.strategy_api import empty_signals


def make_bars(n: int = 80, start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    base = np.linspace(10.0, 18.0, n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base * 1.003,
            "volume": np.full(n, 100000.0),
            "amount": np.full(n, 1e7),
        }
    )


def make_bench(n: int = 80, start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start, periods=n)
    base = np.linspace(3000.0, 3300.0, n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base,
            "volume": np.full(n, 1e8),
            "amount": np.full(n, 1e9),
        }
    )


class BacktestTest(unittest.TestCase):
    def test_t_plus_one_and_execution_prices(self):
        bars = make_bars()
        signals = empty_signals(pd.DatetimeIndex(bars["date"]))
        signals.loc[bars["date"].iloc[5], "entry"] = True
        signals.loc[bars["date"].iloc[5], "entry_reason"] = "测试买入"
        signals.loc[bars["date"].iloc[10], "exit"] = True
        signals.loc[bars["date"].iloc[10], "exit_reason"] = "测试卖出"
        config = BacktestConfig(
            initial_cash=100000.0,
            commission_rate=0.00025,
            min_commission=5.0,
            stamp_tax_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_bps=10.0,
            stop_loss_pct=0.0,
            close_at_end=True,
            start_date=str(bars["date"].iloc[0].date()),
            end_date=str(bars["date"].iloc[-1].date()),
        )
        result = run_backtest(bars, signals, make_bench(), config)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades.iloc[0]
        self.assertEqual(pd.Timestamp(trade["entry_date"]), bars["date"].iloc[6])
        self.assertEqual(pd.Timestamp(trade["exit_date"]), bars["date"].iloc[11])
        self.assertEqual(int(trade["shares"]) % 100, 0)
        expected_entry = float(bars["open"].iloc[6]) * 1.001
        expected_exit = float(bars["open"].iloc[11]) * 0.999
        self.assertAlmostEqual(float(trade["entry_price"]), expected_entry, places=6)
        self.assertAlmostEqual(float(trade["exit_price"]), expected_exit, places=6)
        self.assertGreater(float(trade["pnl"]), 0)
        self.assertGreater(float(trade["exit_fee"]), float(trade["entry_fee"]))
        self.assertAlmostEqual(
            float(result.equity["equity"].iloc[-1]),
            float(result.equity["cash"].iloc[-1]),
            places=6,
        )
        self.assertAlmostEqual(
            float(result.equity["equity"].iloc[-1]),
            config.initial_cash + float(trade["pnl"]),
            places=6,
        )
        self.assertEqual(result.metrics["trade_count"], 1)
        self.assertGreater(result.metrics["total_return_pct"], 0)
        self.assertGreater(result.metrics["benchmark_return_pct"], 0)

    def test_last_day_buy_not_sold_same_day(self):
        bars = make_bars()
        signals = empty_signals(pd.DatetimeIndex(bars["date"]))
        signals.loc[bars["date"].iloc[-2], "entry"] = True
        config = BacktestConfig(
            initial_cash=100000.0,
            close_at_end=True,
            start_date=str(bars["date"].iloc[0].date()),
            end_date=str(bars["date"].iloc[-1].date()),
        )
        result = run_backtest(bars, signals, make_bench(), config)
        self.assertEqual(len(result.trades), 0)
        self.assertIsNotNone(result.open_position)
        self.assertTrue(any("期末仍有持仓" in warning for warning in result.warnings))

    def test_stop_loss(self):
        bars = make_bars()
        bars.loc[8:, ["open", "high", "low", "close"]] *= 0.80
        signals = empty_signals(pd.DatetimeIndex(bars["date"]))
        signals.loc[bars["date"].iloc[5], "entry"] = True
        config = BacktestConfig(
            initial_cash=100000.0,
            close_at_end=True,
            stop_loss_pct=5.0,
            start_date=str(bars["date"].iloc[0].date()),
            end_date=str(bars["date"].iloc[-1].date()),
        )
        result = run_backtest(bars, signals, None, config)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades.iloc[0]
        self.assertIn("止损", trade["exit_reason"])
        self.assertLess(float(trade["pnl"]), 0)
        self.assertEqual(pd.Timestamp(trade["exit_date"]), bars["date"].iloc[9])

    def test_position_size_and_cash_never_negative(self):
        bars = make_bars()
        signals = empty_signals(pd.DatetimeIndex(bars["date"]))
        for idx in (5, 20, 35):
            signals.loc[bars["date"].iloc[idx], "entry"] = True
            signals.loc[bars["date"].iloc[idx + 5], "exit"] = True
        config = BacktestConfig(
            initial_cash=30000.0,
            position_pct=1.0,
            close_at_end=True,
            start_date=str(bars["date"].iloc[0].date()),
            end_date=str(bars["date"].iloc[-1].date()),
        )
        result = run_backtest(bars, signals, None, config)
        self.assertTrue((result.equity["cash"] >= 0).all())
        self.assertTrue((result.equity["equity"] > 0).all())
        for shares in result.trades["shares"]:
            self.assertEqual(int(shares) % 100, 0)


if __name__ == "__main__":
    unittest.main()
