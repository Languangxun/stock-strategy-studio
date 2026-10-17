from __future__ import annotations

import importlib.util
import tarfile
import tempfile
import unittest
from pathlib import Path

from core.backtest import BacktestConfig
from core.quant_generator import generate_quant_project
from core.remote_deploy import _archive, resolve_remote_path
from core.runner import RunRequest


ROOT = Path(__file__).resolve().parent.parent
HAS_AKQUANT = importlib.util.find_spec("akquant") is not None


class QuantGeneratorTest(unittest.TestCase):
    def test_generate_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = RunRequest(
                symbol="sz000001",
                start="2024-01-01",
                end="2026-10-16",
                strategy="dual_ma",
                config=BacktestConfig(),
                offline=True,
                benchmark_symbol="sh000001",
            )
            project = generate_quant_project(request, tmp)
            self.assertTrue(project.path.exists())
            for name in (
                "config.json", "data/bars.csv", "data/signals.csv",
                "quant_app/strategy.py", "quant_app/backtest.py",
                "requirements.txt", "run.sh", "README.md",
            ):
                self.assertTrue((project.path / name).exists(), name)
            self.assertIn("akquant==", (project.path / "requirements.txt").read_text())
            self.assertIn("SignalReplayStrategy", (project.path / "quant_app" / "strategy.py").read_text())
            archive = Path(tmp) / "deploy.tar.gz"
            _archive(project.path, archive)
            with tarfile.open(archive) as tar:
                names = tar.getnames()
            self.assertIn("run.sh", names)
            self.assertFalse(any(name.startswith("output/") for name in names))

    def test_remote_path_resolution(self):
        self.assertEqual(resolve_remote_path("/home/demo", "quant"), "/home/demo/quant")
        self.assertEqual(resolve_remote_path("/home/demo", "~/quant"), "/home/demo/quant")
        self.assertEqual(resolve_remote_path("/home/demo", "/opt/quant"), "/opt/quant")
        self.assertEqual(resolve_remote_path("/home/demo", ""), "/home/demo/stock_strategy_quant")

    @unittest.skipUnless(HAS_AKQUANT, "本机未安装 akquant，跳过生成项目执行测试")
    def test_generated_akquant_project_runs(self):
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            request = RunRequest(
                symbol="sz000001",
                start="2024-01-01",
                end="2026-10-16",
                strategy="dual_ma",
                config=BacktestConfig(),
                offline=True,
                benchmark_symbol="sh000001",
            )
            project = generate_quant_project(request, tmp)
            check = subprocess.run(
                [sys.executable, "-m", "quant_app.backtest", "--check"],
                cwd=project.path,
                capture_output=True,
                text=True,
                timeout=90,
            )
            self.assertEqual(check.returncode, 0, check.stderr)
            run = subprocess.run(
                [sys.executable, "-m", "quant_app.backtest", "--no-progress"],
                cwd=project.path,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((project.path / "output" / "backtest_result.txt").exists())


if __name__ == "__main__":
    unittest.main()
