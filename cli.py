from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from core.backtest import BacktestConfig
from core.runner import RunRequest, execute, export_output
from models.registry import list_models
from strategies.registry import get_strategy_meta, list_strategies


def parse_params(items: list[str]) -> dict:
    params: dict = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"参数格式应为 key=value: {item}")
        key, value = item.split("=", 1)
        params[key.strip()] = value.strip()
    return params


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="股票策略工坊命令行回测")
    parser.add_argument("--symbol", default="sz000001", help="股票代码，如 sz000001")
    parser.add_argument("--start", default=str(date.today() - timedelta(days=365 * 3)))
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument("--strategy", default="dual_ma")
    parser.add_argument("--param", action="append", default=[], help="策略参数 key=value，可重复")
    parser.add_argument("--benchmark", default="sh000001")
    parser.add_argument("--adjust", default="qfq", choices=("qfq", "hfq"))
    parser.add_argument("--cash", type=float, default=100000.0)
    parser.add_argument("--commission-pct", type=float, default=0.025)
    parser.add_argument("--stamp-pct", type=float, default=0.05)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--position-pct", type=float, default=95.0)
    parser.add_argument("--stop-loss-pct", type=float, default=0.0)
    parser.add_argument("--take-profit-pct", type=float, default=0.0)
    parser.add_argument("--max-hold-days", type=int, default=0)
    parser.add_argument("--offline", action="store_true", help="不访问网络，使用缓存/内置示例")
    parser.add_argument("--export", type=Path, help="导出目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    parser.add_argument("--list-strategies", action="store_true")
    parser.add_argument("--list-models", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.list_strategies:
        for meta in list_strategies():
            print(f"{meta.slug:16s} {meta.name} - {meta.description}")
        return
    if args.list_models:
        for meta in list_models():
            print(f"{meta.slug:16s} {meta.name} v{meta.version} - {meta.description}")
        return
    meta = get_strategy_meta(args.strategy)
    if meta is None:
        raise SystemExit(f"未知策略: {args.strategy}，可用 --list-strategies 查看")
    params = {spec.key: spec.default for spec in meta.params}
    params.update(parse_params(args.param))
    config = BacktestConfig(
        initial_cash=args.cash,
        commission_rate=args.commission_pct / 100.0,
        stamp_tax_rate=args.stamp_pct / 100.0,
        slippage_bps=args.slippage_bps,
        position_pct=args.position_pct / 100.0,
        stop_loss_pct=args.stop_loss_pct,
        take_profit_pct=args.take_profit_pct,
        max_hold_days=args.max_hold_days,
    )
    request = RunRequest(
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        strategy=args.strategy,
        strategy_params=params,
        config=config,
        benchmark_symbol=args.benchmark,
        trade_adjust=args.adjust,
        offline=args.offline,
    )
    output = execute(request, progress=lambda message: print(f"[数据] {message}"))
    metrics = output.result.metrics
    if args.export:
        report = export_output(output, args.export)
        print(f"报告: {report}")
    if args.json:
        payload = {
            "symbol": output.symbol,
            "strategy": output.strategy_meta.slug,
            "model": output.model_meta.slug if output.model_meta else None,
            "metrics": metrics,
            "sources": output.data_sources,
            "warnings": output.warnings,
            "latest_signal": output.latest_signal,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    print("\n回测结果")
    print(f"  股票: {output.symbol}")
    print(f"  策略: {output.strategy_meta.name}")
    if output.model_meta:
        print(f"  模型: {output.model_meta.name}")
    print(f"  区间: {metrics['start']} ~ {metrics['end']}")
    print(f"  总收益: {metrics['total_return_pct']:.2f}%")
    print(f"  年化: {metrics['annual_return_pct']:.2f}%")
    print(f"  最大回撤: {metrics['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe: {metrics['sharpe']:.3f}")
    print(f"  交易: {metrics['trade_count']} 笔 · 胜率 {metrics['win_rate_pct']:.2f}%")
    if metrics.get("benchmark_return_pct") is not None:
        print(f"  基准: {metrics['benchmark_return_pct']:.2f}% · 超额 {metrics['excess_return_pct']:.2f}%")
    if output.latest_signal:
        latest = output.latest_signal
        print(f"  最新: {latest['date']} 评分 {latest['score']:.1f} · 买点 {latest['entry']} · 卖点 {latest['exit']}")
    for warning in output.warnings:
        print(f"  [提示] {warning}")
    if not output.result.trades.empty:
        print("\n最近交易")
        print(output.result.trades.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()
