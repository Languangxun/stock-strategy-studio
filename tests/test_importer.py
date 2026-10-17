from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.datafeed import CacheStore
from core.importer import import_paths


ROOT = Path(__file__).resolve().parent.parent


class ImporterTest(unittest.TestCase):
    def test_import_csv_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.db"
            report = import_paths(
                [
                    ROOT / "sample_data" / "sz000001_qfq.csv",
                    ROOT / "sample_data" / "sz000001_hfq.csv",
                ],
                cache_path,
                "auto",
            )
            self.assertGreater(report.rows, 4000)
            self.assertFalse(report.errors)
            store = CacheStore(cache_path)
            summary = store.summary()
            self.assertIn("qfq", summary)
            self.assertIn("hfq", summary)
            self.assertEqual(summary["qfq"]["symbols"], 1)
            self.assertGreaterEqual(summary["qfq"]["bars"], 2000)

    def test_import_sqlite_daily_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            con = sqlite3.connect(source)
            con.execute(
                "CREATE TABLE daily_bars(code TEXT, date TEXT, open REAL, high REAL, "
                "low REAL, close REAL, vol REAL)"
            )
            con.executemany(
                "INSERT INTO daily_bars VALUES(?,?,?,?,?,?,?)",
                [
                    ("sz000725", "20240102", 1.0, 1.1, 0.9, 1.05, 100),
                    ("sz000725", "20240103", 1.05, 1.2, 1.0, 1.1, 120),
                    ("600519", "2024-01-02", 100, 110, 99, 105, 200),
                ],
            )
            con.commit()
            con.close()
            cache_path = Path(tmp) / "cache.db"
            report = import_paths([source], cache_path, "hfq")
            self.assertFalse(report.errors, report.errors)
            self.assertEqual(report.symbols, 2)
            store = CacheStore(cache_path)
            symbols = set(store.market_symbols("hfq", __import__("pandas").Timestamp("2020-01-01"), __import__("pandas").Timestamp("2030-01-01"), 1)["symbol"])
            self.assertEqual(symbols, {"sz000725", "sh600519"})


if __name__ == "__main__":
    unittest.main()
