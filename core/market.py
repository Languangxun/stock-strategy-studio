from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from core.backtest import BacktestConfig, BacktestResult, run_backtest, run_backtest_native
from core.datafeed import CacheStore, DataFeed
from core.model_api import ModelContext, ModelError
from core.params import ModelMeta, StrategyMeta, normalize_params
from core.runner import DATA_DIR, SAMPLE_DIR
from core.rust_engine import available as native_available
from core.strategy_api import StrategyContext, StrategyError
from models.registry import create_model
from strategies.registry import create_strategy, get_strategy_meta


@dataclass
class MarketRequest:
    start: str
    end: str
    strategy: str
    strategy_params: dict[str, Any] = field(default_factory=dict)
    config: BacktestConfig = field(default_factory=BacktestConfig)
    adjust: str = "qfq"
    benchmark_symbol: str = "sh000001"
    limit: int = 0
    min_bars: int = 120
    use_native: bool = True


@dataclass
class MarketResult:
    rows: pd.DataFrame
    summary: dict[str, Any]
    failures: list[str]
    warnings: list[str]
    strategy_meta: StrategyMeta
    model_meta: ModelMeta | None
    duration_sec: float
    cancelled: bool = False
    native_used: bool = False


def _benchmark_return(benchmark: pd.DataFrame | None, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if benchmark is None or benchmark.empty:
        return None
    frame = benchmark.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame[(frame["date"] >= start) & (frame["date"] <= end)].sort_values("date")
    if len(frame) < 2:
        return None
    first = float(frame["close"].iloc[0])
    last = float(frame["close"].iloc[-1])
    if first <= 0:
        return None
    return (last / first - 1.0) * 100.0


def _aggregate(rows: pd.DataFrame, universe_count: int, failures: int, bench_return: float | None) -> dict[str, Any]:
    if rows.empty:
        return {
            "universe": universe_count,
            "success": 0,
            "failed": failures,
            "avg_return_pct": None,
            "median_return_pct": None,
            "positive_pct": None,
            "avg_drawdown_pct": None,
            "avg_sharpe": None,
            "total_trades": 0,
            "beat_benchmark_pct": None,
            "best_symbol": None,
            "best_return_pct": None,
            "worst_symbol": None,
            "worst_return_pct": None,
        }
    returns = rows["total_return_pct"].astype(float)
    drawdowns = rows["max_drawdown_pct"].astype(float)
    return {
        "universe": universe_count,
        "success": int(len(rows)),
        "failed": int(failures),
        "avg_return_pct": float(returns.mean()),
        "median_return_pct": float(returns.median()),
        "positive_pct": float((returns > 0).mean() * 100.0),
        "avg_drawdown_pct": float(drawdowns.mean()),
        "avg_sharpe": float(rows["sharpe"].astype(float).mean()),
        "total_trades": int(rows["trade_count"].sum()),
        "beat_benchmark_pct": float((rows["excess_return_pct"].astype(float) > 0).mean() * 100.0) if bench_return is not None else None,
        "best_symbol": str(rows.iloc[0]["symbol"]),
        "best_return_pct": float(rows.iloc[0]["total_return_pct"]),
        "worst_symbol": str(rows.iloc[-1]["symbol"]),
        "worst_return_pct": float(rows.iloc[-1]["total_return_pct"]),
    }


def run_market(
    request: MarketRequest,
    progress: Callable[[int, int, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MarketResult:
    progress = progress or (lambda _current, _total, _message: None)
    should_cancel = should_cancel or (lambda: False)
    meta = get_strategy_meta(request.strategy)
    if meta is None:
        raise StrategyError(f"未知策略: {request.strategy}")
    params = normalize_params(meta.params, request.strategy_params)
    strategy = create_strategy(request.strategy)
    start = pd.Timestamp(request.start).normalize()
    end = pd.Timestamp(request.end).normalize()
    if start >= end:
        raise ValueError("开始日期必须早于结束日期")
    config_base = replace(request.config, start_date=str(start.date()), end_date=str(end.date()))
    warmup_start = start - pd.Timedelta(days=420)
    cache = CacheStore(DATA_DIR / "cache.db")
    warnings: list[str] = []
    model = None
    model_meta = None
    if meta.requires_model:
        model_slug = str(params.get("model") or meta.default_model or "")
        if not model_slug:
            raise StrategyError("策略未配置默认模型")
        model = create_model(model_slug)
        if not model.available():
            raise ModelError(f"模型 {model_slug} 不可用")
        model_meta = model.meta
    native_ok = native_available()
    use_native = bool(request.use_native and native_ok)
    if request.use_native and not native_ok:
        warnings.append("Rust 加速不可用，已回退 Python 回测引擎")
    benchmark: pd.DataFrame | None = None
    try:
        feed = DataFeed(DATA_DIR / "cache.db", SAMPLE_DIR, offline=True)
        benchmark = feed.load_index(request.benchmark_symbol, warmup_start, end, allow_sample=True)
    except Exception as exc:
        warnings.append(f"基准加载失败: {exc}")
    bench_return = _benchmark_return(benchmark, start, end)
    if bench_return is None:
        warnings.append("基准区间数据不足，超额收益不可用")
    symbols = cache.market_symbols(
        request.adjust,
        warmup_start,
        end,
        max(int(request.min_bars), 2),
        request.limit if request.limit > 0 else None,
    )
    if meta.requires_model and model_meta is not None and model_meta.adjust != request.adjust and not symbols.empty:
        model_symbols = cache.market_symbols(
            model_meta.adjust,
            warmup_start,
            end,
            max(int(meta.min_bars), 2),
            None,
        )
        symbols = symbols.merge(model_symbols[["symbol"]], on="symbol", how="inner")
        if request.limit > 0:
            symbols = symbols.head(int(request.limit))
    total = len(symbols)
    if total == 0:
        warnings.append(f"本地 {request.adjust or 'raw'} 数据库中没有满足条件的 A 股行情，请先导入数据")
    progress(0, total, f"本地股票 {total} 只 · 开始全市场逐股回测")
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    rows_df = pd.DataFrame()
    cancelled = False
    started = time.time()
    for idx, symbol_row in enumerate(symbols.itertuples(index=False), start=1):
        if should_cancel():
            cancelled = True
            warnings.append("用户取消全市场回测")
            break
        symbol = str(symbol_row.symbol)
        progress(idx - 1, total, f"回测 {symbol} ({idx}/{total})")
        try:
            bars = cache.read_bars(symbol, request.adjust, warmup_start, end)
            if len(bars) < int(request.min_bars):
                failures.append(f"{symbol}: 行情不足 ({len(bars)} < {request.min_bars})")
                continue
            if bars["date"].min() > start:
                failures.append(f"{symbol}: 起始日无行情")
                continue
            if bars["date"].max() < end - pd.Timedelta(days=10):
                failures.append(f"{symbol}: 区间末缺少行情")
                continue
            model_bars = None
            model_context = ModelContext(benchmark=benchmark)
            if meta.requires_model and model is not None and model_meta is not None:
                if model_meta.adjust != request.adjust:
                    model_bars = cache.read_bars(symbol, model_meta.adjust, warmup_start, end)
                    if len(model_bars) < meta.min_bars:
                        failures.append(f"{symbol}: 缺少模型所需 {model_meta.adjust} 行情")
                        continue
                else:
                    model_bars = bars
                model_context = ModelContext(benchmark=benchmark, universe={symbol: model_bars})
            context = StrategyContext(
                benchmark=benchmark,
                model_bars=model_bars,
                model=model,
                model_context=model_context,
            )
            signals = strategy.generate(bars, params, context)
            config = replace(config_base)
            try:
                result = run_backtest_native(bars, signals, None, config) if use_native else run_backtest(bars, signals, None, config)
            except Exception as exc:
                if use_native:
                    result = run_backtest(bars, signals, None, config)
                    warnings.append(f"{symbol}: Rust 回测异常，已回退 Python ({exc})")
                else:
                    raise
            metrics = result.metrics
            total_return = float(metrics["total_return_pct"])
            excess = total_return - bench_return if bench_return is not None else np.nan
            rows.append(
                {
                    "symbol": symbol,
                    "bars": int(len(bars)),
                    "start": metrics["start"],
                    "end": metrics["end"],
                    "total_return_pct": total_return,
                    "annual_return_pct": float(metrics["annual_return_pct"]),
                    "max_drawdown_pct": float(metrics["max_drawdown_pct"]),
                    "sharpe": float(metrics["sharpe"]),
                    "trade_count": int(metrics["trade_count"]),
                    "win_rate_pct": float(metrics["win_rate_pct"]),
                    "avg_trade_pct": float(metrics["avg_trade_pct"]),
                    "exposure_pct": float(metrics["exposure_pct"]),
                    "final_equity": float(metrics["final_equity"]),
                    "benchmark_return_pct": bench_return,
                    "excess_return_pct": excess,
                }
            )
        except Exception as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
        if idx % 10 == 0 or idx == total:
            progress(idx, total, f"已完成 {idx}/{total} · 成功 {len(rows)} · 跳过 {len(failures)}")
    if rows:
        rows_df = pd.DataFrame(rows).sort_values("total_return_pct", ascending=False).reset_index(drop=True)
    summary = _aggregate(rows_df, total, len(failures), bench_return)
    summary["benchmark_return_pct"] = bench_return
    summary["adjust"] = request.adjust
    summary["native"] = use_native
    summary["duration_sec"] = time.time() - started
    progress(total, total, f"完成：成功 {summary['success']} / {total}，用时 {summary['duration_sec']:.1f}s")
    return MarketResult(
        rows=rows_df,
        summary=summary,
        failures=failures[:200],
        warnings=list(dict.fromkeys(warnings)),
        strategy_meta=meta,
        model_meta=model_meta,
        duration_sec=summary["duration_sec"],
        cancelled=cancelled,
        native_used=use_native,
    )


def export_market_result(result: MarketResult, directory: str | Path) -> Path:
    import json

    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"market_backtest_{stamp}"
    rows = result.rows.copy()
    rows.to_csv(target / f"{prefix}_stocks.csv", index=False, encoding="utf-8-sig")
    summary = {
        key: (None if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))) else value)
        for key, value in result.summary.items()
    }
    summary.update(
        {
            "strategy": result.strategy_meta.name,
            "strategy_slug": result.strategy_meta.slug,
            "model": result.model_meta.name if result.model_meta else None,
            "warnings": result.warnings,
            "failures": result.failures,
        }
    )
    with open(target / f"{prefix}_summary.json", "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, allow_nan=False)
    lines = [
        f"# 全市场逐股回测 · {result.strategy_meta.name}",
        "",
        f"- 覆盖股票：{summary.get('success')} / {summary.get('universe')}",
        f"- 失败/跳过：{summary.get('failed')}",
        f"- 平均收益：{summary.get('avg_return_pct'):.2f}%" if summary.get("avg_return_pct") is not None else "- 平均收益：--",
        f"- 中位收益：{summary.get('median_return_pct'):.2f}%" if summary.get("median_return_pct") is not None else "- 中位收益：--",
        f"- 正收益占比：{summary.get('positive_pct'):.2f}%" if summary.get("positive_pct") is not None else "- 正收益占比：--",
        f"- 平均最大回撤：{summary.get('avg_drawdown_pct'):.2f}%" if summary.get("avg_drawdown_pct") is not None else "- 平均最大回撤：--",
        f"- 基准收益：{summary.get('benchmark_return_pct'):.2f}%" if summary.get("benchmark_return_pct") is not None else "- 基准收益：--",
        f"- 引擎：{'Rust' if summary.get('native') else 'Python'}",
        "",
    ]
    if result.warnings:
        lines.extend(["## 提示", ""] + [f"- {item}" for item in result.warnings] + [""])
    (target / f"{prefix}_report.md").write_text("\n".join(lines), encoding="utf-8")
    return target / f"{prefix}_report.md"
