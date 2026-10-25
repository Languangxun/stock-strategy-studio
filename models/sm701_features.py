from __future__ import annotations

import numpy as np
import pandas as pd


FEATURES_SM701 = [
    "ret1", "ret5", "ret10", "ret20", "ret60",
    "ma5r", "ma10r", "ma20r", "ma60r", "ma5_20", "ma20_60",
    "vol20", "vol60", "volr", "rsi6", "rsi14",
    "macd_dif", "macd_dea", "macd_hist",
    "kdj_k", "kdj_d", "kdj_j",
    "bollpos", "hilo20", "atr_n",
    "amp", "gap", "cpos",
    "vr5", "vr20", "logvol", "updays20",
    "r_ret5", "r_ret20", "r_ret60", "r_ma20r", "r_rsi14",
    "r_vr20", "r_vol20", "r_logvol",
]

RANK_FEATURES = [
    "ret5", "ret20", "ret60", "ma20r", "rsi14",
    "vr20", "vol20", "logvol",
]


def _prepare_bars(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize().astype("datetime64[ns]")
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    if "volume" not in df.columns:
        if "vol" in df.columns:
            df["volume"] = df["vol"]
        else:
            raise ValueError("缺少行情字段: volume")
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"缺少行情字段: {col}")
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_features(
    bars: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    neutral_ranks: bool = True,
) -> pd.DataFrame:
    if bars is None or len(bars) < 65:
        return pd.DataFrame(columns=["date", *FEATURES_SM701])
    df = _prepare_bars(bars)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    feats: dict[str, pd.Series] = {}
    for n in (1, 5, 10, 20, 60):
        feats[f"ret{n}"] = close.pct_change(n, fill_method=None)

    ma5 = close.rolling(5, min_periods=5).mean()
    ma10 = close.rolling(10, min_periods=10).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    feats["ma5r"] = close / ma5 - 1.0
    feats["ma10r"] = close / ma10 - 1.0
    feats["ma20r"] = close / ma20 - 1.0
    feats["ma60r"] = close / ma60 - 1.0
    feats["ma5_20"] = ma5 / ma20 - 1.0
    feats["ma20_60"] = ma20 / ma60 - 1.0

    std20 = close.rolling(20, min_periods=20).std()
    std60 = close.rolling(60, min_periods=60).std()
    feats["vol20"] = std20
    feats["vol60"] = std60
    feats["volr"] = std20 / (std60 + 1e-9)

    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    for n in (6, 14):
        fu = up.rolling(n, min_periods=n).mean()
        fd = dn.rolling(n, min_periods=n).mean()
        feats[f"rsi{n}"] = 100.0 * fu / (fu + fd + 1e-9)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    feats["macd_dif"] = dif / close
    feats["macd_dea"] = dea / close
    feats["macd_hist"] = (dif - dea) * 2.0 / close

    llv = low.rolling(9, min_periods=9).min()
    hhv = high.rolling(9, min_periods=9).max()
    rsv = (close - llv) / (hhv - llv + 1e-9) * 100.0
    k = rsv.ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    feats["kdj_k"] = k
    feats["kdj_d"] = d
    feats["kdj_j"] = 3.0 * k - 2.0 * d

    cmin20 = close.rolling(20, min_periods=20).min()
    cmax20 = close.rolling(20, min_periods=20).max()
    feats["bollpos"] = (close - ma20) / (2.0 * std20 + 1e-9)
    feats["hilo20"] = (close - cmin20) / (cmax20 - cmin20 + 1e-9)

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    feats["atr_n"] = tr.rolling(14, min_periods=14).mean() / close
    feats["amp"] = (high - low) / (prev_close + 1e-9)
    feats["gap"] = df["open"] / (prev_close + 1e-9) - 1.0
    feats["cpos"] = (close - low) / (high - low + 1e-9)

    vol5 = volume.rolling(5, min_periods=5).mean()
    vol20 = volume.rolling(20, min_periods=20).mean()
    feats["vr5"] = volume / (vol5 + 1e-9)
    feats["vr20"] = volume / (vol20 + 1e-9)
    feats["logvol"] = np.log1p(volume)

    feats["updays20"] = (feats["ret1"] > 0).astype(float).rolling(20, min_periods=20).mean()

    if neutral_ranks:
        for col in RANK_FEATURES:
            feats[f"r_{col}"] = pd.Series(0.5, index=df.index, dtype=float)
    else:
        for col in RANK_FEATURES:
            feats[f"r_{col}"] = feats[col].rank(pct=True)

    out = pd.DataFrame({"date": df["date"]})
    for name in FEATURES_SM701:
        out[name] = feats[name]
    out = out[out["ret60"].notna()].reset_index(drop=True)
    for col in FEATURES_SM701:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out[FEATURES_SM701] = out[FEATURES_SM701].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out
