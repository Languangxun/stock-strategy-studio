from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.runner import RunRequest, execute, export_output


class ExportTest(unittest.TestCase):
    def test_export_offline_run(self):
        output = execute(
            RunRequest(
                symbol="sz000001",
                start="2023-01-01",
                end="2026-10-16",
                strategy="dual_ma",
                offline=True,
            ),
            progress=lambda _message: None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            report = export_output(output, tmp)
            self.assertTrue(report.exists())
            self.assertIn("回测报告", report.read_text(encoding="utf-8"))
            files = list(Path(tmp).glob("backtest_*"))
            names = [path.name for path in files]
            self.assertTrue(any(name.endswith("_trades.csv") for name in names))
            self.assertTrue(any(name.endswith("_equity.csv") for name in names))
            summary_file = next(path for path in files if path.name.endswith("_summary.json"))
            payload = json.loads(summary_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["symbol"], "sz000001")
            self.assertEqual(payload["strategy_slug"], "dual_ma")
            self.assertIn("total_return_pct", payload["metrics"])


if __name__ == "__main__":
    unittest.main()
