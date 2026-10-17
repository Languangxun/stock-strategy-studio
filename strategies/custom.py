from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from core.indicator_catalog import compute_indicator, describe_indicator, normalize_indicator_config
from core.params import StrategyMeta
from core.strategy_api import BaseStrategy, StrategyContext, empty_signals


OPERATORS: tuple[tuple[str, str], ...] = (
    ("gt", "大于 >"),
    ("ge", "大于等于 ≥"),
    ("lt", "小于 <"),
    ("le", "小于等于 ≤"),
    ("cross_above", "上穿 / 金叉"),
    ("cross_below", "下穿 / 死叉"),
    ("between", "介于区间"),
    ("rising", "较前值上升"),
    ("falling", "较前值下降"),
)
OP_LABELS = dict(OPERATORS)
OP_TEXT = {
    "gt": ">",
    "ge": "≥",
    "lt": "<",
    "le": "≤",
    "cross_above": "上穿",
    "cross_below": "下穿",
    "rising": "较前值上升",
    "falling": "较前值下降",
}
RIGHT_KINDS: tuple[tuple[str, str], ...] = (("value", "数值"), ("indicator", "指标"))
LOGIC_LABELS = {"all": "全部满足 (AND)", "any": "任一满足 (OR)"}
LOGIC_VALUES = {label: value for value, label in LOGIC_LABELS.items()}
COMPARISON_OPS = {"gt", "ge", "lt", "le", "cross_above", "cross_below"}
MAX_CONDITIONS = 20


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def validate_right(op: str, raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if op in ("rising", "falling"):
        return None
    if op == "between":
        if raw is None:
            raise ValueError("介于区间需要填写下限和上限")
        kind = str(raw.get("type", "value"))
        if kind != "value":
            raise ValueError("介于区间目前只支持数值")
        try:
            low = float(raw.get("value"))
            high = float(raw.get("value2"))
        except (TypeError, ValueError) as exc:
            raise ValueError("介于区间需要有效的下限和上限") from exc
        if low > high:
            low, high = high, low
        return {"type": "value", "value": low, "value2": high}
    if raw is None:
        raise ValueError("当前操作符需要设置比较对象")
    kind = str(raw.get("type", "value"))
    if kind == "indicator":
        indicator = normalize_indicator_config(raw)
        if indicator is None:
            raise ValueError("右侧指标配置无效")
        return {"type": "indicator", **indicator}
    if kind != "value":
        raise ValueError("比较对象类型必须是数值或指标")
    try:
        value = float(raw.get("value"))
    except (TypeError, ValueError) as exc:
        raise ValueError("比较数值无效") from exc
    return {"type": "value", "value": value}


def normalize_condition(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("条件必须是对象")
    left = normalize_indicator_config(raw.get("left"))
    if left is None:
        raise ValueError("左侧指标配置无效")
    op = str(raw.get("op", "gt"))
    if op not in OP_LABELS:
        raise ValueError(f"未知操作符: {op}")
    right = validate_right(op, raw.get("right"))
    return {"left": left, "op": op, "right": right}


def normalize_definition(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("策略定义必须是 JSON 对象")
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("策略名称不能为空")
    if len(name) > 60:
        name = name[:60]
    entry_conditions_raw = raw.get("entry_conditions") or []
    exit_conditions_raw = raw.get("exit_conditions") or []
    if not isinstance(entry_conditions_raw, list) or not isinstance(exit_conditions_raw, list):
        raise ValueError("入场/离场条件必须是列表")
    if len(entry_conditions_raw) > MAX_CONDITIONS or len(exit_conditions_raw) > MAX_CONDITIONS:
        raise ValueError(f"入场/离场条件最多各 {MAX_CONDITIONS} 条")
    entry_conditions = [normalize_condition(item) for item in entry_conditions_raw]
    exit_conditions = [normalize_condition(item) for item in exit_conditions_raw]
    entry_logic = str(raw.get("entry_logic", "all"))
    exit_logic = str(raw.get("exit_logic", "all"))
    if entry_logic not in ("all", "any"):
        entry_logic = "all"
    if exit_logic not in ("all", "any"):
        exit_logic = "all"
    slug = str(raw.get("slug", "")).strip()
    return {
        "slug": slug,
        "name": name,
        "description": str(raw.get("description", "")).strip()[:240],
        "entry_logic": entry_logic,
        "exit_logic": exit_logic,
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "created_at": str(raw.get("created_at") or now_text()),
        "updated_at": str(raw.get("updated_at") or now_text()),
    }


def new_definition(name: str = "新建自定义策略") -> dict[str, Any]:
    return {
        "slug": "",
        "name": name,
        "description": "",
        "entry_logic": "all",
        "exit_logic": "all",
        "entry_conditions": [],
        "exit_conditions": [],
        "created_at": now_text(),
        "updated_at": now_text(),
    }


def describe_condition(condition: dict[str, Any]) -> str:
    left = describe_indicator(condition["left"])
    op = str(condition["op"])
    if op in ("rising", "falling"):
        return f"{left} {OP_TEXT[op]}"
    right = condition.get("right") or {}
    if op == "between":
        return f"{left} 介于 {float(right.get('value', 0)):g} ~ {float(right.get('value2', 0)):g}"
    if right.get("type") == "indicator":
        return f"{left} {OP_TEXT.get(op, op)} {describe_indicator(right)}"
    return f"{left} {OP_TEXT.get(op, op)} {float(right.get('value', 0)):g}"


def meta_from_definition(definition: dict[str, Any]) -> StrategyMeta:
    entry_count = len(definition.get("entry_conditions") or [])
    exit_count = len(definition.get("exit_conditions") or [])
    description = definition.get("description") or (
        f"{entry_count} 个入场条件 / {exit_count} 个离场条件 · "
        f"{'AND' if definition.get('entry_logic') == 'all' else 'OR'} 入场"
    )
    return StrategyMeta(
        slug=str(definition.get("slug", "")),
        name=f"自定义 · {definition.get('name', '未命名策略')}",
        description=description,
        params=(),
        requires_model=False,
        min_bars=65,
    )


def evaluate_condition(condition: dict[str, Any], bars: pd.DataFrame, cache: dict[str, pd.Series]) -> pd.Series:
    left_config = condition["left"]
    left_key = json.dumps(left_config, sort_keys=True, ensure_ascii=False)
    left = cache.get(left_key)
    if left is None:
        left = compute_indicator(left_config, bars)
        cache[left_key] = left
    op = condition["op"]
    if op in ("rising", "falling"):
        result = left.diff() > 0 if op == "rising" else left.diff() < 0
        return result.fillna(False).astype(bool)
    right = condition.get("right") or {}
    if op == "between":
        low = float(right.get("value"))
        high = float(right.get("value2"))
        return left.between(low, high).fillna(False).astype(bool)
    if right.get("type") == "indicator":
        right_key = json.dumps({k: right[k] for k in ("indicator", "params")}, sort_keys=True, ensure_ascii=False)
        right_series = cache.get(right_key)
        if right_series is None:
            right_series = compute_indicator(right, bars)
            cache[right_key] = right_series
    else:
        right_series = float(right.get("value", 0.0))
    if op == "gt":
        result = left > right_series
    elif op == "ge":
        result = left >= right_series
    elif op == "lt":
        result = left < right_series
    elif op == "le":
        result = left <= right_series
    elif op == "cross_above":
        result = (left > right_series) & (left.shift(1) <= (right_series.shift(1) if isinstance(right_series, pd.Series) else right_series))
    elif op == "cross_below":
        result = (left < right_series) & (left.shift(1) >= (right_series.shift(1) if isinstance(right_series, pd.Series) else right_series))
    else:
        raise ValueError(f"未知操作符: {op}")
    return result.fillna(False).astype(bool)


def evaluate_conditions(
    conditions: list[dict[str, Any]],
    logic: str,
    bars: pd.DataFrame,
    cache: dict[str, pd.Series],
) -> tuple[pd.Series, pd.DataFrame]:
    if not conditions:
        return pd.Series(False, index=bars.index), pd.DataFrame(index=bars.index)
    matrix = pd.concat(
        [evaluate_condition(condition, bars, cache) for condition in conditions],
        axis=1,
    )
    matrix.columns = range(len(conditions))
    if logic == "any":
        signal = matrix.any(axis=1)
    else:
        signal = matrix.all(axis=1)
    return signal.fillna(False).astype(bool), matrix


class CustomRuleStrategy(BaseStrategy):
    def __init__(self, definition: dict[str, Any]):
        self.definition = normalize_definition(definition)
        self.meta = meta_from_definition(self.definition)

    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        dates = pd.DatetimeIndex(pd.to_datetime(bars["date"]).dt.normalize())
        out = empty_signals(dates)
        entry_conditions = self.definition.get("entry_conditions") or []
        exit_conditions = self.definition.get("exit_conditions") or []
        if not entry_conditions and not exit_conditions:
            return out
        cache: dict[str, pd.Series] = {}
        entry_signal, entry_hits = evaluate_conditions(
            entry_conditions,
            self.definition.get("entry_logic", "all"),
            bars,
            cache,
        )
        exit_signal, exit_hits = evaluate_conditions(
            exit_conditions,
            self.definition.get("exit_logic", "all"),
            bars,
            cache,
        )
        if not entry_hits.empty:
            ratios = entry_hits.mean(axis=1) * 100.0
            out["score"] = ratios.fillna(0.0).to_numpy()
        out["entry"] = entry_signal.to_numpy()
        out["exit"] = exit_signal.to_numpy()
        entry_descriptions = [describe_condition(condition) for condition in entry_conditions]
        exit_descriptions = [describe_condition(condition) for condition in exit_conditions]
        entry_join = " 或 " if self.definition.get("entry_logic") == "any" else " 且 "
        exit_join = " 或 " if self.definition.get("exit_logic") == "any" else " 且 "
        if not entry_hits.empty:
            entry_reasons = entry_hits.apply(
                lambda row: entry_join.join(
                    description for description, hit in zip(entry_descriptions, row) if hit
                ),
                axis=1,
            )
            mask = entry_signal.to_numpy(dtype=bool)
            out.loc[mask, "entry_reason"] = entry_reasons[mask].fillna("").to_numpy()
        if not exit_hits.empty:
            exit_reasons = exit_hits.apply(
                lambda row: exit_join.join(
                    description for description, hit in zip(exit_descriptions, row) if hit
                ),
                axis=1,
            )
            mask = exit_signal.to_numpy(dtype=bool)
            out.loc[mask, "exit_reason"] = exit_reasons[mask].fillna("").to_numpy()
        return out
