from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from core.strategy_api import normalize_signals


@dataclass
class BacktestConfig:
    initial_cash: float = 100000.0
    commission_rate: float = 0.00025
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0
    lot_size: int = 100
    position_pct: float = 0.95
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    max_hold_days: int = 0
    close_at_end: bool = True
    start_date: str | None = None
    end_date: str | None = None


@dataclass
class BacktestResult:
    equity: pd.DataFrame
    trades: pd.DataFrame
    signals: pd.DataFrame
    metrics: dict[str, Any]
    config: BacktestConfig
    warnings: list[str] = field(default_factory=list)
    open_position: dict[str, Any] | None = None


def _commission(notional: float, config: BacktestConfig) -> float:
    return max(config.min_commission, notional * config.commission_rate)


def _buy_fee(notional: float, config: BacktestConfig) -> float:
    return _commission(notional, config) + notional * config.transfer_fee_rate


def _sell_fee(notional: float, config: BacktestConfig) -> float:
    return (
        _commission(notional, config)
        + notional * config.transfer_fee_rate
        + notional * config.stamp_tax_rate
    )


def _prepare_bars(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    df = bars.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize().astype("datetime64[ns]")
    df = df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)
    if df.empty:
        raise ValueError("回测区间内没有行情数据")
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return df


def _prepare_benchmark(benchmark: pd.DataFrame | None) -> pd.DataFrame | None:
    if benchmark is None or benchmark.empty:
        return None
    df = benchmark.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize().astype("datetime64[ns]")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").drop_duplicates("date", keep="last")
    return df[["date", "close"]].reset_index(drop=True)


def _align_benchmark(benchmark: pd.DataFrame | None, dates: pd.DatetimeIndex) -> pd.Series:
    if benchmark is None or benchmark.empty:
        return pd.Series(np.nan, index=dates, dtype=float)
    aligned = pd.merge_asof(
        pd.DataFrame({"date": dates}).sort_values("date"),
        benchmark.sort_values("date"),
        on="date",
        direction="backward",
    )
    values = aligned.set_index("date")["close"].reindex(dates)
    return values.ffill()


def _format_trade_reason(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def run_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    config = config or BacktestConfig()
    if config.initial_cash <= 0:
        raise ValueError("初始资金必须大于 0")
    start = pd.Timestamp(config.start_date).normalize() if config.start_date else pd.Timestamp(bars["date"].min()).normalize()
    end = pd.Timestamp(config.end_date).normalize() if config.end_date else pd.Timestamp(bars["date"].max()).normalize()
    df = _prepare_bars(bars, start, end)
    dates = pd.DatetimeIndex(df["date"])
    sig = normalize_signals(signals, dates)
    bench = _prepare_benchmark(benchmark)
    bench_aligned = _align_benchmark(bench, dates)
    warnings: list[str] = []
    if bench_aligned.dropna().empty:
        warnings.append("基准指数无数据，超额收益不可用")
    slippage = float(config.slippage_bps) / 10000.0
    lot = max(int(config.lot_size), 1)
    cash = float(config.initial_cash)
    shares = 0
    entry_price = 0.0
    entry_date: pd.Timestamp | None = None
    entry_index = -1
    entry_notional = 0.0
    entry_fee_paid = 0.0
    entry_reason = ""
    entry_score = 0.0
    pending_entry = False
    pending_exit = False
    pending_exit_reason = ""
    trades: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []

    for i, row in df.iterrows():
        date = pd.Timestamp(row["date"])
        if i > 0 and (pending_exit or pending_entry):
            if pending_exit and shares > 0:
                if i > entry_index:
                    price = float(row["open"]) * (1.0 - slippage)
                    notional = shares * price
                    fee = _sell_fee(notional, config)
                    proceeds = notional - fee
                    cash += proceeds
                    pnl = proceeds - (entry_notional + entry_fee_paid)
                    trades.append(
                        {
                            "entry_date": entry_date,
                            "exit_date": date,
                            "holding_days": int(i - entry_index),
                            "entry_price": entry_price,
                            "exit_price": price,
                            "shares": int(shares),
                            "entry_notional": entry_notional,
                            "exit_notional": notional,
                            "entry_fee": entry_fee_paid,
                            "exit_fee": fee,
                            "pnl": pnl,
                            "return_pct": pnl / (entry_notional + entry_fee_paid) * 100.0 if entry_notional + entry_fee_paid else 0.0,
                            "entry_reason": entry_reason,
                            "exit_reason": pending_exit_reason or "策略离场",
                            "entry_score": entry_score,
                        }
                    )
                    shares = 0
                    entry_price = 0.0
                    entry_date = None
                    entry_index = -1
                    entry_notional = 0.0
                    entry_fee_paid = 0.0
                    entry_reason = ""
                    entry_score = 0.0
                else:
                    warnings.append(f"{date.date()} 触发离场但受 T+1 限制，顺延")
            if pending_entry and shares == 0:
                price = float(row["open"]) * (1.0 + slippage)
                budget = cash * float(config.position_pct)
                lots = int(budget // (price * lot)) if price > 0 else 0
                bought = 0
                notional = 0.0
                fee = 0.0
                while lots > 0:
                    candidate = lots * lot
                    candidate_notional = candidate * price
                    candidate_fee = _buy_fee(candidate_notional, config)
                    if candidate_notional + candidate_fee <= cash:
                        bought = candidate
                        notional = candidate_notional
                        fee = candidate_fee
                        break
                    lots -= 1
                if bought > 0:
                    cash -= notional + fee
                    shares = bought
                    entry_price = price
                    entry_date = date
                    entry_index = i
                    entry_notional = notional
                    entry_fee_paid = fee
                    entry_reason = _format_trade_reason(
                        sig.iloc[i - 1].get("entry_reason"), "策略入场"
                    )
                    entry_score = float(sig.iloc[i - 1].get("score", 0.0))
                else:
                    warnings.append(f"{date.date()} 资金不足一手，跳过买入")
            pending_entry = False
            pending_exit = False
            pending_exit_reason = ""

        position_value = shares * float(row["close"])
        equity = cash + position_value
        curve.append(
            {
                "date": date,
                "equity": equity,
                "cash": cash,
                "position_value": position_value,
                "exposure": position_value / equity if equity > 0 else 0.0,
                "benchmark_equity": np.nan,
            }
        )

        if shares > 0:
            exit_reason = ""
            holding_days = i - entry_index
            if config.stop_loss_pct > 0 and float(row["close"]) <= entry_price * (1.0 - config.stop_loss_pct / 100.0):
                exit_reason = f"止损 {config.stop_loss_pct:g}%"
            elif config.take_profit_pct > 0 and float(row["close"]) >= entry_price * (1.0 + config.take_profit_pct / 100.0):
                exit_reason = f"止盈 {config.take_profit_pct:g}%"
            elif config.max_hold_days > 0 and holding_days >= int(config.max_hold_days):
                exit_reason = f"最长持有 {config.max_hold_days} 天"
            elif bool(sig.iloc[i]["exit"]):
                exit_reason = _format_trade_reason(sig.iloc[i].get("exit_reason"), "策略离场")
            if exit_reason and i < len(df) - 1:
                pending_exit = True
                pending_exit_reason = exit_reason
        else:
            if bool(sig.iloc[i]["entry"]) and i < len(df) - 1:
                pending_entry = True

    last = df.iloc[-1]
    if shares > 0 and config.close_at_end and entry_index < len(df) - 1:
        date = pd.Timestamp(last["date"])
        price = float(last["close"]) * (1.0 - slippage)
        notional = shares * price
        fee = _sell_fee(notional, config)
        proceeds = notional - fee
        cash += proceeds
        pnl = proceeds - (entry_notional + entry_fee_paid)
        trades.append(
            {
                "entry_date": entry_date,
                "exit_date": date,
                "holding_days": int(len(df) - 1 - entry_index),
                "entry_price": entry_price,
                "exit_price": price,
                "shares": int(shares),
                "entry_notional": entry_notional,
                "exit_notional": notional,
                "entry_fee": entry_fee_paid,
                "exit_fee": fee,
                "pnl": pnl,
                "return_pct": pnl / (entry_notional + entry_fee_paid) * 100.0 if entry_notional + entry_fee_paid else 0.0,
                "entry_reason": entry_reason,
                "exit_reason": "期末清仓",
                "entry_score": entry_score,
            }
        )
        curve[-1]["equity"] = cash
        curve[-1]["cash"] = cash
        curve[-1]["position_value"] = 0.0
        curve[-1]["exposure"] = 0.0
        shares = 0
    open_position = None
    if shares > 0:
        open_position = {
            "entry_date": entry_date,
            "shares": int(shares),
            "entry_price": entry_price,
            "entry_reason": entry_reason,
            "last_price": float(last["close"]),
            "unrealized_pnl": shares * float(last["close"]) - (entry_notional + entry_fee_paid),
        }
        warnings.append("回测期末仍有持仓，指标按市值计算但未计入已实现交易")

    curve_df = pd.DataFrame(curve)
    bench_base = bench_aligned.dropna()
    if not bench_base.empty:
        base_close = float(bench_base.iloc[0])
        curve_df["benchmark_equity"] = config.initial_cash * bench_aligned.values / base_close
    trades_df = pd.DataFrame(
        trades,
        columns=[
            "entry_date",
            "exit_date",
            "holding_days",
            "entry_price",
            "exit_price",
            "shares",
            "entry_notional",
            "exit_notional",
            "entry_fee",
            "exit_fee",
            "pnl",
            "return_pct",
            "entry_reason",
            "exit_reason",
            "entry_score",
        ],
    )
    metrics = _metrics(curve_df, trades_df, config)
    return BacktestResult(
        equity=curve_df,
        trades=trades_df,
        signals=sig,
        metrics=metrics,
        config=config,
        warnings=warnings,
        open_position=open_position,
    )


def _metrics(curve: pd.DataFrame, trades: pd.DataFrame, config: BacktestConfig) -> dict[str, Any]:
    equity = curve["equity"].astype(float)
    daily = equity.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    n_days = len(equity)
    final = float(equity.iloc[-1]) if n_days else config.initial_cash
    total_return = final / config.initial_cash - 1.0 if config.initial_cash else 0.0
    annual = (final / config.initial_cash) ** (252.0 / max(n_days, 1)) - 1.0 if final > 0 and config.initial_cash > 0 else -1.0
    drawdown = equity / equity.cummax() - 1.0
    std = float(daily.std(ddof=0))
    sharpe = float(daily.mean() / std * math.sqrt(252)) if std > 1e-12 else 0.0
    downside = daily[daily < 0]
    downside_std = float(downside.std(ddof=0)) if len(downside) else 0.0
    sortino = float(daily.mean() / downside_std * math.sqrt(252)) if downside_std > 1e-12 else 0.0
    benchmark_return = np.nan
    if "benchmark_equity" in curve.columns and curve["benchmark_equity"].notna().any():
        bench = curve["benchmark_equity"].dropna()
        if len(bench) > 1 and bench.iloc[0] > 0:
            benchmark_return = float(bench.iloc[-1] / bench.iloc[0] - 1.0)
    wins = trades[trades["pnl"] > 0] if not trades.empty else trades
    losses = trades[trades["pnl"] < 0] if not trades.empty else trades
    gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
    gross_loss = float(losses["pnl"].sum()) if not losses.empty else 0.0
    return {
        "final_equity": final,
        "total_return_pct": total_return * 100.0,
        "annual_return_pct": annual * 100.0,
        "max_drawdown_pct": float(drawdown.min() * 100.0) if n_days else 0.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "volatility_pct": std * math.sqrt(252) * 100.0,
        "trade_count": int(len(trades)),
        "win_rate_pct": float(len(wins) / len(trades) * 100.0) if len(trades) else 0.0,
        "profit_factor": (gross_profit / abs(gross_loss)) if gross_loss < 0 else (float("inf") if gross_profit > 0 else 0.0),
        "avg_trade_pct": float(trades["return_pct"].mean()) if len(trades) else 0.0,
        "exposure_pct": float(curve["exposure"].mean() * 100.0) if n_days else 0.0,
        "benchmark_return_pct": benchmark_return * 100.0 if not math.isnan(benchmark_return) else None,
        "excess_return_pct": (total_return - benchmark_return) * 100.0 if not math.isnan(benchmark_return) else None,
        "buy_hold_return_pct": float((curve["benchmark_equity"].dropna().iloc[-1] / config.initial_cash - 1.0) * 100.0) if curve["benchmark_equity"].notna().any() else None,
        "n_days": int(n_days),
        "start": str(curve["date"].iloc[0].date()) if n_days else "",
        "end": str(curve["date"].iloc[-1].date()) if n_days else "",
    }


def run_backtest_native(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    from core.rust_engine import simulate

    config = config or BacktestConfig()
    if config.initial_cash <= 0:
        raise ValueError("初始资金必须大于 0")
    start = pd.Timestamp(config.start_date).normalize() if config.start_date else pd.Timestamp(bars["date"].min()).normalize()
    end = pd.Timestamp(config.end_date).normalize() if config.end_date else pd.Timestamp(bars["date"].max()).normalize()
    df = _prepare_bars(bars, start, end)
    dates = pd.DatetimeIndex(df["date"])
    sig = normalize_signals(signals, dates)
    bench = _prepare_benchmark(benchmark)
    bench_aligned = _align_benchmark(bench, dates)
    warnings: list[str] = []
    if bench_aligned.dropna().empty:
        warnings.append("基准指数无数据，超额收益不可用")
    native = simulate(
        df["open"].to_numpy(dtype=np.float64),
        df["close"].to_numpy(dtype=np.float64),
        sig["entry"].to_numpy(dtype=np.uint8),
        sig["exit"].to_numpy(dtype=np.uint8),
        config,
    )
    curve_df = pd.DataFrame(
        {
            "date": dates,
            "equity": native.equity,
            "cash": native.cash,
            "position_value": native.position_value,
            "exposure": native.exposure,
            "benchmark_equity": np.nan,
        }
    )
    bench_base = bench_aligned.dropna()
    if not bench_base.empty:
        base_close = float(bench_base.iloc[0])
        curve_df["benchmark_equity"] = config.initial_cash * bench_aligned.values / base_close
    trade_rows: list[dict[str, Any]] = []
    for item in native.trades:
        entry_idx = int(item["entry_idx"])
        exit_idx = int(item["exit_idx"])
        entry_signal_idx = int(item["entry_signal_idx"])
        exit_signal_idx = int(item["exit_signal_idx"])
        entry_reason = "策略入场"
        entry_score = 0.0
        if 0 <= entry_signal_idx < len(sig):
            entry_reason = _format_trade_reason(sig.iloc[entry_signal_idx].get("entry_reason"), "策略入场")
            entry_score = float(sig.iloc[entry_signal_idx].get("score", 0.0))
        code = int(item["exit_code"])
        if code == 1:
            exit_reason = f"止损 {config.stop_loss_pct:g}%"
        elif code == 2:
            exit_reason = f"止盈 {config.take_profit_pct:g}%"
        elif code == 3:
            exit_reason = f"最长持有 {config.max_hold_days} 天"
        elif code == 4:
            exit_reason = "期末清仓"
        else:
            exit_reason = "策略离场"
            if 0 <= exit_signal_idx < len(sig):
                exit_reason = _format_trade_reason(sig.iloc[exit_signal_idx].get("exit_reason"), "策略离场")
        trade_rows.append(
            {
                "entry_date": dates[entry_idx],
                "exit_date": dates[exit_idx],
                "holding_days": exit_idx - entry_idx,
                "entry_price": item["entry_price"],
                "exit_price": item["exit_price"],
                "shares": int(item["shares"]),
                "entry_notional": item["entry_notional"],
                "exit_notional": item["exit_notional"],
                "entry_fee": item["entry_fee"],
                "exit_fee": item["exit_fee"],
                "pnl": item["pnl"],
                "return_pct": item["return_pct"],
                "entry_reason": entry_reason,
                "exit_reason": exit_reason,
                "entry_score": entry_score,
            }
        )
    trades_df = pd.DataFrame(
        trade_rows,
        columns=[
            "entry_date", "exit_date", "holding_days", "entry_price", "exit_price",
            "shares", "entry_notional", "exit_notional", "entry_fee", "exit_fee",
            "pnl", "return_pct", "entry_reason", "exit_reason", "entry_score",
        ],
    )
    open_position = None
    if native.open_position is not None:
        entry_idx = int(native.open_position["entry_idx"])
        entry_price = float(native.open_position["entry_price"])
        shares = int(native.open_position["shares"])
        last_price = float(df["close"].iloc[-1])
        entry_reason = "策略入场"
        if entry_idx - 1 >= 0 and entry_idx - 1 < len(sig):
            entry_reason = _format_trade_reason(sig.iloc[entry_idx - 1].get("entry_reason"), "策略入场")
        open_position = {
            "entry_date": dates[entry_idx] if entry_idx >= 0 else None,
            "shares": shares,
            "entry_price": entry_price,
            "entry_reason": entry_reason,
            "last_price": last_price,
            "unrealized_pnl": shares * last_price - (native.open_position["entry_notional"] + native.open_position["entry_fee"]),
        }
        warnings.append("回测期末仍有持仓，指标按市值计算但未计入已实现交易")
    metrics = _metrics(curve_df, trades_df, config)
    return BacktestResult(
        equity=curve_df,
        trades=trades_df,
        signals=sig,
        metrics=metrics,
        config=config,
        warnings=warnings,
        open_position=open_position,
    )


def run_backtest_auto(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    try:
        from core.rust_engine import available

        if available():
            return run_backtest_native(bars, signals, benchmark, config)
    except Exception:
        pass
    return run_backtest(bars, signals, benchmark, config)
