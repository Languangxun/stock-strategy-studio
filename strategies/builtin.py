from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from core.indicators import normalized_strength, rolling_high, rolling_low, rsi, sma, volume_ratio
from core.model_api import ModelError
from core.params import ParamSpec, StrategyMeta
from core.strategy_api import BaseStrategy, StrategyContext, StrategyError, empty_signals
from strategies.registry import register_strategy


def _index(bars: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(bars["date"]).dt.normalize())


def _signals(bars: pd.DataFrame) -> pd.DataFrame:
    return empty_signals(_index(bars))


@register_strategy
class DualMAStrategy(BaseStrategy):
    meta = StrategyMeta(
        slug="dual_ma",
        name="双均线趋势",
        description="快慢均线金叉入场、死叉或跌破慢线离场，可选 RSI 与量能过滤。",
        params=(
            ParamSpec("fast_ma", "快线周期", "int", 10, 2, 120, 1),
            ParamSpec("slow_ma", "慢线周期", "int", 30, 5, 250, 1),
            ParamSpec("rsi_window", "RSI 周期", "int", 14, 2, 60, 1),
            ParamSpec("rsi_buy_max", "买入 RSI 上限", "float", 100.0, 0.0, 100.0, 1.0, help="100 表示不启用"),
            ParamSpec("rsi_sell_min", "卖出 RSI 下限", "float", 100.0, 0.0, 100.0, 1.0, help="100 表示不启用"),
            ParamSpec("use_volume_filter", "启用放量过滤", "bool", False),
            ParamSpec("min_volume_ratio", "最小量比", "float", 1.0, 0.0, 10.0, 0.1),
            ParamSpec("exit_below_slow", "跌破慢线离场", "bool", True),
        ),
    )

    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        out = _signals(bars)
        if len(bars) < max(int(params["slow_ma"]), int(params["rsi_window"])) + 2:
            return out
        close = pd.Series(bars["close"].astype(float).to_numpy(), index=out.index)
        volume = pd.Series(bars["volume"].astype(float).to_numpy(), index=out.index)
        fast_n = int(params["fast_ma"])
        slow_n = int(params["slow_ma"])
        if fast_n >= slow_n:
            raise StrategyError("快线周期必须小于慢线周期")
        fast = sma(close, fast_n)
        slow = sma(close, slow_n)
        strength = normalized_strength(fast, slow)
        out["score"] = strength.fillna(0.0)
        cross_up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
        cross_down = (fast < slow) & (fast.shift(1) >= slow.shift(1))
        entry = cross_up & (close > slow)
        if params["rsi_buy_max"] < 100:
            entry &= rsi(close, int(params["rsi_window"])) <= float(params["rsi_buy_max"])
        if params["use_volume_filter"]:
            entry &= volume_ratio(volume, 5) >= float(params["min_volume_ratio"])
        exit_ = cross_down
        if params["exit_below_slow"]:
            exit_ = exit_ | (close < slow)
        if params["rsi_sell_min"] < 100:
            exit_ = exit_ | (rsi(close, int(params["rsi_window"])) >= float(params["rsi_sell_min"]))
        out["entry"] = entry.fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        out.loc[out["entry"], "entry_reason"] = (
            f"MA{fast_n} 上穿 MA{slow_n}"
        )
        out.loc[out["exit"], "exit_reason"] = f"MA{fast_n}/MA{slow_n} 转弱"
        return out


@register_strategy
class BreakoutStrategy(BaseStrategy):
    meta = StrategyMeta(
        slug="breakout",
        name="通道突破",
        description="收盘突破前 N 日高点入场，跌破前 M 日低点离场，可选量能确认。",
        params=(
            ParamSpec("entry_window", "突破周期", "int", 20, 3, 250, 1),
            ParamSpec("exit_window", "离场周期", "int", 10, 2, 250, 1),
            ParamSpec("use_volume_filter", "启用放量过滤", "bool", True),
            ParamSpec("min_volume_ratio", "最小量比", "float", 1.2, 0.0, 10.0, 0.1),
        ),
    )

    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        out = _signals(bars)
        entry_n = int(params["entry_window"])
        exit_n = int(params["exit_window"])
        if len(bars) < entry_n + 2:
            return out
        close = pd.Series(bars["close"].astype(float).to_numpy(), index=out.index)
        volume = pd.Series(bars["volume"].astype(float).to_numpy(), index=out.index)
        upper = rolling_high(close, entry_n)
        lower = rolling_low(close, exit_n)
        entry = close > upper
        if params["use_volume_filter"]:
            entry &= volume_ratio(volume, 5) >= float(params["min_volume_ratio"])
        exit_ = close < lower
        out["entry"] = entry.fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        base = np.where(upper > 0, close / upper - 1.0, 0.0)
        out["score"] = pd.Series(50.0 + 50.0 * np.tanh(base * 50.0), index=out.index).clip(0, 100)
        out.loc[out["entry"], "entry_reason"] = f"突破 {entry_n} 日高点"
        out.loc[out["exit"], "exit_reason"] = f"跌破 {exit_n} 日低点"
        return out


@register_strategy
class Sm701Strategy(BaseStrategy):
    meta = StrategyMeta(
        slug="sm701",
        name="SM701 概率模型",
        description="调用可插拔预测模型，用上涨概率阈值生成买卖信号，可选大盘 MA20 过滤。",
        params=(
            ParamSpec("model", "预测模型", "model", "sm701", choices=("sm701",)),
            ParamSpec("buy_threshold", "买入概率阈值", "float", 0.55, 0.50, 0.95, 0.01),
            ParamSpec("sell_threshold", "卖出概率阈值", "float", 0.45, 0.05, 0.70, 0.01),
            ParamSpec("market_filter", "大盘 MA20 过滤", "bool", False),
        ),
        requires_model=True,
        default_model="sm701",
        min_bars=65,
    )

    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        out = _signals(bars)
        if context.model is None:
            raise StrategyError("当前策略需要预测模型，但未加载模型")
        model_slug = str(params.get("model") or self.meta.default_model or "")
        if context.model.meta.slug != model_slug:
            raise StrategyError(f"模型不匹配: 需要 {model_slug}，实际 {context.model.meta.slug}")
        if context.model_bars is None or context.model_bars.empty:
            raise StrategyError("缺少模型所需的行情数据")
        try:
            pred = context.model.predict(context.model_bars, {}, context.model_context)
        except ModelError as exc:
            raise StrategyError(str(exc)) from exc
        if pred is None or pred.empty:
            return out
        pred = pred.copy()
        pred["date"] = pd.to_datetime(pred["date"]).dt.normalize().astype("datetime64[ns]")
        prob = pred.drop_duplicates("date", keep="last").set_index("date")["score"].astype(float).clip(0.0, 1.0)
        reason_map = pred.drop_duplicates("date", keep="last").set_index("date").get("reason", pd.Series("", index=pred["date"]))
        dates = _index(bars)
        aligned = prob.reindex(dates).ffill(limit=5).clip(0.0, 1.0)
        aligned_reason = reason_map.reindex(dates).ffill(limit=5)
        entry = aligned >= float(params["buy_threshold"])
        exit_ = aligned <= float(params["sell_threshold"])
        if params["market_filter"] and context.benchmark is not None and not context.benchmark.empty:
            bench = context.benchmark.copy()
            bench["date"] = pd.to_datetime(bench["date"]).dt.normalize().astype("datetime64[ns]")
            bench = bench.sort_values("date").drop_duplicates("date", keep="last")
            bench["ma20"] = bench["close"].rolling(20, min_periods=20).mean()
            market = pd.merge_asof(
                pd.DataFrame({"date": dates}).sort_values("date"),
                bench[["date", "close", "ma20"]].sort_values("date"),
                on="date",
                direction="backward",
            ).set_index("date")
            above = (market["close"] > market["ma20"]).reindex(dates).fillna(False)
            entry &= above.values
            exit_ |= (~above).values
        out["entry"] = entry.fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        out["score"] = (aligned.fillna(0.0) * 100.0).values
        out.loc[out["entry"], "entry_reason"] = aligned_reason[out["entry"]].fillna("模型概率达标")
        out.loc[out["exit"], "exit_reason"] = "模型概率低于卖出阈值"
        return out
