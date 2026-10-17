from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


class NativeUnavailable(RuntimeError):
    pass


class BtConfig(ctypes.Structure):
    _fields_ = [
        ("initial_cash", ctypes.c_double),
        ("commission_rate", ctypes.c_double),
        ("min_commission", ctypes.c_double),
        ("stamp_tax_rate", ctypes.c_double),
        ("transfer_fee_rate", ctypes.c_double),
        ("slippage_bps", ctypes.c_double),
        ("lot_size", ctypes.c_int),
        ("position_pct", ctypes.c_double),
        ("stop_loss_pct", ctypes.c_double),
        ("take_profit_pct", ctypes.c_double),
        ("max_hold_days", ctypes.c_int),
        ("close_at_end", ctypes.c_int),
    ]


class BtTrade(ctypes.Structure):
    _fields_ = [
        ("entry_idx", ctypes.c_int),
        ("exit_idx", ctypes.c_int),
        ("entry_signal_idx", ctypes.c_int),
        ("exit_signal_idx", ctypes.c_int),
        ("exit_code", ctypes.c_int),
        ("_pad", ctypes.c_int),
        ("shares", ctypes.c_longlong),
        ("entry_price", ctypes.c_double),
        ("exit_price", ctypes.c_double),
        ("entry_notional", ctypes.c_double),
        ("exit_notional", ctypes.c_double),
        ("entry_fee", ctypes.c_double),
        ("exit_fee", ctypes.c_double),
        ("pnl", ctypes.c_double),
        ("return_pct", ctypes.c_double),
    ]


class BtOpen(ctypes.Structure):
    _fields_ = [
        ("shares", ctypes.c_longlong),
        ("entry_idx", ctypes.c_int),
        ("_pad", ctypes.c_int),
        ("entry_price", ctypes.c_double),
        ("entry_notional", ctypes.c_double),
        ("entry_fee", ctypes.c_double),
    ]


@dataclass
class NativeResult:
    equity: np.ndarray
    cash: np.ndarray
    position_value: np.ndarray
    exposure: np.ndarray
    trades: list[dict[str, Any]]
    open_position: dict[str, Any] | None


_LIB: ctypes.CDLL | None = None
_LOAD_ERROR: str | None = None


def _library_path() -> Path:
    custom = os.environ.get("STUDIO_RUST_LIB")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent / "native" / "libbtcore.so"


def library() -> ctypes.CDLL:
    global _LIB, _LOAD_ERROR
    if _LIB is not None:
        return _LIB
    path = _library_path()
    if not path.exists():
        _LOAD_ERROR = f"Rust 扩展不存在: {path}"
        raise NativeUnavailable(_LOAD_ERROR)
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        _LOAD_ERROR = f"Rust 扩展加载失败: {exc}"
        raise NativeUnavailable(_LOAD_ERROR) from exc
    lib.bt_run.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.POINTER(BtConfig),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(ctypes.c_double),
        ctypes.POINTER(BtTrade),
        ctypes.c_int,
        ctypes.POINTER(BtOpen),
    ]
    lib.bt_run.restype = ctypes.c_int
    _LIB = lib
    return lib


def available() -> bool:
    try:
        library()
        return True
    except NativeUnavailable:
        return False


def status() -> tuple[bool, str]:
    try:
        library()
        return True, str(_library_path())
    except NativeUnavailable as exc:
        return False, str(exc)


def simulate(
    open_prices: np.ndarray,
    close_prices: np.ndarray,
    entries: np.ndarray,
    exits: np.ndarray,
    config: Any,
) -> NativeResult:
    lib = library()
    n = int(len(open_prices))
    if n == 0:
        raise ValueError("空行情无法执行原生回测")
    if not (len(close_prices) == len(entries) == len(exits) == n):
        raise ValueError("原生回测输入长度不一致")
    op = np.ascontiguousarray(open_prices, dtype=np.float64)
    cl = np.ascontiguousarray(close_prices, dtype=np.float64)
    en = np.ascontiguousarray(entries, dtype=np.uint8)
    ex = np.ascontiguousarray(exits, dtype=np.uint8)
    equity = np.zeros(n, dtype=np.float64)
    cash = np.zeros(n, dtype=np.float64)
    position_value = np.zeros(n, dtype=np.float64)
    exposure = np.zeros(n, dtype=np.float64)
    trades = (BtTrade * n)()
    open_out = BtOpen()
    cfg = BtConfig(
        initial_cash=float(config.initial_cash),
        commission_rate=float(config.commission_rate),
        min_commission=float(config.min_commission),
        stamp_tax_rate=float(config.stamp_tax_rate),
        transfer_fee_rate=float(config.transfer_fee_rate),
        slippage_bps=float(config.slippage_bps),
        lot_size=int(config.lot_size),
        position_pct=float(config.position_pct),
        stop_loss_pct=float(config.stop_loss_pct),
        take_profit_pct=float(config.take_profit_pct),
        max_hold_days=int(config.max_hold_days),
        close_at_end=1 if config.close_at_end else 0,
    )
    count = lib.bt_run(
        n,
        op.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        cl.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        en.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ex.ctypes.data_as(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.byref(cfg),
        equity.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        cash.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        position_value.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        exposure.ctypes.data_as(ctypes.POINTER(ctypes.c_double)),
        trades,
        n,
        ctypes.byref(open_out),
    )
    if count < 0:
        raise NativeUnavailable(f"Rust 回测执行失败，错误码 {count}")
    records = []
    for idx in range(count):
        item = trades[idx]
        records.append(
            {
                "entry_idx": int(item.entry_idx),
                "exit_idx": int(item.exit_idx),
                "entry_signal_idx": int(item.entry_signal_idx),
                "exit_signal_idx": int(item.exit_signal_idx),
                "exit_code": int(item.exit_code),
                "shares": int(item.shares),
                "entry_price": float(item.entry_price),
                "exit_price": float(item.exit_price),
                "entry_notional": float(item.entry_notional),
                "exit_notional": float(item.exit_notional),
                "entry_fee": float(item.entry_fee),
                "exit_fee": float(item.exit_fee),
                "pnl": float(item.pnl),
                "return_pct": float(item.return_pct),
            }
        )
    open_position = None
    if int(open_out.shares) > 0:
        open_position = {
            "entry_idx": int(open_out.entry_idx),
            "shares": int(open_out.shares),
            "entry_price": float(open_out.entry_price),
            "entry_notional": float(open_out.entry_notional),
            "entry_fee": float(open_out.entry_fee),
        }
    return NativeResult(equity, cash, position_value, exposure, records, open_position)
