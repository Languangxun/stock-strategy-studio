from __future__ import annotations

from typing import Any

import pandas as pd

from core.indicators import rsi, sma
from core.params import ParamSpec, StrategyMeta
from core.strategy_api import BaseStrategy, StrategyContext, empty_signals
from strategies.registry import register_strategy


@register_strategy
class ExampleRsiReversal(BaseStrategy):
    meta = StrategyMeta(
        slug="example_rsi",
        name="示例插件 · RSI 反转",
        description="复制本文件即可扩展策略：RSI 超卖买入、回到中枢卖出，可选趋势过滤。",
        params=(
            ParamSpec("period", "RSI 周期", "int", 14, 2, 60, 1),
            ParamSpec("oversold", "超卖阈值", "float", 30.0, 1.0, 50.0, 1.0),
            ParamSpec("exit_level", "离场阈值", "float", 55.0, 30.0, 95.0, 1.0),
            ParamSpec("trend_filter", "只做 MA20 上方", "bool", False),
        ),
    )

    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        dates = pd.DatetimeIndex(pd.to_datetime(bars["date"]).dt.normalize())
        out = empty_signals(dates)
        close = pd.Series(bars["close"].astype(float).to_numpy(), index=out.index)
        period = int(params["period"])
        values = rsi(close, period)
        entry = values <= float(params["oversold"])
        exit_ = values >= float(params["exit_level"])
        if params["trend_filter"]:
            trend = sma(close, 20)
            entry &= close > trend
            exit_ |= close < trend
        out["entry"] = entry.fillna(False).astype(bool)
        out["exit"] = exit_.fillna(False).astype(bool)
        out["score"] = (100.0 - values).fillna(50.0).clip(0, 100)
        out.loc[out["entry"], "entry_reason"] = f"RSI{period} 超卖"
        out.loc[out["exit"], "exit_reason"] = f"RSI{period} 回升"
        return out
