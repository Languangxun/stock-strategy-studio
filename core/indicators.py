from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.rolling(window, min_periods=window).mean()
    avg_loss = loss.rolling(window, min_periods=window).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    return 100.0 - 100.0 / (1.0 + rs)


def true_range(bars: pd.DataFrame) -> pd.Series:
    prev_close = bars["close"].shift(1)
    return pd.concat(
        [
            (bars["high"] - bars["low"]).abs(),
            (bars["high"] - prev_close).abs(),
            (bars["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(bars: pd.DataFrame, window: int = 14) -> pd.Series:
    return true_range(bars).rolling(window, min_periods=window).mean()


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    dif = ema(series, fast) - ema(series, slow)
    dea = ema(dif, signal)
    hist = (dif - dea) * 2.0
    return dif, dea, hist


def kdj(bars: pd.DataFrame, window: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    low = bars["low"].rolling(window, min_periods=window).min()
    high = bars["high"].rolling(window, min_periods=window).max()
    rsv = (bars["close"] - low) / (high - low + 1e-12) * 100.0
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return k, d, j


def bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = sma(series, window)
    std = series.rolling(window, min_periods=window).std()
    return mid + num_std * std, mid, mid - num_std * std


def volume_ratio(volume: pd.Series, window: int = 5) -> pd.Series:
    base = volume.rolling(window, min_periods=window).mean().shift(1)
    return volume / (base + 1e-12)


def rolling_high(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).max().shift(1)


def rolling_low(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).min().shift(1)


def normalized_strength(short: pd.Series, long: pd.Series) -> pd.Series:
    spread = short / (long + 1e-12) - 1.0
    return (50.0 + 50.0 * np.tanh(spread * 40.0)).clip(0, 100)
