from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import core.market as market
from core.backtest import BacktestConfig
from core.importer import import_paths
from core.market import MarketRequest, export_market_result, run_market


ROOT = Path(__file__).resolve().parent.parent


class MarketBacktestTest(unittest.TestCase):
    def test_full_market_local_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            import_paths(
                [
                    ROOT / "sample_data" / "sz000001_qfq.csv",
                    ROOT / "sample_data" / "sz000001_hfq.csv",
                ],
                data_dir / "cache.db",
                "auto",
            )
            with patch.object(market, "DATA_DIR", data_dir), patch.object(market, "SAMPLE_DIR", ROOT / "sample_data"):
                result = run_market(
                    MarketRequest(
                        start="2023-01-01",
                        end="2026-10-16",
                        strategy="dual_ma",
                        strategy_params={},
                        config=BacktestConfig(),
                        adjust="qfq",
                        benchmark_symbol="sh000001",
                        limit=0,
                        min_bars=120,
                        use_native=True,
                    )
                )
                self.assertEqual(result.summary["universe"], 1)
                self.assertEqual(result.summary["success"], 1)
                self.assertFalse(result.rows.empty)
                self.assertEqual(result.rows.iloc[0]["symbol"], "sz000001")
                self.assertEqual(result.summary["native"], True)
                report = export_market_result(result, Path(tmp) / "exports")
                self.assertTrue(report.exists())
                self.assertTrue(any((Path(tmp) / "exports").glob("market_backtest_*_stocks.csv")))


if __name__ == "__main__":
    unittest.main()
