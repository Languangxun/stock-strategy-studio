from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from core.backtest import BacktestConfig, BacktestResult, run_backtest_auto
from core.datafeed import DataError, DataFeed
from core.model_api import ModelContext, ModelError
from core.params import ModelMeta, StrategyMeta, normalize_params
from core.strategy_api import StrategyContext, StrategyError
from models.registry import create_model
from strategies.registry import create_strategy, get_strategy_meta


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SAMPLE_DIR = PROJECT_ROOT / "sample_data"


@dataclass
class RunRequest:
    symbol: str
    start: str
    end: str
    strategy: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    benchmark_symbol: str = "sh000001"
    trade_adjust: str = "qfq"
    offline: bool = False
    allow_sample: bool = True


@dataclass
class RunOutput:
    symbol: str
    strategy_meta: StrategyMeta
    model_meta: ModelMeta | None
    result: BacktestResult
    bars: pd.DataFrame
    benchmark: pd.DataFrame | None
    warnings: list[str] = field(default_factory=list)
    data_sources: dict[str, str] = field(default_factory=dict)
    latest_signal: dict[str, Any] | None = None


def _summary_signal(bars: pd.DataFrame, signals: pd.DataFrame) -> dict[str, Any] | None:
    if bars.empty or signals.empty:
        return None
    row = signals.iloc[-1]
    return {
        "date": str(pd.Timestamp(bars["date"].iloc[-1]).date()),
        "close": float(bars["close"].iloc[-1]),
        "entry": bool(row.get("entry", False)),
        "exit": bool(row.get("exit", False)),
        "score": float(row.get("score", 0.0)),
        "entry_reason": str(row.get("entry_reason", "")),
        "exit_reason": str(row.get("exit_reason", "")),
    }


def execute(
    request: RunRequest,
    progress: Callable[[str], None] | None = None,
) -> RunOutput:
    progress = progress or (lambda _message: None)
    meta = get_strategy_meta(request.strategy)
    if meta is None:
        raise StrategyError(f"未知策略: {request.strategy}")
    params = normalize_params(meta.params, request.strategy_params)
    strategy = create_strategy(request.strategy)
    start = pd.Timestamp(request.start).normalize()
    end = pd.Timestamp(request.end).normalize()
    if start >= end:
        raise ValueError("开始日期必须早于结束日期")
    config = replace(
        request.config,
        start_date=str(start.date()),
        end_date=str(end.date()),
    )
    warmup_start = start - pd.Timedelta(days=420)
    data = DataFeed(
        cache_path=DATA_DIR / "cache.db",
        sample_dir=SAMPLE_DIR,
        offline=request.offline,
        progress=progress,
    )
    warnings: list[str] = []
    sources: dict[str, str] = {}
    progress(f"加载交易行情 {request.symbol} ({request.trade_adjust}) ...")
    bars = data.load_stock(
        request.symbol,
        warmup_start,
        end,
        adjust=request.trade_adjust,
        allow_sample=request.allow_sample,
    )
    if bars.attrs.get("warning"):
        warnings.append(str(bars.attrs["warning"]))
    sources["bars"] = str(bars.attrs.get("source", "akshare"))
    if len(bars) < 30:
        raise DataError(f"行情不足 30 根，无法回测（当前 {len(bars)} 根）")
    progress(f"交易行情 {len(bars)} 根 · {sources['bars']}")
    benchmark: pd.DataFrame | None = None
    try:
        progress(f"加载基准 {request.benchmark_symbol} ...")
        benchmark = data.load_index(
            request.benchmark_symbol,
            warmup_start,
            end,
            allow_sample=request.allow_sample,
        )
        if benchmark.attrs.get("warning"):
            warnings.append(str(benchmark.attrs["warning"]))
        sources["benchmark"] = str(benchmark.attrs.get("source", "akshare"))
        progress(f"基准 {len(benchmark)} 根 · {sources['benchmark']}")
    except Exception as exc:
        warnings.append(f"基准加载失败: {exc}")
        benchmark = None
    model = None
    model_meta = None
    model_bars = None
    model_context = ModelContext(benchmark=benchmark)
    if meta.requires_model:
        model_slug = str(params.get("model") or meta.default_model or "")
        if not model_slug:
            raise StrategyError("策略未配置默认模型")
        try:
            progress(f"加载预测模型 {model_slug} ...")
            model = create_model(model_slug)
        except Exception as exc:
            raise ModelError(f"模型加载失败: {exc}") from exc
        if not model.available():
            raise ModelError(f"模型 {model_slug} 不可用，请检查 artifacts 目录")
        model_meta = model.meta
        progress(f"模型 {model.meta.name} · {model.meta.version} 已加载")
        if model.meta.adjust != request.trade_adjust:
            progress(f"加载模型行情 {request.symbol} ({model.meta.adjust}) ...")
            model_bars = data.load_stock(
                request.symbol,
                warmup_start,
                end,
                adjust=model.meta.adjust,
                allow_sample=request.allow_sample,
            )
            if model_bars.attrs.get("warning"):
                warnings.append(str(model_bars.attrs["warning"]))
            sources["model_bars"] = str(model_bars.attrs.get("source", "akshare"))
        else:
            model_bars = bars
            sources["model_bars"] = sources["bars"]
        model_context = ModelContext(benchmark=benchmark, universe={request.symbol: model_bars})
    context = StrategyContext(
        benchmark=benchmark,
        model_bars=model_bars,
        model=model,
        model_context=model_context,
    )
    progress(f"生成策略信号 · {meta.name} ...")
    signals = strategy.generate(bars, params, context)
    progress("执行回测 ...")
    result = run_backtest_auto(bars, signals, benchmark, config)
    trade_start = start
    trade_end = end
    display_bars = bars[(bars["date"] >= trade_start) & (bars["date"] <= trade_end)].reset_index(drop=True)
    if len(display_bars) < 2:
        warnings.append("回测区间行情过少，结果参考性有限")
    latest = _summary_signal(display_bars, result.signals)
    warnings.extend(result.warnings)
    progress(
        f"回测完成 · 交易 {result.metrics['trade_count']} 笔 · "
        f"收益 {result.metrics['total_return_pct']:.2f}%"
    )
    return RunOutput(
        symbol=request.symbol,
        strategy_meta=meta,
        model_meta=model_meta,
        result=result,
        bars=display_bars,
        benchmark=benchmark,
        warnings=list(dict.fromkeys(warnings)),
        data_sources=sources,
        latest_signal=latest,
    )


def export_output(output: RunOutput, directory: str | Path) -> Path:
    import json

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"backtest_{output.symbol.replace(':', '')}_{stamp}"
    output.result.trades.to_csv(target / f"{prefix}_trades.csv", index=False, encoding="utf-8-sig")
    output.result.equity.to_csv(target / f"{prefix}_equity.csv", index=False, encoding="utf-8-sig")
    config_dict = {
        key: value
        for key, value in output.result.config.__dict__.items()
    }
    summary = {
        "symbol": output.symbol,
        "strategy": output.strategy_meta.name,
        "strategy_slug": output.strategy_meta.slug,
        "model": output.model_meta.name if output.model_meta else None,
        "data_sources": output.data_sources,
        "config": config_dict,
        "metrics": {
            key: (None if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))) else value)
            for key, value in output.result.metrics.items()
        },
        "warnings": output.warnings,
        "latest_signal": output.latest_signal,
    }
    with open(target / f"{prefix}_summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
    lines = [
        f"# 回测报告 · {output.symbol} · {output.strategy_meta.name}",
        "",
        f"- 区间：{output.result.metrics['start']} ~ {output.result.metrics['end']}",
        f"- 初始资金：{output.result.config.initial_cash:,.2f}",
        f"- 期末权益：{output.result.metrics['final_equity']:,.2f}",
        f"- 总收益：{output.result.metrics['total_return_pct']:.2f}%",
        f"- 年化收益：{output.result.metrics['annual_return_pct']:.2f}%",
        f"- 最大回撤：{output.result.metrics['max_drawdown_pct']:.2f}%",
        f"- Sharpe：{output.result.metrics['sharpe']:.3f}",
        f"- 交易次数：{output.result.metrics['trade_count']}",
        f"- 胜率：{output.result.metrics['win_rate_pct']:.2f}%",
        "",
        "## 交易明细",
        "",
        "```",
        output.result.trades.to_string(index=False) if not output.result.trades.empty else "无成交",
        "```",
        "",
        "## 数据来源",
        "",
        "```",
        json.dumps(output.data_sources, ensure_ascii=False, indent=2),
        "```",
    ]
    if output.warnings:
        lines.extend(["", "## 提示", ""] + [f"- {item}" for item in output.warnings])
    (target / f"{prefix}_report.md").write_text("\n".join(lines), encoding="utf-8")
    return target / f"{prefix}_report.md"
