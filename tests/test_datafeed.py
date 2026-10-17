from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.datafeed import CacheStore, DataFeed, DataError, parse_index, parse_symbol


ROOT = Path(__file__).resolve().parent.parent


class DataFeedTest(unittest.TestCase):
    def test_parse_symbol(self):
        self.assertEqual(parse_symbol("000001").full_code, "sz000001")
        self.assertEqual(parse_symbol("600519").full_code, "sh600519")
        self.assertEqual(parse_symbol("sz300750").full_code, "sz300750")
        self.assertEqual(parse_symbol("830799").full_code, "bj830799")
        self.assertEqual(parse_index("000001"), "sh000001")
        self.assertEqual(parse_index("399001"), "sz399001")
        with self.assertRaises(DataError):
            parse_symbol("123")

    def test_offline_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            feed = DataFeed(
                cache_path=Path(tmp) / "cache.db",
                sample_dir=ROOT / "sample_data",
                offline=True,
            )
            bars = feed.load_stock("sz000001", "2023-01-01", "2026-10-16", adjust="qfq")
            self.assertGreater(len(bars), 800)
            self.assertTrue(bars.attrs.get("sample"))
            self.assertEqual(list(bars.columns), ["date", "open", "high", "low", "close", "volume", "amount"])
            bench = feed.load_index("sh000001", "2023-01-01", "2026-10-16")
            self.assertGreater(len(bench), 800)
            self.assertIn("warning", bench.attrs)

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CacheStore(Path(tmp) / "cache.db")
            df = pd.DataFrame(
                {
                    "date": pd.to_datetime(["2026-01-05", "2026-01-06"]),
                    "open": [10.0, 10.5],
                    "high": [10.8, 11.0],
                    "low": [9.9, 10.2],
                    "close": [10.5, 10.9],
                    "volume": [1000, 1200],
                    "amount": [1e6, 1.2e6],
                }
            )
            df.attrs["source"] = "test"
            store.write_bars("sz000001", "qfq", df)
            min_date, max_date = store.coverage("sz000001", "qfq")
            self.assertEqual(str(min_date.date()), "2026-01-05")
            self.assertEqual(str(max_date.date()), "2026-01-06")
            loaded = store.read_bars(
                "sz000001",
                "qfq",
                pd.Timestamp("2026-01-01"),
                pd.Timestamp("2026-01-31"),
            )
            self.assertEqual(len(loaded), 2)
            self.assertAlmostEqual(float(loaded.iloc[-1]["close"]), 10.9)
            self.assertTrue(bool(loaded.attrs.get("cached", False)))


if __name__ == "__main__":
    unittest.main()
