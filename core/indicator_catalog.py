from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from core.indicators import atr, bollinger, ema, macd, sma, true_range


@dataclass(frozen=True)
class IndicatorParam:
    key: str
    label: str
    kind: str = "int"
    default: Any = 1
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None

    def normalize(self, value: Any) -> Any:
        if self.kind == "int":
            value = int(float(value))
        else:
            value = float(value)
        if self.minimum is not None:
            value = max(value, self.minimum)
        if self.maximum is not None:
            value = min(value, self.maximum)
        return value


@dataclass(frozen=True)
class IndicatorSpec:
    key: str
    name: str
    group: str
    description: str
    params: tuple[IndicatorParam, ...]
    compute: Callable[[pd.DataFrame, dict[str, Any]], pd.Series]

    @property
    def label(self) -> str:
        return f"{self.group} · {self.name}"

    def defaults(self) -> dict[str, Any]:
        return {param.key: param.default for param in self.params}


def _close(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["close"].astype(float)


def _open(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["open"].astype(float)


def _high(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["high"].astype(float)


def _low(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["low"].astype(float)


def _volume(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["volume"].astype(float)


def _amount(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["amount"].astype(float) if "amount" in bars.columns else pd.Series(0.0, index=bars.index)


def _change_pct(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["close"].astype(float).pct_change(1, fill_method=None) * 100.0


def _amplitude(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return (bars["high"].astype(float) - bars["low"].astype(float)) / (bars["close"].astype(float).shift(1) + 1e-12) * 100.0


def _gap_pct(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return (bars["open"].astype(float) / (bars["close"].astype(float).shift(1) + 1e-12) - 1.0) * 100.0


def _sma_indicator(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return sma(bars["close"].astype(float), int(params["period"]))


def _ema_indicator(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return ema(bars["close"].astype(float), int(params["period"]))


def _macd_dif(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    dif, _, _ = macd(bars["close"].astype(float), int(params["fast"]), int(params["slow"]), int(params["signal"]))
    return dif


def _macd_dea(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    _, dea, _ = macd(bars["close"].astype(float), int(params["fast"]), int(params["slow"]), int(params["signal"]))
    return dea


def _macd_hist(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    _, _, hist = macd(bars["close"].astype(float), int(params["fast"]), int(params["slow"]), int(params["signal"]))
    return hist


def _rsi_indicator(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    delta = bars["close"].astype(float).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    n = int(params["period"])
    avg_gain = gain.rolling(n, min_periods=n).mean()
    avg_loss = loss.rolling(n, min_periods=n).mean()
    return 100.0 - 100.0 / (1.0 + avg_gain / (avg_loss + 1e-12))


def _kdj(bars: pd.DataFrame, params: dict[str, Any]) -> tuple[pd.Series, pd.Series, pd.Series]:
    n = int(params["n"])
    m1 = max(int(params["m1"]), 1)
    m2 = max(int(params["m2"]), 1)
    low = bars["low"].astype(float).rolling(n, min_periods=n).min()
    high = bars["high"].astype(float).rolling(n, min_periods=n).max()
    rsv = (bars["close"].astype(float) - low) / (high - low + 1e-12) * 100.0
    k = rsv.ewm(alpha=1.0 / m1, adjust=False).mean()
    d = k.ewm(alpha=1.0 / m2, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def _kdj_k(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _kdj(bars, params)[0]


def _kdj_d(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _kdj(bars, params)[1]


def _kdj_j(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _kdj(bars, params)[2]


def _boll_upper(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    upper, _, _ = bollinger(bars["close"].astype(float), int(params["period"]), float(params["width"]))
    return upper


def _boll_mid(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    _, mid, _ = bollinger(bars["close"].astype(float), int(params["period"]), float(params["width"]))
    return mid


def _boll_lower(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    _, _, lower = bollinger(bars["close"].astype(float), int(params["period"]), float(params["width"]))
    return lower


def _boll_width(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    upper, mid, lower = bollinger(bars["close"].astype(float), int(params["period"]), float(params["width"]))
    return (upper - lower) / (mid + 1e-12) * 100.0


def _atr_indicator(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return atr(bars, int(params["period"]))


def _atr_pct(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return atr(bars, int(params["period"])) / (bars["close"].astype(float) + 1e-12) * 100.0


def _volume_ma(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return sma(bars["volume"].astype(float), int(params["period"]))


def _volume_ratio(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    base = bars["volume"].astype(float).rolling(n, min_periods=n).mean().shift(1)
    return bars["volume"].astype(float) / (base + 1e-12)


def _obv(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    close = bars["close"].astype(float)
    volume = bars["volume"].astype(float)
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume).cumsum()


def _obv_ma(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return sma(_obv(bars, params), int(params["period"]))


def _cci(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    tp = (bars["high"].astype(float) + bars["low"].astype(float) + bars["close"].astype(float)) / 3.0
    ma = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - ma) / (0.015 * md + 1e-12)


def _williams_r(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    high = bars["high"].astype(float).rolling(n, min_periods=n).max()
    low = bars["low"].astype(float).rolling(n, min_periods=n).min()
    return (high - bars["close"].astype(float)) / (high - low + 1e-12) * -100.0


def _roc(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["close"].astype(float).pct_change(int(params["period"]), fill_method=None) * 100.0


def _momentum(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return bars["close"].astype(float) - bars["close"].astype(float).shift(int(params["period"]))


def _dmi(bars: pd.DataFrame, params: dict[str, Any]) -> tuple[pd.Series, pd.Series, pd.Series]:
    n = int(params["period"])
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=bars.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=bars.index)
    atr_wilder = true_range(bars).ewm(alpha=1.0 / n, adjust=False).mean()
    plus_smooth = plus_dm.ewm(alpha=1.0 / n, adjust=False).mean()
    minus_smooth = minus_dm.ewm(alpha=1.0 / n, adjust=False).mean()
    plus_di = 100.0 * plus_smooth / (atr_wilder + 1e-12)
    minus_di = 100.0 * minus_smooth / (atr_wilder + 1e-12)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
    adx = dx.ewm(alpha=1.0 / n, adjust=False).mean()
    return adx, plus_di, minus_di


def _adx(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _dmi(bars, params)[0]


def _plus_di(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _dmi(bars, params)[1]


def _minus_di(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    return _dmi(bars, params)[2]


def _mfi(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    tp = (bars["high"].astype(float) + bars["low"].astype(float) + bars["close"].astype(float)) / 3.0
    flow = tp * bars["volume"].astype(float)
    delta = tp.diff()
    positive = flow.where(delta > 0, 0.0).rolling(n, min_periods=n).sum()
    negative = flow.where(delta < 0, 0.0).rolling(n, min_periods=n).sum()
    return 100.0 - 100.0 / (1.0 + positive / (negative + 1e-12))


def _bias(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    ma = sma(bars["close"].astype(float), n)
    return (bars["close"].astype(float) - ma) / (ma + 1e-12) * 100.0


def _psy(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    up = (bars["close"].astype(float).diff() > 0).astype(float)
    return up.rolling(n, min_periods=n).mean() * 100.0


def _vwap(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    amount = bars["amount"].astype(float) if "amount" in bars.columns else pd.Series(0.0, index=bars.index)
    volume = bars["volume"].astype(float) * 100.0
    amount_sum = amount.rolling(n, min_periods=n).sum()
    volume_sum = volume.rolling(n, min_periods=n).sum()
    return amount_sum / (volume_sum + 1e-12)


def _n_high_prev(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    return bars["high"].astype(float).rolling(n, min_periods=n).max().shift(1)


def _n_low_prev(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    return bars["low"].astype(float).rolling(n, min_periods=n).min().shift(1)


def _range_position(bars: pd.DataFrame, params: dict[str, Any]) -> pd.Series:
    n = int(params["period"])
    high = bars["high"].astype(float).rolling(n, min_periods=n).max()
    low = bars["low"].astype(float).rolling(n, min_periods=n).min()
    return (bars["close"].astype(float) - low) / (high - low + 1e-12) * 100.0


def _period_param(default: int = 20, label: str = "周期", minimum: int = 1, maximum: int = 500) -> IndicatorParam:
    return IndicatorParam("period", label, "int", default, minimum, maximum, 1)


SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("close", "收盘价", "价格", "当日收盘价", (), _close),
    IndicatorSpec("open", "开盘价", "价格", "当日开盘价", (), _open),
    IndicatorSpec("high", "最高价", "价格", "当日最高价", (), _high),
    IndicatorSpec("low", "最低价", "价格", "当日最低价", (), _low),
    IndicatorSpec("change_pct", "涨跌幅", "价格", "相对昨收的涨跌幅（%）", (), _change_pct),
    IndicatorSpec("amplitude", "振幅", "价格", "当日振幅（%）", (), _amplitude),
    IndicatorSpec("gap_pct", "跳空幅度", "价格", "开盘相对昨收的跳空（%）", (), _gap_pct),
    IndicatorSpec("ma", "简单均线 SMA", "趋势", "收盘价简单移动平均", (_period_param(20, "周期"),), _sma_indicator),
    IndicatorSpec("ema", "指数均线 EMA", "趋势", "收盘价指数移动平均", (_period_param(20, "周期"),), _ema_indicator),
    IndicatorSpec(
        "macd_dif", "MACD DIF", "趋势", "MACD 快慢线差值",
        (IndicatorParam("fast", "快线", "int", 12, 1, 100, 1),
         IndicatorParam("slow", "慢线", "int", 26, 2, 200, 1),
         IndicatorParam("signal", "信号线", "int", 9, 1, 100, 1)),
        _macd_dif,
    ),
    IndicatorSpec(
        "macd_dea", "MACD DEA", "趋势", "MACD 信号线",
        (IndicatorParam("fast", "快线", "int", 12, 1, 100, 1),
         IndicatorParam("slow", "慢线", "int", 26, 2, 200, 1),
         IndicatorParam("signal", "信号线", "int", 9, 1, 100, 1)),
        _macd_dea,
    ),
    IndicatorSpec(
        "macd_hist", "MACD 柱", "趋势", "MACD 柱状值",
        (IndicatorParam("fast", "快线", "int", 12, 1, 100, 1),
         IndicatorParam("slow", "慢线", "int", 26, 2, 200, 1),
         IndicatorParam("signal", "信号线", "int", 9, 1, 100, 1)),
        _macd_hist,
    ),
    IndicatorSpec("rsi", "RSI 相对强弱", "动量", "相对强弱指标（0~100）", (_period_param(14, "周期"),), _rsi_indicator),
    IndicatorSpec(
        "kdj_k", "KDJ K 值", "动量", "随机指标 K 值",
        (IndicatorParam("n", "N", "int", 9, 1, 100, 1),
         IndicatorParam("m1", "M1", "int", 3, 1, 100, 1),
         IndicatorParam("m2", "M2", "int", 3, 1, 100, 1)),
        _kdj_k,
    ),
    IndicatorSpec(
        "kdj_d", "KDJ D 值", "动量", "随机指标 D 值",
        (IndicatorParam("n", "N", "int", 9, 1, 100, 1),
         IndicatorParam("m1", "M1", "int", 3, 1, 100, 1),
         IndicatorParam("m2", "M2", "int", 3, 1, 100, 1)),
        _kdj_d,
    ),
    IndicatorSpec(
        "kdj_j", "KDJ J 值", "动量", "随机指标 J 值",
        (IndicatorParam("n", "N", "int", 9, 1, 100, 1),
         IndicatorParam("m1", "M1", "int", 3, 1, 100, 1),
         IndicatorParam("m2", "M2", "int", 3, 1, 100, 1)),
        _kdj_j,
    ),
    IndicatorSpec(
        "boll_upper", "布林上轨", "波动",
        "布林带上轨",
        (_period_param(20, "周期"), IndicatorParam("width", "倍宽", "float", 2.0, 0.1, 10.0, 0.1)),
        _boll_upper,
    ),
    IndicatorSpec(
        "boll_mid", "布林中轨", "波动",
        "布林带中轨",
        (_period_param(20, "周期"), IndicatorParam("width", "倍宽", "float", 2.0, 0.1, 10.0, 0.1)),
        _boll_mid,
    ),
    IndicatorSpec(
        "boll_lower", "布林下轨", "波动",
        "布林带下轨",
        (_period_param(20, "周期"), IndicatorParam("width", "倍宽", "float", 2.0, 0.1, 10.0, 0.1)),
        _boll_lower,
    ),
    IndicatorSpec(
        "boll_width", "布林带宽", "波动",
        "布林带宽度（%）",
        (_period_param(20, "周期"), IndicatorParam("width", "倍宽", "float", 2.0, 0.1, 10.0, 0.1)),
        _boll_width,
    ),
    IndicatorSpec("atr", "ATR 真实波幅", "波动", "平均真实波幅", (_period_param(14, "周期"),), _atr_indicator),
    IndicatorSpec("atr_pct", "ATR 占比", "波动", "ATR / 收盘价（%）", (_period_param(14, "周期"),), _atr_pct),
    IndicatorSpec("volume", "成交量", "量能", "成交量（手）", (), _volume),
    IndicatorSpec("amount", "成交额", "量能", "成交额（元）", (), _amount),
    IndicatorSpec("volume_ma", "成交量均线", "量能", "成交量简单均线", (_period_param(5, "周期"),), _volume_ma),
    IndicatorSpec("volume_ratio", "量比", "量能", "当前成交量 / 前 N 日均量", (_period_param(5, "周期"),), _volume_ratio),
    IndicatorSpec("obv", "OBV 能量潮", "量能", "成交量方向累计", (), _obv),
    IndicatorSpec("obv_ma", "OBV 均线", "量能", "OBV 简单均线", (_period_param(20, "周期"),), _obv_ma),
    IndicatorSpec("mfi", "MFI 资金流量", "量能", "资金流量指标（0~100）", (_period_param(14, "周期"),), _mfi),
    IndicatorSpec("cci", "CCI 顺势指标", "动量", "商品通道指数", (_period_param(14, "周期"),), _cci),
    IndicatorSpec("williams_r", "Williams %R", "动量", "威廉指标（-100~0）", (_period_param(14, "周期"),), _williams_r),
    IndicatorSpec("roc", "ROC 变动率", "动量", "价格变动率（%）", (_period_param(12, "周期"),), _roc),
    IndicatorSpec("momentum", "Momentum 动量", "动量", "收盘价与 N 日前之差", (_period_param(10, "周期"),), _momentum),
    IndicatorSpec("adx", "ADX 趋势强度", "趋势", "平均趋向指数", (_period_param(14, "周期"),), _adx),
    IndicatorSpec("plus_di", "+DI 上升方向", "趋势", "正向动向指标", (_period_param(14, "周期"),), _plus_di),
    IndicatorSpec("minus_di", "-DI 下降方向", "趋势", "负向动向指标", (_period_param(14, "周期"),), _minus_di),
    IndicatorSpec("bias", "BIAS 乖离率", "趋势", "收盘价相对均线乖离（%）", (_period_param(6, "周期"),), _bias),
    IndicatorSpec("psy", "PSY 心理线", "动量", "N 日上涨天数占比（%）", (_period_param(12, "周期"),), _psy),
    IndicatorSpec("vwap", "VWAP 成交量加权价", "量能", "N 日成交量加权均价", (_period_param(20, "周期"),), _vwap),
    IndicatorSpec("n_high_prev", "前 N 日最高", "通道", "不含当日的 N 日最高价", (_period_param(20, "周期"),), _n_high_prev),
    IndicatorSpec("n_low_prev", "前 N 日最低", "通道", "不含当日的 N 日最低价", (_period_param(20, "周期"),), _n_low_prev),
    IndicatorSpec("range_position", "区间位置", "通道", "收盘价在 N 日高低区间中的位置（%）", (_period_param(20, "周期"),), _range_position),
)

_CATALOG = {spec.key: spec for spec in SPECS}


def get_indicator(key: str) -> IndicatorSpec | None:
    return _CATALOG.get(str(key))


def all_indicators() -> tuple[IndicatorSpec, ...]:
    return SPECS


def indicator_labels() -> dict[str, str]:
    return {spec.label: spec.key for spec in SPECS}


def normalize_indicator_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if not config:
        return None
    key = str(config.get("indicator", ""))
    spec = get_indicator(key)
    if spec is None:
        return None
    raw = config.get("params") or {}
    params = {}
    for param in spec.params:
        try:
            params[param.key] = param.normalize(raw.get(param.key, param.default))
        except (TypeError, ValueError):
            params[param.key] = param.default
    return {"indicator": key, "params": params}


def compute_indicator(config: dict[str, Any], bars: pd.DataFrame) -> pd.Series:
    spec = get_indicator(str(config.get("indicator", "")))
    if spec is None:
        raise ValueError(f"未知指标: {config.get('indicator')}")
    params = config.get("params") or spec.defaults()
    series = spec.compute(bars, params)
    series = pd.to_numeric(series, errors="coerce")
    return series.replace([np.inf, -np.inf], np.nan)


def describe_indicator(config: dict[str, Any]) -> str:
    spec = get_indicator(str(config.get("indicator", "")))
    if spec is None:
        return "未知指标"
    params = config.get("params") or {}
    pieces = []
    for param in spec.params:
        value = params.get(param.key, param.default)
        pieces.append(f"{param.label}={value}")
    suffix = f"({', '.join(pieces)})" if pieces else ""
    return f"{spec.name}{suffix}"
