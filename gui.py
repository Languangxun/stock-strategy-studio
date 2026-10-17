from __future__ import annotations

import bisect
import math
import queue
import subprocess
import threading
import traceback
import webbrowser
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    DND_AVAILABLE = True
    ROOT_BASE = TkinterDnD.Tk
except Exception:
    DND_FILES = None
    DND_AVAILABLE = False
    ROOT_BASE = tk.Tk

from core.backtest import BacktestConfig
from core.datafeed import CacheStore
from core.importer import collect_paths, import_paths
from core.market import MarketRequest, export_market_result, run_market
from core.quant_generator import QuantProject, generate_quant_project
from core.remote_deploy import RemoteDeployConfig, deploy_project
from core.rust_engine import status as rust_status
from core.settings import UserSettings, blend_hex, contrast_text, is_hex_color, load_settings, save_settings
from core.indicator_catalog import all_indicators, get_indicator
from core.runner import PROJECT_ROOT, RunOutput, RunRequest, execute, export_output
from models.registry import LOAD_ERRORS as MODEL_LOAD_ERRORS, create_model, list_models
from strategies.custom import (
    LOGIC_LABELS,
    LOGIC_VALUES,
    OP_LABELS,
    OPERATORS,
    RIGHT_KINDS,
    describe_condition,
    new_definition,
    normalize_condition,
)
from strategies.custom_store import delete_definition, get_definition, list_definitions, save_definition
from strategies.registry import CUSTOM_ERRORS, LOAD_ERRORS as STRATEGY_LOAD_ERRORS, get_strategy_meta, list_strategies


BG = "#000000"
PANEL = "#060606"
CARD = "#0c0c0c"
CARD_ALT = "#181818"
BORDER = "#707070"
TEXT = "#ffffff"
MUTED = "#d6d6d6"
DIM = "#a8a8a8"
ACCENT = "#00d4ff"
ACCENT_HOVER = "#7ae8ff"
GREEN = "#00ff9c"
RED = "#ff4d4d"
AMBER = "#ffd400"
GRID = "#3c3c3c"
CHART_FILL = "#00313d"
DRAWDOWN_FILL = "#3a0000"
DOCS = PROJECT_ROOT / "docs"
CONFIG_DIR = PROJECT_ROOT / "config"
SESSION_FILE = CONFIG_DIR / "last_session.json"
DATA_DIR = PROJECT_ROOT / "data"
IMPORT_DIR = DATA_DIR / "import"
IMPORT_DIR.mkdir(parents=True, exist_ok=True)
APP_VERSION = "0.1.1"


def choose_font(root: tk.Misc) -> str:
    available = set(tkfont.families(root))
    for name in (
        "Noto Sans CJK SC",
        "Noto Sans CJK",
        "Source Han Sans SC",
        "WenQuanYi Micro Hei",
        "Microsoft YaHei",
        "PingFang SC",
        "DejaVu Sans",
    ):
        if name in available:
            return name
    return "TkDefaultFont"


def fmt_pct(value: Any, signed: bool = True) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    try:
        return f"{value:+.2f}%" if signed else f"{value:.2f}%"
    except (TypeError, ValueError):
        return "--"


def fmt_money(value: Any) -> str:
    try:
        return f"¥{float(value):,.0f}"
    except (TypeError, ValueError):
        return "--"


def fmt_num(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        return [low]
    span = high - low
    raw_step = span / max(count - 1, 1)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    candidates = [1, 2, 2.5, 5, 10]
    step = magnitude
    for candidate in candidates:
        if raw_step <= candidate * magnitude:
            step = candidate * magnitude
            break
    start = math.ceil(low / step) * step
    ticks = []
    value = start
    while value <= high + step * 0.01:
        ticks.append(value)
        value += step
    return ticks


def date_ticks(dates: pd.Series, count: int = 6) -> list[int]:
    n = len(dates)
    if n == 0:
        return []
    raw = np.linspace(0, n - 1, min(count, n), dtype=int)
    return sorted(set(int(x) for x in raw))


class ScrollableFrame(tk.Frame):
    def __init__(self, parent: tk.Misc, background: str):
        super().__init__(parent, bg=background)
        self.canvas = tk.Canvas(self, bg=background, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=background)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

    def _on_inner(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def contains_widget(self, widget: tk.Misc | None) -> bool:
        current = widget
        while current is not None:
            if current in (self, self.canvas, self.inner):
                return True
            current = getattr(current, "master", None)
        return False

    def scroll_event(self, event) -> str:
        if getattr(event, "num", None) == 4:
            step = -3
        elif getattr(event, "num", None) == 5:
            step = 3
        else:
            step = -3 if getattr(event, "delta", 0) > 0 else 3
        self.canvas.yview_scroll(step, "units")
        return "break"


class MetricCard(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, font_family: str):
        super().__init__(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.title = tk.Label(self, text=title, bg=CARD, fg=MUTED, font=(font_family, 9), anchor="w")
        self.value = tk.Label(self, text="--", bg=CARD, fg=TEXT, font=(font_family, 18, "bold"), anchor="w")
        self.sub = tk.Label(self, text="", bg=CARD, fg=DIM, font=(font_family, 8), anchor="w")
        self.title.pack(fill="x", padx=14, pady=(11, 1))
        self.value.pack(fill="x", padx=14)
        self.sub.pack(fill="x", padx=14, pady=(1, 10))

    def set(self, value: str, sub: str = "", color: str = TEXT):
        self.value.configure(text=value, fg=color)
        self.sub.configure(text=sub)


class ChartBase(tk.Canvas):
    def __init__(self, parent: tk.Misc, font_family: str):
        super().__init__(parent, bg=CARD, highlightthickness=0, bd=0)
        self.font_family = font_family
        self.plot_left = 72
        self.plot_right = 24
        self._data = None
        self._hover_points: list[tuple[float, float]] = []
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self.delete("hover"))

    def set_data(self, output: RunOutput | None):
        self._data = output
        self.redraw()

    def _empty(self, message: str):
        self.delete("all")
        width = max(self.winfo_width(), 500)
        height = max(self.winfo_height(), 320)
        self.create_text(
            width / 2,
            height / 2,
            text=message,
            fill=DIM,
            font=(self.font_family, 11),
        )

    def _box(self) -> tuple[int, int, int, int]:
        width = max(self.winfo_width(), 500)
        height = max(self.winfo_height(), 320)
        return self.plot_left, 34, width - self.plot_right, height - 36

    def _axes(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        ticks: list[float],
        dates: pd.Series,
        fmt_value,
        low: float | None = None,
        high: float | None = None,
    ):
        low = ticks[0] if low is None else low
        high = ticks[-1] if high is None else high
        for value in ticks:
            y = y1 - (value - low) / (high - low + 1e-12) * (y1 - y0)
            self.create_line(x0, y, x1, y, fill=GRID, dash=(1, 4))
            self.create_text(x0 - 8, y, text=fmt_value(value), fill=DIM, anchor="e", font=(self.font_family, 8))
        for idx in date_ticks(dates):
            if idx >= len(dates):
                continue
            x = x0 + idx / max(len(dates) - 1, 1) * (x1 - x0)
            self.create_line(x, y1, x, y1 + 4, fill=BORDER)
            self.create_text(
                x,
                y1 + 16,
                text=pd.Timestamp(dates.iloc[idx]).strftime("%Y-%m"),
                fill=DIM,
                font=(self.font_family, 8),
            )
        self.create_line(x0, y1, x1, y1, fill=BORDER)

    def _on_motion(self, event):
        if not self._hover_points:
            return
        x0, y0, x1, y1 = self._box()
        if event.x < x0 or event.x > x1:
            self.delete("hover")
            return
        xs = [p[0] for p in self._hover_points]
        idx = min(max(bisect.bisect_left(xs, event.x), 0), len(xs) - 1)
        if idx > 0 and abs(xs[idx - 1] - event.x) < abs(xs[idx] - event.x):
            idx -= 1
        x, _ = self._hover_points[idx]
        self.delete("hover")
        self.create_line(x, y0, x, y1, fill=ACCENT, dash=(3, 3), tags="hover")
        self._draw_tooltip(x, event.y, idx, x0, x1)


class EquityChart(ChartBase):
    def __init__(self, parent: tk.Misc, font_family: str, settings: UserSettings):
        super().__init__(parent, font_family)
        self.settings = settings

    def redraw(self):
        if self._data is None:
            self._empty("运行回测后显示净值曲线")
            return
        equity = self._data.result.equity
        if equity.empty:
            self._empty("暂无净值数据")
            return
        self.delete("all")
        self._hover_points = []
        width = max(self.winfo_width(), 500)
        height = max(self.winfo_height(), 320)
        x0, top, x1, bottom = self._box()
        draw_top = top
        draw_bottom = bottom
        dd_height = max(int((bottom - top) * 0.22), 56)
        eq_bottom = draw_bottom - dd_height - 26
        dates = pd.to_datetime(equity["date"]).reset_index(drop=True)
        strat = equity["equity"].astype(float)
        bench = equity.get("benchmark_equity", pd.Series(np.nan, index=equity.index)).astype(float)
        values = pd.concat([strat, bench.dropna()], ignore_index=True)
        low = float(values.min())
        high = float(values.max())
        pad = max((high - low) * 0.08, high * 0.005, 1e-6)
        low -= pad
        high += pad
        ticks = nice_ticks(low, high, 5)
        self._axes(x0, draw_top, x1, eq_bottom, ticks, dates, lambda v: f"{v/10000:.1f}万", low, high)
        self.create_text(x0, draw_top - 18, text="策略净值", fill=TEXT, anchor="w", font=(self.font_family, 10, "bold"))
        x_positions = [x0 + i / max(len(dates) - 1, 1) * (x1 - x0) for i in range(len(dates))]
        y_strat = [eq_bottom - (v - low) / (high - low + 1e-12) * (eq_bottom - draw_top) for v in strat]
        area = list(zip(x_positions, y_strat))
        strategy_color = self.settings.strategy_color
        benchmark_color = self.settings.benchmark_color
        drawdown_color = self.settings.drawdown_color
        self.create_polygon(
            [(x0, eq_bottom)] + area + [(x1, eq_bottom)],
            fill=blend_hex(strategy_color, BG, 0.24),
            outline="",
        )
        if bench.notna().any():
            bench_y = [eq_bottom - (v - low) / (high - low + 1e-12) * (eq_bottom - draw_top) if pd.notna(v) else None for v in bench]
            seg = []
            for x, y in zip(x_positions, bench_y):
                if y is None:
                    if len(seg) > 1:
                        self.create_line(seg, fill=benchmark_color, width=1.4, smooth=True)
                    seg = []
                else:
                    seg.append((x, y))
            if len(seg) > 1:
                self.create_line(seg, fill=benchmark_color, width=1.4, smooth=True)
        self.create_line(list(zip(x_positions, y_strat)), fill=strategy_color, width=2.1, smooth=True)
        self.create_line(x0, draw_top, x0, eq_bottom, fill=BORDER)
        legend_y = draw_top + 2
        self.create_line(x1 - 168, legend_y, x1 - 148, legend_y, fill=strategy_color, width=2)
        self.create_text(x1 - 142, legend_y, text="策略", fill=MUTED, anchor="w", font=(self.font_family, 8))
        self.create_line(x1 - 90, legend_y, x1 - 70, legend_y, fill=benchmark_color, width=2)
        self.create_text(x1 - 64, legend_y, text="基准", fill=MUTED, anchor="w", font=(self.font_family, 8))
        dd_top = eq_bottom + 26
        dd_bottom = draw_bottom - 16
        dd = (strat / strat.cummax() - 1.0) * 100.0
        dd_low = min(float(dd.min()), -0.1)
        self.create_text(x0, dd_top - 12, text="回撤", fill=MUTED, anchor="w", font=(self.font_family, 9))
        for value in nice_ticks(dd_low, 0.0, 3):
            y = dd_bottom - (value - dd_low) / (0.0 - dd_low + 1e-12) * (dd_bottom - dd_top)
            self.create_line(x0, y, x1, y, fill=GRID, dash=(1, 4))
            self.create_text(x0 - 8, y, text=f"{value:.0f}%", fill=DIM, anchor="e", font=(self.font_family, 8))
        dd_y = [dd_bottom - (v - dd_low) / (0.0 - dd_low + 1e-12) * (dd_bottom - dd_top) for v in dd]
        self.create_polygon(
            [(x0, dd_top)] + list(zip(x_positions, dd_y)) + [(x1, dd_top)],
            fill=blend_hex(drawdown_color, BG, 0.22),
            outline="",
        )
        self.create_line(list(zip(x_positions, dd_y)), fill=drawdown_color, width=1.2)
        self.create_line(x0, dd_top, x0, dd_bottom, fill=BORDER)
        self._hover_points = list(zip(x_positions, y_strat))
        self._dates = dates
        self._strat = strat
        self._bench = bench
        self._dd = dd

    def _draw_tooltip(self, x, y, idx, x0, x1):
        dates = self._dates
        date = pd.Timestamp(dates.iloc[idx])
        equity = float(self._strat.iloc[idx])
        bench = self._bench.iloc[idx]
        dd = float(self._dd.iloc[idx])
        lines = [
            date.strftime("%Y-%m-%d"),
            f"策略 {equity:,.0f}",
            f"基准 {bench:,.0f}" if pd.notna(bench) else "基准 --",
            f"回撤 {dd:.2f}%",
        ]
        text = "\n".join(lines)
        width = 148
        height = 22 + 16 * len(lines)
        tx = x + 12 if x + width + 12 < x1 else x - width - 12
        ty = max(36, min(y - height / 2, self.winfo_height() - height - 12))
        self.create_rectangle(tx, ty, tx + width, ty + height, fill=CARD_ALT, outline=BORDER, tags="hover")
        self.create_text(
            tx + 10,
            ty + 8,
            text=text,
            fill=TEXT,
            anchor="nw",
            font=(self.font_family, 8),
            tags="hover",
        )


class KLineChart(ChartBase):
    def __init__(self, parent: tk.Misc, font_family: str, settings: UserSettings):
        super().__init__(parent, font_family)
        self.settings = settings
        self.view_start = 0
        self.view_end = 0
        self.initial_span = 160
        self.min_span = 20
        self._drag_origin = None
        self._drag_start_view = None
        self._dragging = False
        self._view_meta: dict[str, Any] = {}
        self.bind("<MouseWheel>", self._on_zoom, add="+")
        self.bind("<Button-4>", self._on_zoom, add="+")
        self.bind("<Button-5>", self._on_zoom, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<B1-Motion>", self._on_drag, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")

    def set_data(self, output: RunOutput | None):
        self._data = output
        if output is not None and output.bars is not None and not output.bars.empty:
            n = len(output.bars)
            self.view_end = n
            self.view_start = max(0, n - self.initial_span)
        else:
            self.view_start = 0
            self.view_end = 0
        self.redraw()

    def _visible_range(self) -> tuple[int, int, int]:
        total = len(self._data.bars) if self._data is not None and self._data.bars is not None else 0
        if total <= 0:
            return 0, 0, 0
        span = max(self.min_span, min(self.view_end - self.view_start, total))
        start = max(0, min(self.view_start, total - span))
        end = min(total, start + span)
        return start, end, total

    def _on_zoom(self, event):
        if self._data is None or self._data.bars is None or self._data.bars.empty:
            return "break"
        start, end, total = self._visible_range()
        if total <= self.min_span:
            return "break"
        direction = getattr(event, "num", None)
        if direction == 4:
            zoom_in = True
        elif direction == 5:
            zoom_in = False
        else:
            zoom_in = getattr(event, "delta", 0) > 0
        span = end - start
        factor = 0.78 if zoom_in else 1.28
        new_span = max(self.min_span, min(total, int(round(span * factor))))
        if new_span == span:
            return "break"
        x0, _, x1, _ = self._box()
        fraction = min(max((event.x - x0) / max(x1 - x0, 1), 0.0), 1.0)
        anchor = start + fraction * span
        new_start = int(round(anchor - fraction * new_span))
        new_start = max(0, min(new_start, total - new_span))
        self.view_start = new_start
        self.view_end = new_start + new_span
        self.redraw()
        return "break"

    def _on_press(self, event):
        if self._data is None or self._data.bars is None or self._data.bars.empty:
            return
        self._drag_origin = event.x
        start, end, _ = self._visible_range()
        self._drag_start_view = (start, end)
        self._dragging = True
        self.configure(cursor="fleur")
        self.delete("hover")

    def _on_drag(self, event):
        if not self._dragging or self._drag_origin is None or self._drag_start_view is None:
            return
        if self._data is None or self._data.bars is None or self._data.bars.empty:
            return
        start, end, total = self._visible_range()
        span = end - start
        x0, _, x1, _ = self._box()
        dx = event.x - self._drag_origin
        shift = int(round(dx / max(x1 - x0, 1) * span))
        new_start = self._drag_start_view[0] - shift
        new_start = max(0, min(new_start, max(0, total - span)))
        self.view_start = new_start
        self.view_end = new_start + span
        self.redraw()

    def _on_release(self, _event=None):
        self._dragging = False
        self._drag_origin = None
        self._drag_start_view = None
        self.configure(cursor="")
        self.delete("hover")

    def _map_y(self, value: float, low: float, high: float, top: float, bottom: float) -> float:
        return bottom - (value - low) / (high - low + 1e-12) * (bottom - top)

    def _line_segments(self, values: list[float | None], xs: list[float], color: str, width: float):
        segment: list[tuple[float, float]] = []
        for x, value in zip(xs, values):
            if value is None or not math.isfinite(float(value)):
                if len(segment) > 1:
                    self.create_line(segment, fill=color, width=width, smooth=True)
                segment = []
            else:
                segment.append((x, float(value)))
        if len(segment) > 1:
            self.create_line(segment, fill=color, width=width, smooth=True)

    def redraw(self):
        if self._data is None:
            self._empty("运行回测后显示 K 线与交易点")
            return
        bars = self._data.bars
        if bars is None or bars.empty:
            self._empty("暂无 K 线数据")
            return
        start, end, total = self._visible_range()
        if end - start < 2:
            self._empty("K 线数据不足")
            return
        self.delete("all")
        self._hover_points = []
        width = max(self.winfo_width(), 500)
        height = max(self.winfo_height(), 320)
        x0, top, x1, bottom = self._box()
        chart_bottom = height - 34
        vol_height = max(46, int((chart_bottom - top) * 0.18))
        vol_bottom = chart_bottom - 6
        vol_top = vol_bottom - vol_height
        price_bottom = vol_top - 24
        price_top = top
        visible = bars.iloc[start:end].reset_index(drop=True)
        dates = pd.to_datetime(visible["date"]).reset_index(drop=True)
        count = len(visible)
        opens = visible["open"].astype(float).to_numpy()
        highs = visible["high"].astype(float).to_numpy()
        lows = visible["low"].astype(float).to_numpy()
        closes = visible["close"].astype(float).to_numpy()
        volumes = visible["volume"].astype(float).to_numpy()
        if highs.max() <= 0 or closes.max() <= 0:
            self._empty("K 线价格无效")
            return
        price_low = float(lows.min())
        price_high = float(highs.max())
        pad = max((price_high - price_low) * 0.08, price_high * 0.005, 1e-6)
        price_low -= pad
        price_high += pad
        x_positions = [x0 + (i + 0.5) / count * (x1 - x0) for i in range(count)]
        candle_width = max(1.0, min(14.0, (x1 - x0) / count * 0.62))
        price_ticks = nice_ticks(price_low, price_high, 5)
        for value in price_ticks:
            y = self._map_y(value, price_low, price_high, price_top, price_bottom)
            self.create_line(x0, y, x1, y, fill=GRID, dash=(1, 4))
            self.create_text(x0 - 8, y, text=f"{value:.2f}", fill=DIM, anchor="e", font=(self.font_family, 8))
        date_positions = date_ticks(dates, 6)
        for idx in date_positions:
            x = x_positions[idx]
            self.create_line(x, price_top, x, vol_bottom, fill=GRID, dash=(1, 5))
            self.create_text(
                x,
                chart_bottom + 16,
                text=pd.Timestamp(dates.iloc[idx]).strftime("%Y-%m-%d"),
                fill=MUTED,
                font=(self.font_family, 8),
            )
        self.create_line(x0, chart_bottom, x1, chart_bottom, fill=BORDER)
        self.create_line(x0, price_top, x0, vol_bottom, fill=BORDER)
        self.create_line(x0, vol_top - 10, x1, vol_top - 10, fill=BORDER)
        self.create_text(x0, price_top - 18, text="K 线与交易点", fill=TEXT, anchor="w", font=(self.font_family, 10, "bold"))
        self.create_text(
            x1,
            price_top - 18,
            text="滚轮缩放 · 拖拽平移 · 显示 %d/%d 根" % (count, total),
            fill=MUTED,
            anchor="e",
            font=(self.font_family, 8),
        )
        for i in range(count):
            x = x_positions[i]
            color = self.settings.rise_color if closes[i] >= opens[i] else self.settings.fall_color
            y_high = self._map_y(highs[i], price_low, price_high, price_top, price_bottom)
            y_low = self._map_y(lows[i], price_low, price_high, price_top, price_bottom)
            y_open = self._map_y(opens[i], price_low, price_high, price_top, price_bottom)
            y_close = self._map_y(closes[i], price_low, price_high, price_top, price_bottom)
            self.create_line(x, y_high, x, y_low, fill=color, width=max(1, int(candle_width * 0.18)))
            if candle_width >= 2.4:
                body_top = min(y_open, y_close)
                body_bottom = max(y_open, y_close)
                if body_bottom - body_top < 1.2:
                    body_bottom = body_top + 1.2
                self.create_rectangle(
                    x - candle_width / 2,
                    body_top,
                    x + candle_width / 2,
                    body_bottom,
                    fill=color,
                    outline=color,
                )
        close_full = bars["close"].astype(float).reset_index(drop=True)
        ma_specs = list(zip(self.settings.ma_periods, self.settings.ma_colors))
        for window, color in ma_specs:
            if int(window) <= 0:
                continue
            if self.settings.ma_type == "EMA":
                ma = close_full.ewm(span=int(window), adjust=False, min_periods=int(window)).mean()
            else:
                ma = close_full.rolling(int(window), min_periods=int(window)).mean()
            ma = ma.iloc[start:end].reset_index(drop=True)
            values = [
                self._map_y(float(value), price_low, price_high, price_top, price_bottom) if pd.notna(value) else None
                for value in ma
            ]
            self._line_segments(values, x_positions, color, 1.3)
        max_volume = float(volumes.max()) if len(volumes) else 0.0
        if max_volume > 0:
            rise_volume = blend_hex(self.settings.rise_color, BG, 0.68)
            fall_volume = blend_hex(self.settings.fall_color, BG, 0.68)
            for i in range(count):
                height_px = max(1.0, volumes[i] / max_volume * (vol_bottom - vol_top - 2))
                color = rise_volume if closes[i] >= opens[i] else fall_volume
                x = x_positions[i]
                self.create_line(x, vol_bottom, x, vol_bottom - height_px, fill=color, width=max(1, int(candle_width * 0.6)))
            self.create_text(x0 - 8, vol_top, text="量", fill=DIM, anchor="e", font=(self.font_family, 8))
            self.create_text(x0 - 8, vol_bottom, text="", fill=DIM, anchor="e", font=(self.font_family, 8))
        trades = self._data.result.trades
        date_to_index = {pd.Timestamp(d).normalize(): i for i, d in enumerate(pd.to_datetime(bars["date"]))}
        for row in trades.itertuples(index=False):
            for day, is_entry in ((row.entry_date, True), (row.exit_date, False)):
                full_idx = date_to_index.get(pd.Timestamp(day).normalize())
                if full_idx is None or full_idx < start or full_idx >= end:
                    continue
                x = x_positions[full_idx - start]
                bar = bars.iloc[full_idx]
                color = self.settings.buy_color if is_entry else self.settings.sell_color
                if is_entry:
                    y = self._map_y(float(bar["low"]), price_low, price_high, price_top, price_bottom) + 18
                    y = min(y, price_bottom - 10)
                    self.create_polygon(x, y - 8, x - 6, y + 3, x + 6, y + 3, fill=color, outline=BG, width=1)
                    self.create_text(x + 9, y - 2, text="B", fill=color, anchor="w", font=(self.font_family, 8, "bold"))
                else:
                    y = self._map_y(float(bar["high"]), price_low, price_high, price_top, price_bottom) - 18
                    y = max(y, price_top + 10)
                    self.create_polygon(x, y + 8, x - 6, y - 3, x + 6, y - 3, fill=color, outline=BG, width=1)
                    self.create_text(x + 9, y + 2, text="S", fill=color, anchor="w", font=(self.font_family, 8, "bold"))
        legend_y = price_top + 2
        legend_items = [(self.settings.buy_color, "买入"), (self.settings.sell_color, "卖出")]
        for period, color in zip(self.settings.ma_periods, self.settings.ma_colors):
            legend_items.append((color, f"{self.settings.ma_type}{int(period)}"))
        offset = x1 - 262
        for color, label in legend_items:
            if label == "买入":
                self.create_polygon(offset, legend_y - 5, offset - 4, legend_y + 4, offset + 4, legend_y + 4, fill=color, outline="")
                self.create_text(offset + 8, legend_y, text=label, fill=MUTED, anchor="w", font=(self.font_family, 8))
                offset += 48
            elif label == "卖出":
                self.create_polygon(offset, legend_y + 5, offset - 4, legend_y - 4, offset + 4, legend_y - 4, fill=color, outline="")
                self.create_text(offset + 8, legend_y, text=label, fill=MUTED, anchor="w", font=(self.font_family, 8))
                offset += 48
            else:
                self.create_line(offset, legend_y, offset + 14, legend_y, fill=color, width=2)
                self.create_text(offset + 18, legend_y, text=label, fill=MUTED, anchor="w", font=(self.font_family, 8))
                offset += 44
        self._view_meta = {
            "x0": x0, "x1": x1, "top": price_top, "price_bottom": price_bottom,
            "vol_top": vol_top, "vol_bottom": vol_bottom, "start": start, "end": end,
            "x_positions": x_positions, "visible": visible, "price_low": price_low,
            "price_high": price_high, "count": count,
        }
        self._hover_points = list(zip(x_positions, [self._map_y(float(c), price_low, price_high, price_top, price_bottom) for c in closes]))
        self._full_bars = bars.reset_index(drop=True)

    def _on_motion(self, event):
        if self._dragging or not self._view_meta:
            return
        meta = self._view_meta
        x0, x1 = meta["x0"], meta["x1"]
        if event.x < x0 or event.x > x1:
            self.delete("hover")
            return
        count = meta["count"]
        start = meta["start"]
        fraction = (event.x - x0) / max(x1 - x0, 1)
        local_idx = min(max(int(fraction * count), 0), count - 1)
        full_idx = start + local_idx
        x = meta["x_positions"][local_idx]
        self.delete("hover")
        if meta["top"] <= event.y <= meta["vol_bottom"]:
            self.create_line(x, meta["top"], x, meta["vol_bottom"], fill=ACCENT, dash=(2, 3), tags="hover")
        if meta["top"] <= event.y <= meta["price_bottom"]:
            self.create_line(x0, event.y, x1, event.y, fill=ACCENT, dash=(2, 3), tags="hover")
        bars = self._full_bars
        row = bars.iloc[full_idx]
        prev_close = float(bars.iloc[full_idx - 1]["close"]) if full_idx > 0 else float(row["close"])
        change = (float(row["close"]) / prev_close - 1.0) * 100.0 if prev_close else 0.0
        volume = float(row["volume"])
        volume_text = f"{volume / 1e8:.2f}亿" if volume >= 1e8 else (f"{volume / 1e4:.1f}万" if volume >= 1e4 else f"{volume:.0f}")
        lines = [
            pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
            f"开 {float(row['open']):.2f}   高 {float(row['high']):.2f}",
            f"低 {float(row['low']):.2f}   收 {float(row['close']):.2f}",
            f"涨跌 {change:+.2f}%   量 {volume_text}",
        ]
        text_width = 178
        text_height = 72
        tx = x + 14 if x + text_width + 14 < x1 else x - text_width - 14
        ty = max(meta["top"] + 6, min(event.y - text_height / 2, meta["price_bottom"] - text_height - 6))
        self.create_rectangle(tx, ty, tx + text_width, ty + text_height, fill=CARD_ALT, outline=ACCENT, tags="hover")
        self.create_text(tx + 10, ty + 8, text="\n".join(lines), fill=TEXT, anchor="nw", font=(self.font_family, 8), tags="hover")



class IndicatorPicker(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        font_family: str,
        background: str = PANEL,
        initial: dict[str, Any] | None = None,
        on_change=None,
        indicator_defaults: dict[str, dict[str, Any]] | None = None,
    ):
        super().__init__(parent, bg=background)
        self.font_family = font_family
        self.background = background
        self.on_change = on_change
        self.indicator_defaults = indicator_defaults or {}
        self.specs = list(all_indicators())
        self.by_label = {spec.label: spec for spec in self.specs}
        self.var = tk.StringVar()
        self.combo = ttk.Combobox(
            self,
            textvariable=self.var,
            values=list(self.by_label),
            state="readonly",
            width=20,
        )
        self.combo.pack(side="left")
        self.combo.bind("<<ComboboxSelected>>", self._selected)
        self.params_frame = tk.Frame(self, bg=background)
        self.params_frame.pack(side="left", padx=(5, 0))
        self.param_vars: dict[str, tk.StringVar] = {}
        self.set_config(initial or {"indicator": "close", "params": {}})

    def _selected(self, _event=None):
        self._build_params()
        if self.on_change:
            self.on_change()

    def _build_params(self, values: dict[str, Any] | None = None):
        for child in self.params_frame.winfo_children():
            child.destroy()
        self.param_vars = {}
        spec = self.by_label.get(self.var.get())
        if spec is None:
            return
        values = {**self.indicator_defaults.get(spec.key, {}), **(values or {})}
        for param in spec.params:
            tk.Label(
                self.params_frame,
                text=param.label,
                bg=self.background,
                fg=DIM,
                font=(self.font_family, 7),
            ).pack(side="left", padx=(4, 1))
            var = tk.StringVar(value=str(values.get(param.key, param.default)))
            ttk.Entry(self.params_frame, textvariable=var, width=5).pack(side="left")
            self.param_vars[param.key] = var
        if not spec.params:
            tk.Label(
                self.params_frame,
                text="无参数",
                bg=self.background,
                fg=DIM,
                font=(self.font_family, 7),
            ).pack(side="left", padx=(3, 0))

    def set_config(self, config: dict[str, Any]):
        spec = get_indicator(config.get("indicator", "close"))
        if spec is None:
            spec = self.specs[0]
        self.var.set(spec.label)
        self._build_params(config.get("params") or {})

    def get_config(self) -> dict[str, Any]:
        spec = self.by_label.get(self.var.get())
        if spec is None:
            raise ValueError("请选择指标")
        params: dict[str, Any] = {}
        for param in spec.params:
            var = self.param_vars.get(param.key)
            if var is None:
                params[param.key] = param.default
                continue
            try:
                params[param.key] = param.normalize(var.get())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{spec.name} 的 {param.label} 无效") from exc
        return {"indicator": spec.key, "params": params}


class ConditionRow(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        font_family: str,
        index: int,
        on_remove,
        initial: dict[str, Any] | None = None,
        indicator_defaults: dict[str, dict[str, Any]] | None = None,
    ):
        super().__init__(
            parent,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
        )
        self.font_family = font_family
        self.on_remove = on_remove
        self._right_picker: IndicatorPicker | None = None
        self._right_value_vars: dict[str, tk.StringVar] = {}
        self._right_kind = "value"
        self._op_key = "gt"
        self._initial = initial
        self.indicator_defaults = indicator_defaults or {}
        top = tk.Frame(self, bg=CARD)
        top.pack(fill="x", padx=8, pady=(8, 2))
        self.number_label = tk.Label(
            top,
            text=f"条件 {index + 1}",
            bg=CARD,
            fg=MUTED,
            width=7,
            anchor="w",
            font=(font_family, 9, "bold"),
        )
        self.number_label.pack(side="left")
        self.left_picker = IndicatorPicker(top, font_family, background=CARD, indicator_defaults=self.indicator_defaults)
        self.left_picker.pack(side="left")
        self.op_var = tk.StringVar(value=OP_LABELS["gt"])
        self.op_combo = ttk.Combobox(
            top,
            textvariable=self.op_var,
            values=[label for _, label in OPERATORS],
            state="readonly",
            width=14,
        )
        self.op_combo.pack(side="left", padx=(8, 0))
        self.op_combo.bind("<<ComboboxSelected>>", self._op_changed)
        self.right_type_var = tk.StringVar(value=RIGHT_KINDS[0][1])
        self.right_type_combo = ttk.Combobox(
            top,
            textvariable=self.right_type_var,
            values=[label for _, label in RIGHT_KINDS],
            state="readonly",
            width=7,
        )
        self.right_type_combo.pack(side="left", padx=(6, 0))
        self.right_type_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_right())
        remove = tk.Button(
            top,
            text="删除",
            command=lambda: self.on_remove(self),
            bg=CARD_ALT,
            fg=MUTED,
            activebackground=RED,
            activeforeground="#000000",
            relief="flat",
            bd=0,
            padx=8,
            pady=4,
            cursor="hand2",
            font=(font_family, 8),
        )
        remove.pack(side="right")
        self.right_holder = tk.Frame(self, bg=CARD)
        self.right_holder.pack(fill="x", padx=(80, 8), pady=(2, 8))
        if initial:
            self.set_condition(initial)
        else:
            self._refresh_right()

    def _op_changed(self, _event=None):
        key = self._operator_key()
        self._op_key = key
        if key == "between":
            self._right_kind = "value"
            self.right_type_var.set(RIGHT_KINDS[0][1])
        self._refresh_right()

    def _operator_key(self) -> str:
        label = self.op_var.get()
        for key, name in OPERATORS:
            if name == label:
                return key
        return "gt"

    def _right_kind_key(self) -> str:
        label = self.right_type_var.get()
        for key, name in RIGHT_KINDS:
            if name == label:
                return key
        return "value"

    def _refresh_right(self, right: dict[str, Any] | None = None):
        for child in self.right_holder.winfo_children():
            child.destroy()
        self._right_picker = None
        self._right_value_vars = {}
        op = self._operator_key()
        if op in ("rising", "falling"):
            self.right_type_combo.configure(state="disabled")
            tk.Label(
                self.right_holder,
                text="该操作符只需左侧指标，无需比较对象",
                bg=CARD,
                fg=DIM,
                font=(self.font_family, 8),
            ).pack(side="left")
            return
        kind = self._right_kind_key()
        if op == "between":
            kind = "value"
            self.right_type_var.set(RIGHT_KINDS[0][1])
            self.right_type_combo.configure(state="disabled")
        else:
            self.right_type_combo.configure(state="readonly")
        self._right_kind = kind
        if kind == "value":
            tk.Label(self.right_holder, text="数值", bg=CARD, fg=MUTED, font=(self.font_family, 8)).pack(side="left")
            initial_values = right or {}
            var1 = tk.StringVar(value=str(initial_values.get("value", 0)))
            ttk.Entry(self.right_holder, textvariable=var1, width=11).pack(side="left", padx=(5, 0))
            self._right_value_vars["value"] = var1
            if op == "between":
                tk.Label(self.right_holder, text="~", bg=CARD, fg=MUTED, font=(self.font_family, 8)).pack(side="left", padx=5)
                var2 = tk.StringVar(value=str(initial_values.get("value2", 100)))
                ttk.Entry(self.right_holder, textvariable=var2, width=11).pack(side="left")
                self._right_value_vars["value2"] = var2
        else:
            initial = right if right and right.get("type") == "indicator" else None
            picker = IndicatorPicker(self.right_holder, self.font_family, background=CARD, initial=initial, indicator_defaults=self.indicator_defaults)
            picker.pack(side="left")
            self._right_picker = picker

    def set_condition(self, condition: dict[str, Any]):
        self.left_picker.set_config(condition.get("left") or {"indicator": "close", "params": {}})
        op = condition.get("op", "gt")
        self.op_var.set(OP_LABELS.get(op, OP_LABELS["gt"]))
        right = condition.get("right")
        if op in ("rising", "falling"):
            self.right_type_var.set(RIGHT_KINDS[0][1])
        elif right and right.get("type") == "indicator":
            self.right_type_var.set(RIGHT_KINDS[1][1])
        else:
            self.right_type_var.set(RIGHT_KINDS[0][1])
        self._refresh_right(right)

    def get_condition(self) -> dict[str, Any]:
        left = self.left_picker.get_config()
        op = self._operator_key()
        right = None
        if op not in ("rising", "falling"):
            if self._right_kind == "indicator":
                if self._right_picker is None:
                    raise ValueError("请设置右侧比较指标")
                right = {"type": "indicator", **self._right_picker.get_config()}
            else:
                try:
                    value = float(self._right_value_vars["value"].get())
                except (KeyError, TypeError, ValueError) as exc:
                    raise ValueError("请填写有效的比较数值") from exc
                right = {"type": "value", "value": value}
                if op == "between":
                    try:
                        right["value2"] = float(self._right_value_vars["value2"].get())
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError("请填写有效的区间上限") from exc
        return normalize_condition({"left": left, "op": op, "right": right})

    def renumber(self, index: int):
        self.number_label.configure(text=f"条件 {index + 1}")


class IndicatorCatalogWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, font_family: str):
        super().__init__(parent)
        self.font_family = font_family
        self.title("指标库")
        self.configure(bg=BG)
        width, height = 880, 620
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{max((screen_w - width) // 2, 20)}+{max((screen_h - height) // 2, 20)}")
        self.minsize(680, 420)
        self.transient(parent)
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="指标库", bg=BG, fg=TEXT, font=(font_family, 16, "bold")).pack(side="left")
        tk.Label(
            header,
            text=f"共 {len(all_indicators())} 个指标，可直接在条件行中选择",
            bg=BG,
            fg=MUTED,
            font=(font_family, 9),
        ).pack(side="left", padx=(14, 0))
        wrapper = tk.Frame(self, bg=CARD)
        wrapper.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        columns = ("group", "name", "params", "description")
        tree = ttk.Treeview(wrapper, columns=columns, show="headings", style="Treeview")
        for col, title, width in (
            ("group", "分组", 80),
            ("name", "指标", 180),
            ("params", "参数", 180),
            ("description", "说明", 420),
        ):
            tree.heading(col, text=title, anchor="w")
            tree.column(col, width=width, anchor="w", stretch=col == "description")
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for spec in sorted(all_indicators(), key=lambda item: (item.group, item.name)):
            params = ", ".join(f"{param.label}={param.default}" for param in spec.params) or "无"
            tree.insert("", "end", values=(spec.group, spec.name, params, spec.description))
        tk.Button(
            self,
            text="关闭",
            command=self.destroy,
            bg=ACCENT,
            fg="#000000",
            activebackground=ACCENT_HOVER,
            activeforeground="#000000",
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            cursor="hand2",
            font=(font_family, 9, "bold"),
        ).pack(pady=(0, 16))


class CustomStrategyEditor(tk.Toplevel):
    def __init__(self, app, initial: dict[str, Any] | None = None):
        super().__init__(app)
        self.app = app
        self.font_family = app.font_family
        self.current_slug: str | None = None
        self.current_definition: dict[str, Any] | None = None
        self.indicator_defaults = getattr(getattr(app, "settings", None), "indicator_defaults", {})
        self.entry_rows: list[ConditionRow] = []
        self.exit_rows: list[ConditionRow] = []
        self.loaded_definitions: list[dict[str, Any]] = []
        self._loading_list = False
        self.title("自定义策略编辑器")
        self.configure(bg=BG)
        width, height = 1320, 840
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max((screen_w - width) // 2, 20)
        y = max((screen_h - height) // 2 - 10, 20)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(1100, 720)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh_saved_list()
        if initial:
            self.load_definition(initial)
        else:
            self.new_strategy()

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 8))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="自定义策略编辑器", bg=BG, fg=TEXT, font=(self.font_family, 18, "bold")).pack(anchor="w")
        tk.Label(
            titles,
            text="任意组合指标、比较对象和 AND/OR 逻辑；信号在 T 日收盘生成，T+1 开盘成交。",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
        ).pack(anchor="w", pady=(2, 0))
        actions = tk.Frame(header, bg=BG)
        actions.pack(side="right", pady=(5, 0))
        for text, command, kind in (
            ("指标库", self.open_catalog, "ghost"),
            ("校验条件", self.validate_form, "ghost"),
            ("另存为", self.save_as, "ghost"),
            ("删除", self.delete_current, "ghost"),
            ("保存并应用", self.save, "primary"),
            ("关闭", self.close, "ghost"),
        ):
            if kind == "primary":
                button = self.app._primary_button(actions, text, command)
            else:
                button = self.app._ghost_button(actions, text, command)
            button.pack(side="left", padx=(6, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18)
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=12)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)
        left = tk.Frame(body, bg=PANEL, width=340, highlightbackground=BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        left.grid_propagate(False)
        tk.Label(left, text="已保存策略", bg=PANEL, fg=TEXT, font=(self.font_family, 11, "bold"), anchor="w").pack(fill="x", padx=12, pady=(12, 2))
        tk.Label(
            left,
            text="选择后载入编辑；保存后会出现在主界面策略下拉框。",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            wraplength=306,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=12)
        list_frame = tk.Frame(left, bg=PANEL)
        list_frame.pack(fill="both", expand=True, padx=10, pady=8)
        self.saved_list = tk.Listbox(
            list_frame,
            bg=CARD,
            fg=TEXT,
            selectbackground=ACCENT,
            selectforeground="#000000",
            activestyle="none",
            highlightthickness=0,
            bd=0,
            font=(self.font_family, 9),
            exportselection=False,
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.saved_list.yview)
        self.saved_list.configure(yscrollcommand=scroll.set)
        self.saved_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.saved_list.bind("<<ListboxSelect>>", self._on_saved_select)
        tk.Button(
            left,
            text="新建策略",
            command=self.new_strategy,
            bg=ACCENT,
            fg="#000000",
            activebackground=ACCENT_HOVER,
            activeforeground="#000000",
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            cursor="hand2",
            font=(self.font_family, 9, "bold"),
        ).pack(fill="x", padx=12, pady=(0, 12))
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        self.form_scroll = ScrollableFrame(right, PANEL)
        self.form_scroll.pack(fill="both", expand=True)
        self.app.register_scroll(self.form_scroll)
        form = self.form_scroll.inner
        form_inner = tk.Frame(form, bg=PANEL)
        form_inner.pack(fill="both", expand=True, padx=18, pady=16)
        name_row = tk.Frame(form_inner, bg=PANEL)
        name_row.pack(fill="x", pady=(0, 8))
        tk.Label(name_row, text="策略名称", bg=PANEL, fg=MUTED, width=10, anchor="w", font=(self.font_family, 9)).pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.name_var, width=40).pack(side="left", fill="x", expand=True)
        desc_row = tk.Frame(form_inner, bg=PANEL)
        desc_row.pack(fill="x", pady=(0, 16))
        tk.Label(desc_row, text="说明", bg=PANEL, fg=MUTED, width=10, anchor="w", font=(self.font_family, 9)).pack(side="left")
        self.desc_var = tk.StringVar()
        ttk.Entry(desc_row, textvariable=self.desc_var, width=80).pack(side="left", fill="x", expand=True)
        self._build_condition_section(form_inner, "入场条件", "entry")
        self._build_condition_section(form_inner, "离场条件", "exit")
        hint = tk.Label(
            form_inner,
            text="提示：单击左侧已保存策略即可载入；条件按 T 日收盘计算，回测引擎负责 T+1、整手、费用与风控。",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
            wraplength=900,
        )
        hint.pack(fill="x", pady=(14, 6))
        footer = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        self.status_label = tk.Label(
            footer,
            text="就绪",
            bg=PANEL,
            fg=MUTED,
            font=(self.font_family, 8),
            anchor="w",
            padx=12,
            pady=7,
        )
        self.status_label.pack(side="left")
        tk.Label(
            footer,
            text="条件最多各 20 条 · 指标参数支持自定义",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            padx=12,
            pady=7,
        ).pack(side="right")

    def _build_condition_section(self, parent: tk.Misc, title: str, kind: str):
        section = tk.Frame(parent, bg=PANEL)
        section.pack(fill="x", pady=(0, 14))
        head = tk.Frame(section, bg=PANEL)
        head.pack(fill="x", pady=(0, 6))
        tk.Label(head, text=title, bg=PANEL, fg=TEXT, font=(self.font_family, 12, "bold")).pack(side="left")
        logic_var = tk.StringVar(value=LOGIC_LABELS["all"])
        if kind == "entry":
            self.entry_logic_var = logic_var
        else:
            self.exit_logic_var = logic_var
        tk.Label(head, text="条件逻辑", bg=PANEL, fg=DIM, font=(self.font_family, 8)).pack(side="left", padx=(14, 5))
        logic = ttk.Combobox(
            head,
            textvariable=logic_var,
            values=list(LOGIC_LABELS.values()),
            state="readonly",
            width=18,
        )
        logic.pack(side="left")
        button = tk.Button(
            head,
            text="+ 添加条件",
            command=lambda: self.add_condition(kind),
            bg=CARD_ALT,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=12,
            pady=5,
            cursor="hand2",
            font=(self.font_family, 9),
        )
        button.pack(side="right")
        rows = tk.Frame(section, bg=PANEL)
        rows.pack(fill="x")
        if kind == "entry":
            self.entry_rows_frame = rows
        else:
            self.exit_rows_frame = rows

    def add_condition(self, kind: str, initial: dict[str, Any] | None = None):
        rows_frame = self.entry_rows_frame if kind == "entry" else self.exit_rows_frame
        rows = self.entry_rows if kind == "entry" else self.exit_rows
        if len(rows) >= 20:
            self.status_label.configure(text="每类条件最多 20 条", fg=AMBER)
            return
        if initial is None:
            ma_period = int(self.indicator_defaults.get("ma", {}).get("period", 20))
            if kind == "entry":
                initial = {
                    "left": {"indicator": "close", "params": {}},
                    "op": "gt",
                    "right": {"type": "indicator", "indicator": "ma", "params": {"period": ma_period}},
                }
            else:
                initial = {
                    "left": {"indicator": "close", "params": {}},
                    "op": "lt",
                    "right": {"type": "indicator", "indicator": "ma", "params": {"period": ma_period}},
                }
        row = ConditionRow(
            rows_frame,
            self.font_family,
            len(rows),
            lambda item: self.remove_condition(kind, item),
            initial,
            indicator_defaults=self.indicator_defaults,
        )
        row.pack(fill="x", pady=(0, 7))
        rows.append(row)
        self._renumber(kind)
        self._on_form_change()

    def remove_condition(self, kind: str, row: ConditionRow):
        rows = self.entry_rows if kind == "entry" else self.exit_rows
        if row in rows:
            rows.remove(row)
            row.destroy()
            self._renumber(kind)
            self._on_form_change()

    def _renumber(self, kind: str):
        rows = self.entry_rows if kind == "entry" else self.exit_rows
        for index, row in enumerate(rows):
            row.renumber(index)

    def _on_form_change(self):
        self.status_label.configure(text="有未保存修改", fg=AMBER)

    def _set_conditions(self, kind: str, conditions: list[dict[str, Any]]):
        rows = self.entry_rows if kind == "entry" else self.exit_rows
        for row in rows:
            row.destroy()
        rows.clear()
        for condition in conditions:
            self.add_condition(kind, condition)
        self._renumber(kind)

    def _load_logic(self, kind: str, value: str):
        var = self.entry_logic_var if kind == "entry" else self.exit_logic_var
        var.set(LOGIC_LABELS.get(value, LOGIC_LABELS["all"]))

    def _current_logic(self, kind: str) -> str:
        var = self.entry_logic_var if kind == "entry" else self.exit_logic_var
        return LOGIC_VALUES.get(var.get(), "all")

    def new_strategy(self):
        self.current_slug = None
        self.current_definition = None
        self.name_var.set("新建自定义策略")
        self.desc_var.set("")
        self._load_logic("entry", "all")
        self._load_logic("exit", "all")
        self._set_conditions("entry", [])
        self._set_conditions("exit", [])
        self.add_condition("entry")
        self.add_condition("exit")
        self.saved_list.selection_clear(0, "end")
        self.status_label.configure(text="新策略：填写名称与条件后保存", fg=MUTED)

    def load_definition(self, definition: dict[str, Any]):
        self.current_slug = definition.get("slug") or None
        self.current_definition = definition
        self.name_var.set(definition.get("name", "未命名策略"))
        self.desc_var.set(definition.get("description", ""))
        self._load_logic("entry", definition.get("entry_logic", "all"))
        self._load_logic("exit", definition.get("exit_logic", "all"))
        self._set_conditions("entry", definition.get("entry_conditions") or [])
        self._set_conditions("exit", definition.get("exit_conditions") or [])
        self.status_label.configure(text=f"已载入：{definition.get('name', '')}", fg=MUTED)
        self._select_saved(self.current_slug)

    def _select_saved(self, slug: str | None):
        if not slug:
            self.saved_list.selection_clear(0, "end")
            return
        for index, definition in enumerate(self.loaded_definitions):
            if definition.get("slug") == slug:
                self._loading_list = True
                self.saved_list.selection_clear(0, "end")
                self.saved_list.selection_set(index)
                self.saved_list.see(index)
                self._loading_list = False
                return

    def refresh_saved_list(self):
        definitions, errors = list_definitions()
        self.loaded_definitions = definitions
        self.saved_list.delete(0, "end")
        for definition in definitions:
            name = str(definition.get("name", "未命名"))
            display = name if len(name) <= 24 else name[:23] + "…"
            self.saved_list.insert("end", display)
        if errors:
            self.status_label.configure(text=f"有 {len(errors)} 个自定义策略文件无法载入", fg=AMBER)
        if self.current_slug:
            self._select_saved(self.current_slug)

    def _on_saved_select(self, _event=None):
        if self._loading_list:
            return
        selection = self.saved_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self.loaded_definitions):
            return
        definition = self.loaded_definitions[index]
        if definition.get("slug") == self.current_slug:
            return
        self.load_definition(definition)

    def _collect_definition(self, as_new: bool = False) -> dict[str, Any]:
        name = self.name_var.get().strip()
        if not name:
            raise ValueError("策略名称不能为空")
        if as_new:
            base = f"{name} 副本"
            name = base
            existing = {str(item.get("name", "")).strip() for item in self.loaded_definitions}
            suffix = 2
            while name in existing:
                name = f"{base} {suffix}"
                suffix += 1
        entry_conditions = [row.get_condition() for row in self.entry_rows]
        exit_conditions = [row.get_condition() for row in self.exit_rows]
        if not entry_conditions and not exit_conditions:
            raise ValueError("至少添加一个入场或离场条件")
        return {
            "slug": "" if as_new else (self.current_slug or ""),
            "name": name,
            "description": self.desc_var.get().strip(),
            "entry_logic": self._current_logic("entry"),
            "exit_logic": self._current_logic("exit"),
            "entry_conditions": entry_conditions,
            "exit_conditions": exit_conditions,
            "created_at": (self.current_definition or {}).get("created_at"),
            "updated_at": None,
        }

    def validate_form(self):
        try:
            definition = self._collect_definition()
            from strategies.custom import normalize_definition

            normalized = normalize_definition(definition)
            self.status_label.configure(
                text=f"校验通过：{len(normalized['entry_conditions'])} 个入场条件，"
                f"{len(normalized['exit_conditions'])} 个离场条件",
                fg=GREEN,
            )
        except Exception as exc:
            self.status_label.configure(text=f"校验失败：{exc}", fg=RED)
            messagebox.showerror("校验失败", str(exc), parent=self)

    def save(self):
        self._save(as_new=False)

    def save_as(self):
        self._save(as_new=True)

    def _save(self, as_new: bool):
        try:
            raw = self._collect_definition(as_new=as_new)
            saved = save_definition(raw, as_new=as_new)
            self.current_slug = saved["slug"]
            self.current_definition = saved
            self.name_var.set(saved["name"])
            self.refresh_saved_list()
            self._select_saved(self.current_slug)
            self.status_label.configure(text=f"已保存：{saved['name']}", fg=GREEN)
            if self.app is not None and self.app.winfo_exists():
                self.app.refresh_strategy_list(select_slug=saved["slug"])
                self.app._log(f"自定义策略已保存: {saved['name']} ({saved['slug']})", "ok")
        except Exception as exc:
            self.status_label.configure(text=f"保存失败：{exc}", fg=RED)
            messagebox.showerror("保存失败", str(exc), parent=self)

    def delete_current(self):
        if not self.current_slug:
            messagebox.showinfo("删除", "当前是新策略，尚未保存。", parent=self)
            return
        name = self.name_var.get()
        if not messagebox.askyesno("删除策略", f"确定删除「{name}」吗？", parent=self):
            return
        try:
            was_current = self.current_slug
            delete_definition(was_current)
            self.current_slug = None
            self.current_definition = None
            self.refresh_saved_list()
            self.new_strategy()
            self.status_label.configure(text=f"已删除：{name}", fg=AMBER)
            if self.app is not None and self.app.winfo_exists():
                self.app.refresh_strategy_list(select_slug=None)
                self.app._log(f"自定义策略已删除: {name}", "warn")
        except Exception as exc:
            messagebox.showerror("删除失败", str(exc), parent=self)

    def open_catalog(self):
        IndicatorCatalogWindow(self, self.font_family)

    def close(self):
        self.app.unregister_scroll(self.form_scroll)
        if getattr(self.app, "custom_editor", None) is self:
            self.app.custom_editor = None
        self.destroy()



class DataImportDialog(tk.Toplevel):
    ADJUST_OPTIONS = {
        "自动识别": "auto",
        "前复权 qfq": "qfq",
        "后复权 hfq": "hfq",
        "不复权 raw": "",
    }

    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.on_done = on_done
        self.font_family = app.font_family
        self.pending: list[Path] = []
        self.msg_queue: queue.Queue = queue.Queue()
        self.running = False
        self._closing = False
        self._poll_after = None
        self._auto_after = None
        self.title("导入本地行情")
        self.configure(bg=BG)
        width, height = 980, 760
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{max((screen_w - width) // 2, 20)}+{max((screen_h - height) // 2, 20)}")
        self.minsize(820, 640)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.scan_import_dir(quiet=True)
        self.refresh_local_stats()
        self._poll_after = self.after(120, self._poll)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 6))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="导入本地行情", bg=BG, fg=TEXT, font=(self.font_family, 18, "bold")).pack(anchor="w")
        tk.Label(
            titles,
            text="支持 CSV / SQLite 文件与文件夹；导入后写入本地数据库，回测优先读取本地。",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
        ).pack(anchor="w", pady=(2, 0))
        tk.Button(
            header,
            text="关闭",
            command=self.close,
            bg=CARD_ALT,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=14,
            pady=8,
            cursor="hand2",
            font=(self.font_family, 9),
        ).pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18)
        content = tk.Frame(self, bg=BG)
        content.pack(fill="both", expand=True, padx=18, pady=12)
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(2, weight=1)
        options = tk.Frame(content, bg=BG)
        options.grid(row=0, column=0, sticky="ew")
        tk.Label(options, text="导入复权口径", bg=BG, fg=MUTED, font=(self.font_family, 9)).pack(side="left")
        self.adjust_var = tk.StringVar(value="自动识别")
        ttk.Combobox(
            options,
            textvariable=self.adjust_var,
            values=list(self.ADJUST_OPTIONS),
            state="readonly",
            width=16,
        ).pack(side="left", padx=(8, 16))
        tk.Label(
            options,
            text="自动识别会读取文件名中的 qfq/hfq；数据库未标注时默认后复权。",
            bg=BG,
            fg=DIM,
            font=(self.font_family, 8),
        ).pack(side="left")
        self.local_label = tk.Label(
            options,
            text="本地库: --",
            bg=BG,
            fg=GREEN,
            font=(self.font_family, 9),
            anchor="e",
        )
        self.local_label.pack(side="right")
        self.drop_canvas = tk.Canvas(
            content,
            height=132,
            bg=CARD,
            highlightbackground=BORDER,
            highlightthickness=1,
            bd=0,
            cursor="hand2",
        )
        self.drop_canvas.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        self.drop_canvas.bind("<Configure>", lambda _e: self._draw_dropzone())
        self.drop_canvas.bind("<Button-1>", lambda _e: self.choose_files())
        if DND_AVAILABLE:
            try:
                self.drop_canvas.drop_target_register(DND_FILES)
                self.drop_canvas.dnd_bind("<<Drop>>", self._on_drop)
            except Exception:
                pass
        list_frame = tk.Frame(content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.grid_rowconfigure(1, weight=1)
        list_frame.grid_columnconfigure(0, weight=1)
        toolbar = tk.Frame(list_frame, bg=PANEL)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=6)
        tk.Label(toolbar, text="待导入文件", bg=PANEL, fg=TEXT, font=(self.font_family, 10, "bold")).pack(side="left")
        self.pending_label = tk.Label(toolbar, text="0 个文件", bg=PANEL, fg=DIM, font=(self.font_family, 8))
        self.pending_label.pack(side="left", padx=10)
        self.auto_import_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="拖入/选择后自动导入", variable=self.auto_import_var).pack(side="right", padx=(10, 0))
        tk.Button(toolbar, text="清空", command=self.clear_pending, bg=CARD_ALT, fg=MUTED, activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0, padx=10, pady=4, cursor="hand2", font=(self.font_family, 8)).pack(side="right", padx=(6, 0))
        tk.Button(toolbar, text="扫描导入文件夹", command=self.scan_import_dir, bg=CARD_ALT, fg=MUTED, activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0, padx=10, pady=4, cursor="hand2", font=(self.font_family, 8)).pack(side="right", padx=(6, 0))
        tk.Button(toolbar, text="打开导入文件夹", command=self.open_import_dir, bg=CARD_ALT, fg=MUTED, activebackground=BORDER, activeforeground=TEXT, relief="flat", bd=0, padx=10, pady=4, cursor="hand2", font=(self.font_family, 8)).pack(side="right")
        self.file_list = tk.Listbox(
            list_frame,
            bg=CARD,
            fg=TEXT,
            selectbackground=ACCENT,
            selectforeground="#000000",
            highlightthickness=0,
            bd=0,
            activestyle="none",
            font=(self.font_family, 9),
            exportselection=False,
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        self.file_list.configure(yscrollcommand=scroll.set)
        self.file_list.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=(0, 10))
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 4), pady=(0, 10))
        bottom = tk.Frame(self, bg=BG)
        bottom.pack(fill="x", padx=18, pady=(0, 12))
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 8))
        actions = tk.Frame(bottom, bg=BG)
        actions.pack(fill="x")
        self.start_button = self.app._primary_button(actions, "开始导入", self.start_import)
        self.start_button.pack(side="left")
        self.app._ghost_button(actions, "选择文件", self.choose_files).pack(side="left", padx=(8, 0))
        self.app._ghost_button(actions, "选择文件夹", self.choose_folder).pack(side="left", padx=(8, 0))
        self.status_label = tk.Label(actions, text="就绪", bg=BG, fg=MUTED, font=(self.font_family, 8), anchor="w")
        self.status_label.pack(side="left", padx=16)
        self.log_text = tk.Text(
            bottom,
            height=5,
            bg="#000000",
            fg="#ffffff",
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            wrap="word",
            padx=8,
            pady=6,
            font=(self.font_family, 8),
            state="disabled",
        )
        self.log_text.pack(fill="x", pady=(8, 0))
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("error", foreground=RED)
        self.log_text.tag_configure("info", foreground="#ffffff")
        self._draw_dropzone()

    def _draw_dropzone(self):
        self.drop_canvas.delete("all")
        width = max(self.drop_canvas.winfo_width(), 400)
        height = max(self.drop_canvas.winfo_height(), 130)
        self.drop_canvas.create_rectangle(12, 12, width - 12, height - 12, outline=ACCENT, dash=(8, 5), width=2)
        self.drop_canvas.create_text(
            width / 2,
            height / 2 - 12,
            text="将 CSV / SQLite 文件或文件夹拖到这里",
            fill=TEXT,
            font=(self.font_family, 13, "bold"),
        )
        extra = "支持拖放；也可以点击这里选择文件" if DND_AVAILABLE else "拖放组件不可用，请点击这里选择文件"
        self.drop_canvas.create_text(
            width / 2,
            height / 2 + 16,
            text=extra + f"  ·  导入文件夹: {IMPORT_DIR}",
            fill=MUTED,
            font=(self.font_family, 8),
        )

    def _log(self, message: str, tag: str = "info"):
        if not self.log_text.winfo_exists():
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_drop(self, event):
        try:
            raw = self.tk.splitlist(event.data)
        except Exception:
            raw = [event.data]
        self._add_paths(raw)
        return getattr(event, "action", "copy")

    def _add_paths(self, raw_paths, auto_start: bool = True):
        paths = collect_paths([Path(item) for item in raw_paths])
        known = {path.resolve() for path in self.pending}
        added = 0
        for path in paths:
            if path.resolve() in known:
                continue
            self.pending.append(path)
            known.add(path.resolve())
            added += 1
        self._refresh_pending()
        if added:
            self._log(f"已加入 {added} 个文件", "ok")
            if auto_start and self.auto_import_var.get() and not self.running:
                self._auto_after = self.after(200, self._auto_import)
        elif paths:
            self._log("文件已在队列中", "warn")
        else:
            self._log("没有发现可导入的 CSV / SQLite 文件", "warn")

    def _refresh_pending(self):
        self.file_list.delete(0, "end")
        for path in self.pending:
            try:
                size = path.stat().st_size / 1024
                self.file_list.insert("end", f"{path.name}  ({size:,.0f} KB)  {path.parent}")
            except OSError:
                self.file_list.insert("end", str(path))
        self.pending_label.configure(text=f"{len(self.pending)} 个文件")

    def clear_pending(self):
        self.pending.clear()
        self._refresh_pending()

    def choose_files(self):
        paths = filedialog.askopenfilenames(
            parent=self,
            title="选择行情文件",
            initialdir=str(IMPORT_DIR),
            filetypes=[
                ("行情文件", "*.csv *.txt *.db *.sqlite *.sqlite3"),
                ("CSV", "*.csv"),
                ("SQLite", "*.db *.sqlite *.sqlite3"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self._add_paths(paths)

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self, title="选择行情文件夹", initialdir=str(IMPORT_DIR))
        if path:
            self._add_paths([path])

    def scan_import_dir(self, quiet=False):
        if not IMPORT_DIR.exists():
            return
        paths = collect_paths([IMPORT_DIR])
        if paths:
            self._add_paths(paths, auto_start=not quiet)
        elif not quiet:
            self._log(f"{IMPORT_DIR} 中没有可导入文件", "warn")

    def open_import_dir(self):
        try:
            IMPORT_DIR.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["xdg-open", str(IMPORT_DIR)])
        except Exception:
            webbrowser.open(IMPORT_DIR.as_uri())

    def refresh_local_stats(self):
        try:
            summary = CacheStore(DATA_DIR / "cache.db").summary()
        except Exception:
            summary = {}
        if not summary:
            text = "本地库: 暂无数据"
        else:
            parts = []
            for adjust in ("qfq", "hfq", ""):
                info = summary.get(adjust)
                if info:
                    label = adjust or "raw"
                    parts.append(f"{label} {info['symbols']}只/{info['bars']:,}根")
            text = "本地库: " + " · ".join(parts) if parts else "本地库: 暂无数据"
        self.local_label.configure(text=text)
        return summary

    def _auto_import(self):
        if self._closing or not self.winfo_exists():
            return
        self.start_import()

    def start_import(self):
        if self.running:
            return
        files = collect_paths(self.pending)
        if not files:
            messagebox.showwarning("没有文件", "请先拖入或选择行情文件。", parent=self)
            return
        adjust = self.ADJUST_OPTIONS.get(self.adjust_var.get(), "auto")
        self.running = True
        self.start_button.configure(state=tk.DISABLED, text="导入中...")
        self.progress.start(10)
        self.status_label.configure(text="正在导入 ...", fg=MUTED)
        self._log(f"开始导入 {len(files)} 个文件，复权口径={adjust or '自动'}", "info")

        def work():
            try:
                from core.importer import collect_paths, import_paths

                report = import_paths(files, DATA_DIR / "cache.db", adjust, progress=lambda message: self.msg_queue.put(("log", message)))
                self.msg_queue.put(("done", report))
            except Exception as exc:
                self.msg_queue.put(("error", traceback.format_exc()))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if self._closing or not self.winfo_exists():
            return
        while True:
            try:
                kind, payload = self.msg_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload))
                self.status_label.configure(text=str(payload))
            elif kind == "done":
                self._finish_import(payload)
            elif kind == "error":
                self.running = False
                self.progress.stop()
                self.start_button.configure(state=tk.NORMAL, text="开始导入")
                self.status_label.configure(text="导入失败", fg=RED)
                self._log(str(payload), "error")
                messagebox.showerror("导入失败", "导入过程中发生错误，请查看日志。", parent=self)
        self._poll_after = self.after(150, self._poll)

    def _finish_import(self, report):
        self.running = False
        self.progress.stop()
        self.start_button.configure(state=tk.NORMAL, text="开始导入")
        summary = self.refresh_local_stats()
        self.status_label.configure(
            text=f"完成: {report.rows:,} 行 · {report.symbols:,} 只 · {report.duration:.1f}s",
            fg=GREEN if report.rows else AMBER,
        )
        self._log(f"导入完成: {report.rows:,} 行，本地 {report.symbols:,} 只股票，用时 {report.duration:.1f}s", "ok")
        for error in report.errors:
            self._log(f"提示: {error}", "warn")
        self.pending.clear()
        self._refresh_pending()
        self.app.refresh_local_stats()
        if self.on_done:
            try:
                self.on_done(report)
            except Exception:
                pass
        self.app._log(f"本地行情导入完成: {report.rows:,} 行，本地库 {summary.get('qfq', {}).get('symbols', 0) + summary.get('hfq', {}).get('symbols', 0)} 条复权记录", "ok")

    def close(self):
        if self.running:
            if not messagebox.askyesno("正在导入", "导入正在进行，确定关闭窗口吗？\n后台导入可能继续执行。", parent=self):
                return
        self._closing = True
        try:
            if self._poll_after:
                self.after_cancel(self._poll_after)
            if self._auto_after:
                self.after_cancel(self._auto_after)
        except Exception:
            pass
        try:
            self.app.import_dialog = None
        except Exception:
            pass
        self.destroy()


class MarketBacktestWindow(tk.Toplevel):
    def __init__(self, app, base_request):
        super().__init__(app)
        self.app = app
        self.base_request = base_request
        self.font_family = app.font_family
        self.meta = get_strategy_meta(base_request.strategy)
        if self.meta is None:
            raise ValueError("当前策略无效")
        self.params = dict(base_request.strategy_params)
        self.base_config = base_request.config
        self.rows_df = pd.DataFrame()
        self.result = None
        self.cancel_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self.running = False
        self.after_id = None
        self.sort_column = "total_return_pct"
        self.sort_desc = True
        self.title("全市场逐股回测")
        self.configure(bg=BG)
        width, height = 1380, 880
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{max((screen_w - width) // 2, 10)}+{max((screen_h - height) // 2 - 10, 10)}")
        self.minsize(1120, 740)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.refresh_data_summary()
        self.after_id = self.after(120, self._poll)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(12, 6))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="全市场逐股回测", bg=BG, fg=TEXT, font=(self.font_family, 18, "bold")).pack(anchor="w")
        tk.Label(
            titles,
            text=f"策略来自主界面（打开时快照）：{self.meta.name} · 本地数据库 · NumPy 指标 + Rust 回测",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
        ).pack(anchor="w", pady=(2, 0))
        close = self.app._ghost_button(header, "关闭", self.close)
        close.pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18)
        controls = tk.Frame(self, bg=BG)
        controls.pack(fill="x", padx=18, pady=10)
        self.var_start = tk.StringVar(value=self.base_request.start)
        self.var_end = tk.StringVar(value=self.base_request.end)
        self.var_benchmark = tk.StringVar(value=self.base_request.benchmark_symbol)
        adjustable = self.base_request.trade_adjust or "qfq"
        self.var_adjust = tk.StringVar(value=adjustable)
        self.var_limit = tk.StringVar(value="0")
        self.var_min_bars = tk.StringVar(value="120")
        native_ok, _native_path = rust_status()
        self.var_native = tk.BooleanVar(value=native_ok)
        fields = [
            ("开始", self.var_start, 11),
            ("结束", self.var_end, 11),
            ("基准", self.var_benchmark, 9),
        ]
        for label, var, width in fields:
            box = tk.Frame(controls, bg=BG)
            box.pack(side="left", padx=(0, 10))
            tk.Label(box, text=label, bg=BG, fg=DIM, font=(self.font_family, 8)).pack(anchor="w")
            ttk.Entry(box, textvariable=var, width=width).pack()
        box = tk.Frame(controls, bg=BG)
        box.pack(side="left", padx=(0, 10))
        tk.Label(box, text="复权", bg=BG, fg=DIM, font=(self.font_family, 8)).pack(anchor="w")
        ttk.Combobox(box, textvariable=self.var_adjust, values=("qfq", "hfq", "raw"), state="readonly", width=7).pack()
        for label, var, width in (("最多股票数 (0=全部)", self.var_limit, 9), ("最少K线", self.var_min_bars, 7)):
            box = tk.Frame(controls, bg=BG)
            box.pack(side="left", padx=(0, 10))
            tk.Label(box, text=label, bg=BG, fg=DIM, font=(self.font_family, 8)).pack(anchor="w")
            ttk.Entry(box, textvariable=var, width=width).pack()
        native_check = ttk.Checkbutton(controls, text="Rust 加速", variable=self.var_native)
        native_check.pack(side="left", padx=(4, 10), pady=(16, 0))
        if not native_ok:
            native_check.configure(state="disabled")
        self.start_button = self.app._primary_button(controls, "开始全市场回测", self.start)
        self.start_button.pack(side="left", pady=(14, 0))
        self.stop_button = self.app._secondary_button(controls, "停止", self.stop, state=tk.DISABLED)
        self.stop_button.pack(side="left", padx=(8, 0), pady=(14, 0))
        self.app._ghost_button(
            controls,
            "导入数据",
            lambda: self.app._open_import_dialog(on_done=lambda _report: self.refresh_data_summary()),
        ).pack(side="left", padx=(8, 0), pady=(14, 0))
        self.local_info = tk.Label(
            self,
            text="本地库: --",
            bg=BG,
            fg=GREEN,
            font=(self.font_family, 8),
            anchor="w",
            padx=18,
        )
        self.local_info.pack(fill="x")
        cards = tk.Frame(self, bg=BG)
        cards.pack(fill="x", padx=18, pady=(8, 10))
        for col in range(6):
            cards.grid_columnconfigure(col, weight=1, uniform="market")
        self.cards = {
            "success": MetricCard(cards, "覆盖股票", self.font_family),
            "avg": MetricCard(cards, "平均收益", self.font_family),
            "median": MetricCard(cards, "中位收益", self.font_family),
            "positive": MetricCard(cards, "正收益占比", self.font_family),
            "drawdown": MetricCard(cards, "平均最大回撤", self.font_family),
            "beat": MetricCard(cards, "跑赢基准", self.font_family),
        }
        for idx, card in enumerate(self.cards.values()):
            card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 6, 0))
        table_frame = tk.Frame(self, bg=CARD)
        table_frame.pack(fill="both", expand=True, padx=18, pady=(0, 8))
        columns = (
            "symbol", "bars", "total_return_pct", "annual_return_pct", "max_drawdown_pct",
            "sharpe", "trade_count", "win_rate_pct", "benchmark_return_pct",
            "excess_return_pct", "final_equity",
        )
        headings = {
            "symbol": "代码", "bars": "K线", "total_return_pct": "收益%",
            "annual_return_pct": "年化%", "max_drawdown_pct": "回撤%",
            "sharpe": "Sharpe", "trade_count": "交易", "win_rate_pct": "胜率%",
            "benchmark_return_pct": "基准%", "excess_return_pct": "超额%",
            "final_equity": "期末权益",
        }
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", style="Treeview")
        for col in columns:
            self.table.heading(col, text=headings[col], command=lambda key=col: self.sort_table(key), anchor="w")
            self.table.column(col, width=92 if col not in ("symbol", "final_equity") else 110, anchor="w", stretch=False)
        self.table.tag_configure("win", foreground=self.app.settings.rise_color)
        self.table.tag_configure("loss", foreground=self.app.settings.fall_color)
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        footer = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(footer, mode="determinate", style="Horizontal.TProgressbar", length=240)
        self.progress.pack(side="left", padx=12, pady=8)
        self.status_label = tk.Label(footer, text="就绪：本地数据 + Rust 加速", bg=PANEL, fg=MUTED, font=(self.font_family, 8), anchor="w")
        self.status_label.pack(side="left", padx=6)
        self.export_button = self.app._secondary_button(footer, "导出结果", self.export, state=tk.DISABLED)
        self.export_button.pack(side="right", padx=12, pady=6)

    def refresh_data_summary(self):
        try:
            summary = CacheStore(DATA_DIR / "cache.db").summary()
        except Exception:
            summary = {}
        if not summary:
            text = "本地库暂无数据，请先点击「导入数据」拖入 CSV / SQLite 行情。"
        else:
            parts = []
            for adjust in ("qfq", "hfq", ""):
                info = summary.get(adjust)
                if info:
                    label = adjust or "raw"
                    parts.append(
                        f"{label}: {info['symbols']:,} 只 / {info['bars']:,} 根 / "
                        f"{info['first_date']} ~ {info['last_date']}"
                    )
            text = "本地库  " + "    |    ".join(parts)
        self.local_info.configure(text=text)

    def _request(self):
        try:
            start = pd.Timestamp(self.var_start.get().strip()).normalize()
            end = pd.Timestamp(self.var_end.get().strip()).normalize()
            limit = max(int(float(self.var_limit.get())), 0)
            min_bars = max(int(float(self.var_min_bars.get())), 2)
        except Exception as exc:
            raise ValueError(f"参数格式错误: {exc}") from exc
        if start >= end:
            raise ValueError("开始日期必须早于结束日期")
        adjust = self.var_adjust.get().strip()
        if adjust == "raw":
            adjust = ""
        return MarketRequest(
            start=str(start.date()),
            end=str(end.date()),
            strategy=self.base_request.strategy,
            strategy_params=self.params,
            config=self.base_config,
            adjust=adjust,
            benchmark_symbol=self.var_benchmark.get().strip(),
            limit=limit,
            min_bars=min_bars,
            use_native=bool(self.var_native.get()),
        )

    def start(self):
        if self.running:
            return
        try:
            request = self._request()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)
            return
        self.running = True
        self.cancel_event.clear()
        self.start_button.configure(state=tk.DISABLED, text="回测中...")
        self.stop_button.configure(state=tk.NORMAL)
        self.export_button.configure(state=tk.DISABLED)
        self.progress.configure(value=0, maximum=1)
        self.status_label.configure(text="正在读取本地数据库 ...", fg=MUTED)
        self.table.delete(*self.table.get_children())
        self.rows_df = pd.DataFrame()
        self.result = None

        def work():
            try:
                result = run_market(
                    request,
                    progress=lambda current, total, message: self.msg_queue.put(("progress", (current, total, message))),
                    should_cancel=self.cancel_event.is_set,
                )
                self.msg_queue.put(("done", result))
            except Exception as exc:
                self.msg_queue.put(("error", (str(exc), traceback.format_exc())))

        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if not self.running:
            return
        self.cancel_event.set()
        self.status_label.configure(text="正在取消 ...", fg=AMBER)

    def _poll(self):
        if not self.winfo_exists():
            return
        while True:
            try:
                kind, payload = self.msg_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                current, total, message = payload
                if total > 0:
                    self.progress.configure(maximum=total, value=current)
                self.status_label.configure(text=message)
            elif kind == "done":
                self._finish(payload)
            elif kind == "error":
                self._error(*payload)
        self.after_id = self.after(120, self._poll)

    def _finish(self, result):
        self.running = False
        self.start_button.configure(state=tk.NORMAL, text="开始全市场回测")
        self.stop_button.configure(state=tk.DISABLED)
        self.result = result
        self.rows_df = result.rows
        self.export_button.configure(state=tk.NORMAL if not self.rows_df.empty else tk.DISABLED)
        summary = result.summary
        self.cards["success"].set(
            f"{summary.get('success', 0)} / {summary.get('universe', 0)}",
            f"跳过 {summary.get('failed', 0)}",
            TEXT,
        )
        avg = summary.get("avg_return_pct")
        median = summary.get("median_return_pct")
        pos = summary.get("positive_pct")
        dd = summary.get("avg_drawdown_pct")
        beat = summary.get("beat_benchmark_pct")
        rise = self.app.settings.rise_color
        fall = self.app.settings.fall_color
        self.cards["avg"].set(fmt_pct(avg), "每只独立本金", rise if (avg or 0) >= 0 else fall)
        self.cards["median"].set(fmt_pct(median), "中位数", rise if (median or 0) >= 0 else fall)
        self.cards["positive"].set(fmt_pct(pos, signed=False), "正收益股票占比", rise)
        self.cards["drawdown"].set(fmt_pct(dd), "逐股平均", fall if dd is not None and dd < 0 else TEXT)
        self.cards["beat"].set(fmt_pct(beat, signed=False), f"基准 {fmt_pct(summary.get('benchmark_return_pct'))}", TEXT)
        engine = "Rust" if summary.get("native") else "Python"
        total_trades = summary.get("total_trades", 0)
        self.status_label.configure(
            text=f"{'已取消' if result.cancelled else '完成'} · {engine} · "
                 f"用时 {result.duration_sec:.1f}s · 总交易 {total_trades} 笔",
            fg=GREEN,
        )
        if result.warnings:
            self.app._log("全市场回测提示: " + " | ".join(result.warnings), "warn")
        for failure in result.failures[:8]:
            self.app._log(f"全市场跳过: {failure}", "warn")
        if self.rows_df.empty:
            self.status_label.configure(text="没有可回测的本地股票，请先导入数据。", fg=AMBER)
        self.fill_table()
        self.app._log(
            f"全市场回测完成: 成功 {summary.get('success', 0)}/{summary.get('universe', 0)}，"
            f"平均收益 {fmt_pct(avg)}，引擎 {engine}",
            "ok",
        )

    def _error(self, message, details):
        self.running = False
        self.start_button.configure(state=tk.NORMAL, text="开始全市场回测")
        self.stop_button.configure(state=tk.DISABLED)
        self.status_label.configure(text=f"失败: {message}", fg=RED)
        self.app._log(f"全市场回测失败: {message}", "error")
        self.app._log(details, "dim")
        messagebox.showerror("全市场回测失败", message, parent=self)

    def fill_table(self):
        self.table.delete(*self.table.get_children())
        if self.rows_df.empty:
            return
        frame = self.rows_df.copy()
        if self.sort_column in frame.columns:
            frame = frame.sort_values(self.sort_column, ascending=not self.sort_desc, na_position="last")
        for row in frame.itertuples(index=False):
            tag = "win" if float(getattr(row, "total_return_pct", 0)) >= 0 else "loss"
            self.table.insert(
                "",
                "end",
                values=(
                    getattr(row, "symbol", ""),
                    int(getattr(row, "bars", 0)),
                    f"{float(getattr(row, 'total_return_pct', 0)):+.2f}",
                    f"{float(getattr(row, 'annual_return_pct', 0)):+.2f}",
                    f"{float(getattr(row, 'max_drawdown_pct', 0)):.2f}",
                    f"{float(getattr(row, 'sharpe', 0)):.2f}",
                    int(getattr(row, "trade_count", 0)),
                    f"{float(getattr(row, 'win_rate_pct', 0)):.1f}",
                    f"{float(getattr(row, 'benchmark_return_pct', 0)):+.2f}" if pd.notna(getattr(row, "benchmark_return_pct", np.nan)) else "--",
                    f"{float(getattr(row, 'excess_return_pct', 0)):+.2f}" if pd.notna(getattr(row, "excess_return_pct", np.nan)) else "--",
                    f"{float(getattr(row, 'final_equity', 0)):,.0f}",
                ),
                tags=(tag,),
            )

    def sort_table(self, column: str):
        if self.rows_df.empty:
            return
        if self.sort_column == column:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_column = column
            self.sort_desc = True
        self.fill_table()

    def export(self):
        if self.result is None:
            return
        directory = filedialog.askdirectory(parent=self, title="选择导出目录", initialdir=str(PROJECT_ROOT / "exports"))
        if not directory:
            return
        try:
            path = export_market_result(self.result, directory)
            self.app._log(f"全市场回测已导出: {path}", "ok")
            messagebox.showinfo("导出完成", f"结果已导出到：\n{directory}", parent=self)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def apply_theme(self):
        settings = self.app.settings
        self.table.tag_configure("win", foreground=settings.rise_color)
        self.table.tag_configure("loss", foreground=settings.fall_color)
        self.fill_table()
        if self.result is None:
            return
        summary = self.result.summary
        avg = summary.get("avg_return_pct")
        median = summary.get("median_return_pct")
        dd = summary.get("avg_drawdown_pct")
        self.cards["avg"].value.configure(
            fg=settings.rise_color if (avg or 0) >= 0 else settings.fall_color
        )
        self.cards["median"].value.configure(
            fg=settings.rise_color if (median or 0) >= 0 else settings.fall_color
        )
        self.cards["drawdown"].value.configure(
            fg=settings.fall_color if dd is not None and dd < 0 else TEXT
        )

    def close(self):
        if self.running:
            if not messagebox.askyesno("正在回测", "全市场回测正在进行，确定关闭吗？", parent=self):
                return
            self.cancel_event.set()
        try:
            if self.after_id:
                self.after_cancel(self.after_id)
        except Exception:
            pass
        try:
            self.app.market_window = None
        except Exception:
            pass
        self.destroy()



class SettingsWindow(tk.Toplevel):
    COLOR_SPECS = (
        ("rise_color", "上涨颜色"),
        ("fall_color", "下跌颜色"),
        ("buy_color", "买入标记"),
        ("sell_color", "卖出标记"),
        ("strategy_color", "策略净值线"),
        ("benchmark_color", "基准线"),
        ("drawdown_color", "回撤线"),
        ("ma_0", "MA1 线"),
        ("ma_1", "MA2 线"),
        ("ma_2", "MA3 线"),
    )

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.font_family = app.font_family
        self.settings = app.settings
        self.color_vars: dict[str, tk.StringVar] = {}
        self.color_buttons: dict[str, tk.Button] = {}
        self.ma_type_var = tk.StringVar()
        self.ma_period_vars: list[tk.StringVar] = []
        self.default_vars: dict[str, tk.StringVar] = {}
        self.title("设置")
        self.configure(bg=BG)
        width, height = 1100, 820
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{max((screen_w - width) // 2, 20)}+{max((screen_h - height) // 2 - 10, 20)}")
        self.minsize(920, 680)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._load(self.settings)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 6))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="设置", bg=BG, fg=TEXT, font=(self.font_family, 18, "bold")).pack(anchor="w")
        tk.Label(
            titles,
            text=f"版本 {APP_VERSION} · 红涨绿跌 · 颜色、K 线均线和指标默认参数",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
        ).pack(anchor="w", pady=(2, 0))
        self.app._ghost_button(header, "关闭", self.close).pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18)
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=18, pady=12)
        color_tab = tk.Frame(notebook, bg=BG)
        chart_tab = tk.Frame(notebook, bg=BG)
        default_tab = tk.Frame(notebook, bg=BG)
        notebook.add(color_tab, text="颜色与标记")
        notebook.add(chart_tab, text="K 线指标")
        notebook.add(default_tab, text="指标默认参数")
        self._build_colors(color_tab)
        self._build_chart(chart_tab)
        self._build_defaults(default_tab)
        footer = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        footer.pack(fill="x", side="bottom")
        self.app._ghost_button(footer, "恢复默认", self.reset_defaults).pack(side="left", padx=(12, 0), pady=8)
        self.app._primary_button(footer, "保存并应用", self.save).pack(side="right", padx=12, pady=8)
        self.app._ghost_button(footer, "取消", self.close).pack(side="right", padx=(0, 8), pady=8)
        self.status = tk.Label(footer, text="设置只影响本机显示和以后新建的指标条件。", bg=PANEL, fg=DIM, font=(self.font_family, 8))
        self.status.pack(side="left", padx=14)

    def _build_colors(self, parent):
        tk.Label(
            parent,
            text="红涨绿跌：上涨/买入默认红色，下跌/卖出默认绿色。点击色块打开系统取色器。",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 10))
        grid = tk.Frame(parent, bg=BG)
        grid.pack(fill="x", padx=12)
        for idx, (key, label) in enumerate(self.COLOR_SPECS):
            row = idx // 2
            col = idx % 2
            item = tk.Frame(grid, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            item.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=5)
            grid.grid_columnconfigure(col, weight=1)
            tk.Label(item, text=label, bg=PANEL, fg=TEXT, font=(self.font_family, 9), width=14, anchor="w").pack(side="left", padx=10, pady=8)
            var = tk.StringVar()
            button = tk.Button(
                item,
                text=var.get() or "#000000",
                command=lambda k=key: self._choose_color(k),
                relief="flat",
                bd=0,
                padx=10,
                pady=5,
                cursor="hand2",
                font=(self.font_family, 8, "bold"),
            )
            button.pack(side="right", padx=8)
            self.color_vars[key] = var
            self.color_buttons[key] = button
        tk.Label(
            parent,
            text="策略线/基准线/均线颜色只影响图表；上涨、下跌和买卖标记颜色会同步到指标卡、交易明细和全市场结果。",
            bg=BG,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=12, pady=(12, 0))

    def _build_chart(self, parent):
        box = tk.Frame(parent, bg=BG)
        box.pack(fill="x", padx=12, pady=14)
        tk.Label(box, text="均线类型", bg=BG, fg=MUTED, font=(self.font_family, 9), width=16, anchor="w").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Combobox(box, textvariable=self.ma_type_var, values=("MA", "EMA"), state="readonly", width=12).grid(row=0, column=1, sticky="w", pady=6)
        for index in range(3):
            var = tk.StringVar()
            self.ma_period_vars.append(var)
            tk.Label(box, text=f"MA{index + 1} 周期", bg=BG, fg=MUTED, font=(self.font_family, 9), width=16, anchor="w").grid(row=index + 1, column=0, sticky="w", pady=6)
            ttk.Entry(box, textvariable=var, width=12).grid(row=index + 1, column=1, sticky="w", pady=6)
        tk.Label(
            parent,
            text="均线颜色请在上一个标签页的 MA1/MA2/MA3 中设置。K 线始终显示成交量；上涨用红色、下跌用绿色。",
            bg=BG,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
        ).pack(fill="x", padx=12)

    def _build_defaults(self, parent):
        tk.Label(
            parent,
            text="这些是自定义策略编辑器新建条件时的默认参数；已有条件不会改变。",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 8))
        grid = tk.Frame(parent, bg=BG)
        grid.pack(fill="x", padx=12)
        fields = (
            ("ma_period", "SMA 默认周期", 20),
            ("ema_period", "EMA 默认周期", 20),
            ("rsi_period", "RSI 默认周期", 14),
            ("macd_fast", "MACD 快线", 12),
            ("macd_slow", "MACD 慢线", 26),
            ("macd_signal", "MACD 信号线", 9),
            ("kdj_n", "KDJ N", 9),
            ("kdj_m1", "KDJ M1", 3),
            ("kdj_m2", "KDJ M2", 3),
            ("boll_period", "BOLL 周期", 20),
            ("boll_width", "BOLL 倍宽", 2.0),
            ("atr_period", "ATR 周期", 14),
            ("volume_ratio_period", "量比周期", 5),
            ("bias_period", "BIAS 周期", 6),
        )
        for index, (key, label, default) in enumerate(fields):
            row = index // 2
            col = index % 2
            item = tk.Frame(grid, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            item.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=4)
            grid.grid_columnconfigure(col, weight=1)
            tk.Label(item, text=label, bg=PANEL, fg=TEXT, font=(self.font_family, 9), width=15, anchor="w").pack(side="left", padx=10, pady=7)
            var = tk.StringVar(value=str(default))
            ttk.Entry(item, textvariable=var, width=10).pack(side="right", padx=8)
            self.default_vars[key] = var

    def _load(self, settings: UserSettings):
        self.color_vars["rise_color"].set(settings.rise_color)
        self.color_vars["fall_color"].set(settings.fall_color)
        self.color_vars["buy_color"].set(settings.buy_color)
        self.color_vars["sell_color"].set(settings.sell_color)
        self.color_vars["strategy_color"].set(settings.strategy_color)
        self.color_vars["benchmark_color"].set(settings.benchmark_color)
        self.color_vars["drawdown_color"].set(settings.drawdown_color)
        for index in range(3):
            self.color_vars[f"ma_{index}"].set(settings.ma_colors[index])
        for key in self.color_vars:
            self._update_color_button(key)
        self.ma_type_var.set(settings.ma_type)
        for index in range(3):
            self.ma_period_vars[index].set(str(settings.ma_periods[index]))
        defaults = settings.indicator_defaults
        def d(key, param, fallback):
            return defaults.get(key, {}).get(param, fallback)
        values = {
            "ma_period": d("ma", "period", 20),
            "ema_period": d("ema", "period", 20),
            "rsi_period": d("rsi", "period", 14),
            "macd_fast": d("macd_dif", "fast", 12),
            "macd_slow": d("macd_dif", "slow", 26),
            "macd_signal": d("macd_dif", "signal", 9),
            "kdj_n": d("kdj_k", "n", 9),
            "kdj_m1": d("kdj_k", "m1", 3),
            "kdj_m2": d("kdj_k", "m2", 3),
            "boll_period": d("boll_upper", "period", 20),
            "boll_width": d("boll_upper", "width", 2.0),
            "atr_period": d("atr", "period", 14),
            "volume_ratio_period": d("volume_ratio", "period", 5),
            "bias_period": d("bias", "period", 6),
        }
        for key, var in self.default_vars.items():
            var.set(str(values[key]))

    def _update_color_button(self, key: str):
        color = self.color_vars[key].get()
        button = self.color_buttons[key]
        if not is_hex_color(color):
            button.configure(bg=CARD_ALT, fg=TEXT, text="无效")
            return
        button.configure(bg=color, fg=contrast_text(color), activebackground=color, text=color)

    def _choose_color(self, key: str):
        result = colorchooser.askcolor(color=self.color_vars[key].get(), parent=self, title="选择颜色")
        if result and result[1]:
            self.color_vars[key].set(result[1].lower())
            self._update_color_button(key)

    def _int_value(self, key: str, label: str, minimum: int = 1, maximum: int = 500) -> int:
        raw = self.default_vars[key].get().strip()
        try:
            value = int(float(raw))
        except ValueError as exc:
            raise ValueError(f"{label} 必须是整数") from exc
        return max(minimum, min(value, maximum))

    def _float_value(self, key: str, label: str, minimum: float = 0.1, maximum: float = 10.0) -> float:
        try:
            value = float(self.default_vars[key].get().strip())
        except ValueError as exc:
            raise ValueError(f"{label} 必须是数字") from exc
        return max(minimum, min(value, maximum))

    def _collect(self) -> UserSettings:
        for key in self.color_vars:
            if not is_hex_color(self.color_vars[key].get().strip()):
                raise ValueError(f"颜色 {key} 无效，应为 #RRGGBB")
        try:
            periods = [int(float(self.ma_period_vars[index].get().strip())) for index in range(3)]
        except ValueError as exc:
            raise ValueError("K 线均线周期必须是整数") from exc
        periods = [max(1, min(value, 500)) for value in periods]
        ma_type = self.ma_type_var.get().upper()
        if ma_type not in ("MA", "EMA"):
            raise ValueError("均线类型必须是 MA 或 EMA")
        defaults = {key: dict(value) for key, value in self.settings.indicator_defaults.items()}
        ma_period = self._int_value("ma_period", "SMA 周期")
        ema_period = self._int_value("ema_period", "EMA 周期")
        rsi_period = self._int_value("rsi_period", "RSI 周期")
        defaults.setdefault("ma", {})["period"] = ma_period
        defaults.setdefault("ema", {})["period"] = ema_period
        defaults.setdefault("rsi", {})["period"] = rsi_period
        macd = {
            "fast": self._int_value("macd_fast", "MACD 快线", 1, 200),
            "slow": self._int_value("macd_slow", "MACD 慢线", 2, 400),
            "signal": self._int_value("macd_signal", "MACD 信号线", 1, 200),
        }
        for key in ("macd_dif", "macd_dea", "macd_hist"):
            defaults.setdefault(key, {}).update(macd)
        kdj = {
            "n": self._int_value("kdj_n", "KDJ N"),
            "m1": self._int_value("kdj_m1", "KDJ M1"),
            "m2": self._int_value("kdj_m2", "KDJ M2"),
        }
        for key in ("kdj_k", "kdj_d", "kdj_j"):
            defaults.setdefault(key, {}).update(kdj)
        boll = {
            "period": self._int_value("boll_period", "BOLL 周期"),
            "width": self._float_value("boll_width", "BOLL 倍宽"),
        }
        for key in ("boll_upper", "boll_mid", "boll_lower", "boll_width"):
            defaults.setdefault(key, {}).update(boll)
        atr = self._int_value("atr_period", "ATR 周期")
        defaults.setdefault("atr", {})["period"] = atr
        defaults.setdefault("atr_pct", {})["period"] = atr
        defaults.setdefault("volume_ratio", {})["period"] = self._int_value("volume_ratio_period", "量比周期")
        defaults.setdefault("bias", {})["period"] = self._int_value("bias_period", "BIAS 周期")
        raw = {
            "schema_version": 1,
            "colors": {
                "rise": self.color_vars["rise_color"].get().strip(),
                "fall": self.color_vars["fall_color"].get().strip(),
                "buy": self.color_vars["buy_color"].get().strip(),
                "sell": self.color_vars["sell_color"].get().strip(),
                "strategy": self.color_vars["strategy_color"].get().strip(),
                "benchmark": self.color_vars["benchmark_color"].get().strip(),
                "drawdown": self.color_vars["drawdown_color"].get().strip(),
                "ma": [self.color_vars[f"ma_{index}"].get().strip() for index in range(3)],
            },
            "chart": {
                "ma_type": ma_type,
                "ma_periods": periods,
                "ma_colors": [self.color_vars[f"ma_{index}"].get().strip() for index in range(3)],
            },
            "indicator_defaults": defaults,
        }
        return UserSettings.from_dict(raw)

    def reset_defaults(self):
        self._load(UserSettings())
        self.status.configure(text="已恢复默认值；点击“保存并应用”生效。", fg=AMBER)

    def save(self):
        try:
            settings = self._collect()
        except Exception as exc:
            messagebox.showerror("设置无效", str(exc), parent=self)
            return
        self.app.apply_settings(settings)
        self.status.configure(text="已保存并应用", fg=GREEN)
        self.after(180, self.close)

    def close(self):
        try:
            self.app.settings_window = None
        except Exception:
            pass
        self.destroy()



class QuantDeployWindow(tk.Toplevel):
    def __init__(self, app, request):
        super().__init__(app)
        self.app = app
        self.request = request
        self.font_family = app.font_family
        self.meta = get_strategy_meta(request.strategy)
        self.project: QuantProject | None = None
        self.running = False
        self.cancel_event = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()
        self._poll_after = None
        self._closing = False
        self.title("生成量化 · AKQuant")
        self.configure(bg=BG)
        width, height = 1080, 880
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{width}x{height}+{max((screen_w - width) // 2, 10)}+{max((screen_h - height) // 2 - 10, 10)}")
        self.minsize(920, 720)
        self.transient(app)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self._poll_after = self.after(120, self._poll)

    def _build(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=18, pady=(14, 6))
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="生成量化 · AKQuant", bg=BG, fg=TEXT, font=(self.font_family, 18, "bold")).pack(anchor="w")
        tk.Label(
            titles,
            text=f"版本 {APP_VERSION} · 当前策略 → AKQuant 项目 → SSH 自动安装",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
        ).pack(anchor="w", pady=(2, 0))
        self.app._ghost_button(header, "关闭", self.close).pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18)
        info = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        info.pack(fill="x", padx=18, pady=(10, 8))
        strategy_name = self.meta.name if self.meta else self.request.strategy
        text = (
            f"策略快照：{strategy_name}    "
            f"股票：{self.request.symbol}    "
            f"区间：{self.request.start} ~ {self.request.end}    "
            f"复权：{self.request.trade_adjust or 'raw'}    "
            f"初始资金：{self.request.config.initial_cash:,.0f}"
        )
        tk.Label(info, text=text, bg=PANEL, fg=TEXT, font=(self.font_family, 9), anchor="w").pack(fill="x", padx=12, pady=9)
        tk.Label(
            self,
            text="v0.1.1 使用 AKQuant 信号回放回测架构：桌面端生成信号，远端 AKQuant 执行回测；实盘/实时信号重算将在后续版本扩展。",
            bg=BG,
            fg=AMBER,
            font=(self.font_family, 8),
            anchor="w",
        ).pack(fill="x", padx=20)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=8)
        body.grid_columnconfigure(0, weight=1)
        local = tk.Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        local.grid(row=0, column=0, sticky="ew")
        tk.Label(local, text="1. 本地生成", bg=PANEL, fg=TEXT, font=(self.font_family, 11, "bold"), anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 2))
        tk.Label(local, text="输出目录", bg=PANEL, fg=MUTED, font=(self.font_family, 8)).grid(row=1, column=0, sticky="w", padx=12, pady=4)
        self.parent_dir_var = tk.StringVar(value=str(PROJECT_ROOT / "exports" / "quant"))
        ttk.Entry(local, textvariable=self.parent_dir_var, width=64).grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=4)
        local.grid_columnconfigure(1, weight=1)
        self.app._ghost_button(local, "选择目录", self._choose_output_dir).grid(row=1, column=2, sticky="e", padx=(0, 12), pady=4)
        actions = tk.Frame(local, bg=PANEL)
        actions.grid(row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(4, 2))
        self.generate_button = self.app._secondary_button(actions, "生成本地 AKQuant 项目", self.generate_local)
        self.generate_button.pack(side="left")
        self.open_project_button = self.app._secondary_button(actions, "打开项目目录", self._open_project_dir, state=tk.DISABLED)
        self.open_project_button.pack(side="left", padx=(8, 0))
        self.project_label = tk.Label(local, text="尚未生成项目", bg=PANEL, fg=DIM, font=(self.font_family, 8), anchor="w", justify="left", wraplength=980)
        self.project_label.grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(2, 4))
        self.file_list = tk.Text(local, height=3, bg="#000000", fg="#ffffff", relief="flat", bd=0, padx=8, pady=4, font=(self.font_family, 8), state="disabled")
        self.file_list.grid(row=4, column=0, columnspan=3, sticky="ew", padx=12, pady=(0, 10))

        ssh = tk.Frame(body, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        ssh.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        tk.Label(ssh, text="2. SSH 自动安装", bg=PANEL, fg=TEXT, font=(self.font_family, 11, "bold"), anchor="w").grid(row=0, column=0, columnspan=6, sticky="w", padx=12, pady=(10, 2))
        fields = (
            ("IP / 主机", "host", "192.168.1.10", 18),
            ("端口", "port", "22", 7),
            ("用户名", "username", "root", 12),
            ("密码", "password", "", 16),
            ("远程目录", "remote_dir", "stock_strategy_quant", 20),
        )
        self.ssh_vars = {}
        for index, (label, key, default, width) in enumerate(fields):
            row = 1 + index // 3
            col = (index % 3) * 2
            tk.Label(ssh, text=label, bg=PANEL, fg=MUTED, font=(self.font_family, 8)).grid(row=row, column=col, sticky="w", padx=(12 if col == 0 else 8, 2), pady=4)
            var = tk.StringVar(value=default)
            self.ssh_vars[key] = var
            entry = ttk.Entry(ssh, textvariable=var, width=width, show="*" if key == "password" else "")
            entry.grid(row=row, column=col + 1, sticky="w", pady=4)
        self.run_after_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ssh, text="安装后运行一次 AKQuant 回测", variable=self.run_after_var).grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(4, 2))
        tk.Label(
            ssh,
            text="密码只用于本次 SSH 连接，不写入磁盘、不上传、不写日志。首次连接会自动信任主机指纹；生产环境建议改用 SSH Key。",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
            wraplength=1000,
        ).grid(row=4, column=0, columnspan=6, sticky="w", padx=12, pady=(2, 4))
        install_row = tk.Frame(ssh, bg=PANEL)
        install_row.grid(row=5, column=0, columnspan=6, sticky="w", padx=12, pady=(4, 10))
        self.install_button = self.app._primary_button(install_row, "生成并 SSH 安装", self.generate_and_deploy)
        self.install_button.pack(side="left")
        self.stop_button = self.app._secondary_button(install_row, "停止", self.stop, state=tk.DISABLED)
        self.stop_button.pack(side="left", padx=(8, 0))
        self.ssh_status = tk.Label(install_row, text="", bg=PANEL, fg=DIM, font=(self.font_family, 8))
        self.ssh_status.pack(side="left", padx=14)
        self.progress = ttk.Progressbar(body, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        log_frame = tk.Frame(body, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        body.grid_rowconfigure(3, weight=1)
        self.log_text = tk.Text(log_frame, bg="#000000", fg="#ffffff", relief="flat", bd=0, wrap="word", padx=10, pady=8, font=(self.font_family, 8), state="disabled")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log_text.tag_configure("info", foreground="#ffffff")
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("error", foreground=RED)

    def _choose_output_dir(self):
        path = filedialog.askdirectory(parent=self, title="选择量化项目输出目录", initialdir=str(PROJECT_ROOT / "exports"))
        if path:
            self.parent_dir_var.set(path)

    def _log(self, message: str, tag: str = "info"):
        if not self.log_text.winfo_exists():
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_running(self, running: bool):
        self.running = running
        state = tk.DISABLED if running else tk.NORMAL
        self.generate_button.configure(state=state)
        self.install_button.configure(state=state)
        self.stop_button.configure(state=tk.NORMAL if running else tk.DISABLED)
        if running:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _set_project(self, project: QuantProject):
        self.project = project
        self.project_label.configure(text=f"项目目录：{project.path}")
        self.file_list.configure(state="normal")
        self.file_list.delete("1.0", "end")
        self.file_list.insert("end", "\n".join(project.files))
        self.file_list.configure(state="disabled")
        self.open_project_button.configure(state=tk.NORMAL)
        self.ssh_status.configure(text="项目已就绪，可以 SSH 安装", fg=GREEN)

    def _worker_generate(self, parent_dir: str):
        try:
            self.msg_queue.put(("log", "开始生成 AKQuant 项目 ..."))
            project = generate_quant_project(
                self.request,
                parent_dir,
                progress=lambda message: self.msg_queue.put(("log", message)),
            )
            self.msg_queue.put(("generated_local", project))
        except Exception as exc:
            self.msg_queue.put(("error", (str(exc), traceback.format_exc())))

    def generate_local(self):
        if self.running:
            return
        parent_dir = self.parent_dir_var.get().strip() or str(PROJECT_ROOT / "exports" / "quant")
        self._set_running(True)
        self.ssh_status.configure(text="正在生成项目 ...", fg=MUTED)
        threading.Thread(target=self._worker_generate, args=(parent_dir,), daemon=True).start()

    def _read_deploy_config(self) -> RemoteDeployConfig:
        try:
            port = int(self.ssh_vars["port"].get().strip())
        except ValueError as exc:
            raise ValueError("SSH 端口必须是数字") from exc
        host = self.ssh_vars["host"].get().strip()
        username = self.ssh_vars["username"].get().strip()
        password = self.ssh_vars["password"].get()
        if not host:
            raise ValueError("请填写 IP / 主机")
        if not username:
            raise ValueError("请填写用户名")
        if not password:
            raise ValueError("请填写 SSH 密码")
        return RemoteDeployConfig(
            host=host,
            port=port,
            username=username,
            password=password,
            remote_dir=self.ssh_vars["remote_dir"].get().strip() or "stock_strategy_quant",
            run_after_install=bool(self.run_after_var.get()),
        )

    def _worker_deploy(self, parent_dir: str, deploy_config):
        try:
            project = self.project
            if project is None:
                self.msg_queue.put(("log", "尚未生成项目，先生成本地项目 ..."))
                project = generate_quant_project(
                    self.request,
                    parent_dir,
                    progress=lambda message: self.msg_queue.put(("log", message)),
                )
                self.msg_queue.put(("generated_for_deploy", project))
            self.msg_queue.put(("log", f"开始 SSH 部署到 {deploy_config.host}:{deploy_config.port} ..."))
            result = deploy_project(
                project,
                deploy_config,
                progress=lambda message: self.msg_queue.put(("log", message)),
                cancel_event=self.cancel_event,
            )
            self.msg_queue.put(("deployed", result))
        except Exception as exc:
            self.msg_queue.put(("error", (str(exc), traceback.format_exc())))

    def generate_and_deploy(self):
        if self.running:
            return
        try:
            deploy_config = self._read_deploy_config()
        except Exception as exc:
            messagebox.showerror("SSH 参数错误", str(exc), parent=self)
            return
        parent_dir = self.parent_dir_var.get().strip() or str(PROJECT_ROOT / "exports" / "quant")
        self.cancel_event.clear()
        self._set_running(True)
        self.ssh_status.configure(text="正在生成/安装 ...", fg=MUTED)
        self._log("目标：AKQuant 信号回放项目 · SSH 自动安装", "info")
        threading.Thread(target=self._worker_deploy, args=(parent_dir, deploy_config), daemon=True).start()

    def stop(self):
        if self.running:
            self.cancel_event.set()
            self.ssh_status.configure(text="正在取消 ...", fg=AMBER)

    def _open_project_dir(self):
        if self.project is None:
            return
        try:
            subprocess.Popen(["xdg-open", str(self.project.path)])
        except Exception:
            webbrowser.open(self.project.path.as_uri())

    def _poll(self):
        if self._closing or not self.winfo_exists():
            return
        while True:
            try:
                kind, payload = self.msg_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload))
                if str(payload).startswith("  "):
                    self.ssh_status.configure(text=str(payload).strip())
            elif kind == "generated_local":
                self._set_project(payload)
                self._set_running(False)
                self._log(f"项目已生成: {payload.path}", "ok")
            elif kind == "generated_for_deploy":
                self._set_project(payload)
                self._log(f"项目已生成: {payload.path}", "ok")
            elif kind == "deployed":
                self._set_running(False)
                remote = payload.get("remote_path", "")
                self.ssh_status.configure(text=f"安装完成: {remote}", fg=GREEN)
                self._log(f"SSH 安装完成: {remote}", "ok")
                self._log(f"远程运行: cd {remote} && ./run.sh", "ok")
                if payload.get("ran_backtest"):
                    self._log("远程 AKQuant 回测已运行，结果保存在远程 output/backtest_result.txt", "ok")
                messagebox.showinfo(
                    "安装完成",
                    f"项目已安装到：\n{remote}\n\n远程运行：\ncd {remote} && ./run.sh",
                    parent=self,
                )
            elif kind == "error":
                self._set_running(False)
                message, details = payload
                self.ssh_status.configure(text="失败", fg=RED)
                self._log(f"失败：{message}", "error")
                self._log(details, "error")
                messagebox.showerror("操作失败", message, parent=self)
        self._poll_after = self.after(120, self._poll)

    def close(self):
        if self.running:
            if not messagebox.askyesno("正在执行", "生成/安装正在进行，确定关闭窗口吗？", parent=self):
                return
            self.cancel_event.set()
        self._closing = True
        try:
            if self._poll_after:
                self.after_cancel(self._poll_after)
        except Exception:
            pass
        try:
            self.app.quant_window = None
        except Exception:
            pass
        self.ssh_vars["password"].set("")
        self.destroy()


class StrategyStudio(ROOT_BASE):
    def __init__(self):
        super().__init__()
        self.font_family = choose_font(self)
        self.title("股票策略工坊")
        self.configure(bg=BG)
        self.geometry("1420x900")
        self.minsize(1180, 760)
        self.output: RunOutput | None = None
        self.running = False
        self.custom_editor: CustomStrategyEditor | None = None
        self.import_dialog: DataImportDialog | None = None
        self.market_window: MarketBacktestWindow | None = None
        self.quant_window: QuantDeployWindow | None = None
        self.msg_queue: queue.Queue = queue.Queue()
        self.session = self._read_session()
        self.settings = load_settings()
        self.settings_window = None
        self.strategy_meta_map = {}
        self.param_vars: dict[str, tk.Variable] = {}
        self.model_combo_maps: dict[str, dict[str, str]] = {}
        self.scroll_frames: list[ScrollableFrame] = []
        self._place_window()
        self._configure_style()
        self._build()
        self.bind_all("<MouseWheel>", self._route_wheel, add="+")
        self.bind_all("<Button-4>", self._route_wheel, add="+")
        self.bind_all("<Button-5>", self._route_wheel, add="+")
        self._apply_session()
        self._show_load_warnings()
        self.refresh_local_stats()
        native_ok, native_path = rust_status()
        self._log(
            f"回测引擎: {'Rust 加速可用' if native_ok else 'Python（Rust 扩展不可用）'} · {native_path}",
            "ok" if native_ok else "warn",
        )
        self._poll_after = self.after(100, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if Path(PROJECT_ROOT / "assets" / "icon.png").exists():
            try:
                self.iconphoto(True, tk.PhotoImage(file=str(PROJECT_ROOT / "assets" / "icon.png")))
            except Exception:
                pass

    def register_scroll(self, frame: ScrollableFrame) -> None:
        if frame not in self.scroll_frames:
            self.scroll_frames.append(frame)

    def unregister_scroll(self, frame: ScrollableFrame) -> None:
        if frame in self.scroll_frames:
            self.scroll_frames.remove(frame)

    def _route_wheel(self, event):
        widget = getattr(event, "widget", None)
        for frame in list(self.scroll_frames):
            if frame.contains_widget(widget):
                return frame.scroll_event(event)
        return None

    def _read_session(self) -> dict[str, Any]:
        import json

        if SESSION_FILE.exists():
            try:
                return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _place_window(self):
        width, height = 1420, 900
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max((screen_w - width) // 2, 20)
        y = max((screen_h - height) // 2 - 10, 20)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        family = self.font_family
        style.configure(
            "TEntry",
            fieldbackground=CARD_ALT,
            foreground=TEXT,
            insertcolor=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=6,
            font=(family, 9),
        )
        style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
        style.configure(
            "TCombobox",
            fieldbackground=CARD_ALT,
            background=CARD_ALT,
            foreground=TEXT,
            arrowcolor=MUTED,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            padding=4,
            font=(family, 9),
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", CARD_ALT)],
            foreground=[("readonly", TEXT)],
            selectbackground=[("readonly", CARD_ALT)],
            selectforeground=[("readonly", TEXT)],
            bordercolor=[("focus", ACCENT)],
        )
        self.option_add("*TCombobox*Listbox.background", CARD_ALT)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", "#000000")
        self.option_add("*TCombobox*Listbox.font", (family, 9))
        style.configure(
            "TCheckbutton",
            background=PANEL,
            foreground=MUTED,
            indicatorbackground=CARD_ALT,
            indicatorforeground=TEXT,
            upperbordercolor=BORDER,
            lowerbordercolor=BORDER,
            focuscolor=PANEL,
            font=(family, 9),
        )
        style.map(
            "TCheckbutton",
            background=[("active", PANEL)],
            foreground=[("active", TEXT)],
            indicatorbackground=[("selected", ACCENT), ("active", BORDER)],
            indicatorforeground=[("selected", "#000000")],
        )
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            borderwidth=0,
            rowheight=29,
            font=(family, 9),
        )
        style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#000000")])
        style.configure(
            "Treeview.Heading",
            background=CARD_ALT,
            foreground=MUTED,
            relief="flat",
            padding=(7, 7),
            font=(family, 9, "bold"),
        )
        style.map("Treeview.Heading", background=[("active", CARD_ALT)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure(
            "TNotebook.Tab",
            background=PANEL,
            foreground=MUTED,
            padding=(18, 9),
            font=(family, 9),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT), ("active", CARD_ALT)],
            foreground=[("selected", "#000000"), ("active", TEXT)],
        )
        style.configure(
            "Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=CARD_ALT,
            bordercolor=CARD_ALT,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        style.configure(
            "Vertical.TScrollbar",
            background=CARD_ALT,
            troughcolor=PANEL,
            bordercolor=PANEL,
            arrowcolor=MUTED,
        )
        style.map("Vertical.TScrollbar", background=[("active", BORDER)])
        style.configure(
            "Horizontal.TScrollbar",
            background=CARD_ALT,
            troughcolor=PANEL,
            bordercolor=PANEL,
            arrowcolor=MUTED,
        )

    def _build(self):
        self._build_header()
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(12, 0))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self.left_panel = tk.Frame(body, bg=PANEL, width=360, highlightbackground=BORDER, highlightthickness=1)
        self.left_panel.grid(row=0, column=0, sticky="ns", padx=(0, 14))
        self.left_panel.grid_propagate(False)
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)
        self._build_controls()
        self._build_right(right)
        self._build_statusbar()

    def _build_header(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=22, pady=(18, 2))
        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="股票策略工坊",
            bg=BG,
            fg=TEXT,
            font=(self.font_family, 22, "bold"),
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="AKShare 行情 · 可插拔预测模型 · 自定义策略回测",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 10),
        ).pack(anchor="w", pady=(2, 0))
        actions = tk.Frame(header, bg=BG)
        actions.pack(side="right", pady=(8, 0))
        for text, command in (
            ("导入数据", self._open_import_dialog),
            ("全市场回测", self._open_market_window),
            ("生成量化", self._open_quant_window),
            ("设置", self._open_settings),
            ("模型接口文档", lambda: self._open_doc("MODEL_API.md")),
            ("策略接口文档", lambda: self._open_doc("STRATEGY_API.md")),
            ("数据目录", self._open_data_dir),
        ):
            self._ghost_button(actions, text, command).pack(side="left", padx=(8, 0))
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", padx=18, pady=(14, 0))

    def _ghost_button(self, parent, text, command):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=BG,
            fg=MUTED,
            activebackground=CARD_ALT,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=13,
            pady=7,
            cursor="hand2",
            font=(self.font_family, 9),
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BORDER,
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=CARD, fg=TEXT))
        button.bind("<Leave>", lambda _e: button.configure(bg=BG, fg=MUTED))
        return button

    def _primary_button(self, parent, text, command):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT,
            fg="#000000",
            activebackground=ACCENT_HOVER,
            activeforeground="#000000",
            relief="flat",
            bd=0,
            padx=12,
            pady=11,
            cursor="hand2",
            font=(self.font_family, 11, "bold"),
            disabledforeground="#00343d",
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=ACCENT_HOVER))
        button.bind("<Leave>", lambda _e: button.configure(bg=ACCENT))
        return button

    def _secondary_button(self, parent, text, command, state=tk.NORMAL):
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=CARD_ALT,
            fg=TEXT,
            activebackground=BORDER,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            padx=12,
            pady=9,
            cursor="hand2",
            font=(self.font_family, 9),
            state=state,
            disabledforeground=DIM,
        )
        return button

    def _section(self, parent, title: str, subtitle: str = ""):
        box = tk.Frame(parent, bg=PANEL)
        box.pack(fill="x", padx=18, pady=(16, 0))
        tk.Label(box, text=title, bg=PANEL, fg=TEXT, font=(self.font_family, 11, "bold"), anchor="w").pack(fill="x")
        if subtitle:
            tk.Label(box, text=subtitle, bg=PANEL, fg=DIM, font=(self.font_family, 8), anchor="w", justify="left").pack(fill="x", pady=(1, 0))
        tk.Frame(box, bg=BORDER, height=1).pack(fill="x", pady=(8, 2))
        return box

    def _row(self, parent, label: str):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=4)
        tk.Label(row, text=label, bg=PANEL, fg=MUTED, font=(self.font_family, 9), width=13, anchor="w").pack(side="left")
        return row

    def _entry(self, parent, variable, width=17):
        entry = ttk.Entry(parent, textvariable=variable, width=width, style="TEntry")
        entry.pack(side="left", fill="x", expand=True)
        return entry

    def _build_controls(self):
        scroll = ScrollableFrame(self.left_panel, PANEL)
        self.left_scroll = scroll
        self.register_scroll(scroll)
        scroll.pack(fill="both", expand=True)
        inner = scroll.inner
        data_box = self._section(inner, "01 行情", "本地数据优先；未覆盖时 AKShare 补拉；失败回退示例")
        row = self._row(data_box, "股票代码")
        self.var_symbol = tk.StringVar(value=self.session.get("symbol", "sz000001"))
        self._entry(row, self.var_symbol)
        row = self._row(data_box, "回测区间")
        self.var_start = tk.StringVar(value=self.session.get("start", str(date.today() - timedelta(days=365 * 3))))
        self.var_end = tk.StringVar(value=self.session.get("end", str(date.today())))
        ttk.Entry(row, textvariable=self.var_start, width=10, style="TEntry").pack(side="left", fill="x", expand=True)
        tk.Label(row, text="至", bg=PANEL, fg=DIM, font=(self.font_family, 8)).pack(side="left", padx=5)
        ttk.Entry(row, textvariable=self.var_end, width=10, style="TEntry").pack(side="left", fill="x", expand=True)
        row = self._row(data_box, "基准指数")
        self.var_benchmark = tk.StringVar(value=self.session.get("benchmark", "sh000001"))
        self._entry(row, self.var_benchmark)
        row = self._row(data_box, "交易复权")
        self.var_adjust = tk.StringVar(value=self.session.get("adjust", "qfq"))
        combo = ttk.Combobox(row, textvariable=self.var_adjust, values=("qfq", "hfq"), state="readonly", width=12)
        combo.pack(side="left", fill="x", expand=True)
        self.var_offline = tk.BooleanVar(value=bool(self.session.get("offline", False)))
        offline = ttk.Checkbutton(data_box, text="离线演示（内置示例数据，仅 sz000001）", variable=self.var_offline)
        offline.pack(anchor="w", pady=(8, 2))
        self.local_stats_label = tk.Label(
            data_box,
            text="本地库: --",
            bg=PANEL,
            fg=GREEN,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.local_stats_label.pack(fill="x", pady=(4, 0))

        strategy_box = self._section(inner, "02 策略", "策略与参数由接口注册表自动发现")
        row = self._row(strategy_box, "选择策略")
        self.var_strategy_name = tk.StringVar()
        self.strategy_combo = ttk.Combobox(row, textvariable=self.var_strategy_name, state="readonly", width=24)
        self.strategy_combo.pack(side="left", fill="x", expand=True)
        self.strategy_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_strategy_changed())
        self.strategy_desc = tk.Label(
            strategy_box,
            text="",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.strategy_desc.pack(fill="x", pady=(5, 1))
        self.params_frame = tk.Frame(strategy_box, bg=PANEL)
        self.params_frame.pack(fill="x", pady=(3, 0))
        self.model_status = tk.Label(
            strategy_box,
            text="",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            justify="left",
            wraplength=310,
        )
        self.model_status.pack(fill="x", pady=(5, 0))
        custom_button = self._secondary_button(
            strategy_box,
            "打开自定义策略编辑器（指标 / AND-OR 条件）",
            self._open_custom_editor,
        )
        custom_button.pack(fill="x", pady=(8, 2))
        quant_button = self._secondary_button(
            strategy_box,
            "生成量化 · AKQuant 项目 / SSH 自动安装",
            self._open_quant_window,
        )
        quant_button.pack(fill="x", pady=(0, 2))

        trade_box = self._section(inner, "03 交易参数", "信号 T 日收盘生成，T+1 开盘成交")
        row = self._row(trade_box, "初始资金")
        self.var_cash = tk.StringVar(value=str(self.session.get("initial_cash", 100000.0)))
        self._entry(row, self.var_cash)
        row = self._row(trade_box, "佣金 %")
        self.var_commission = tk.StringVar(value=str(self.session.get("commission_pct", 0.025)))
        self._entry(row, self.var_commission)
        row = self._row(trade_box, "印花税 %")
        self.var_stamp = tk.StringVar(value=str(self.session.get("stamp_pct", 0.05)))
        self._entry(row, self.var_stamp)
        row = self._row(trade_box, "滑点 bp")
        self.var_slippage = tk.StringVar(value=str(self.session.get("slippage_bps", 5.0)))
        self._entry(row, self.var_slippage)
        row = self._row(trade_box, "仓位 %")
        self.var_position = tk.StringVar(value=str(self.session.get("position_pct", 95.0)))
        self._entry(row, self.var_position)
        row = self._row(trade_box, "止损 %")
        self.var_stop = tk.StringVar(value=str(self.session.get("stop_loss_pct", 0.0)))
        self._entry(row, self.var_stop)
        row = self._row(trade_box, "止盈 %")
        self.var_take = tk.StringVar(value=str(self.session.get("take_profit_pct", 0.0)))
        self._entry(row, self.var_take)
        row = self._row(trade_box, "最长持有")
        self.var_hold = tk.StringVar(value=str(self.session.get("max_hold_days", 0)))
        self._entry(row, self.var_hold)
        self.var_close_end = tk.BooleanVar(value=bool(self.session.get("close_at_end", True)))
        ttk.Checkbutton(trade_box, text="期末清仓", variable=self.var_close_end).pack(anchor="w", pady=(6, 0))

        run_box = tk.Frame(inner, bg=PANEL)
        run_box.pack(fill="x", padx=18, pady=(18, 10))
        self.run_button = self._primary_button(run_box, "开始回测   Ctrl+Enter", self._start_run)
        self.run_button.pack(fill="x")
        self.export_button = self._secondary_button(run_box, "导出结果", self._export, state=tk.DISABLED)
        self.export_button.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(run_box, mode="indeterminate", style="Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(10, 4))
        self.left_status = tk.Label(
            run_box,
            text="就绪",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
            wraplength=310,
            justify="left",
        )
        self.left_status.pack(fill="x")
        self.bind("<Control-Return>", lambda _e: self._start_run())
        self.bind("<Control-e>", lambda _e: self._export())

    def _build_right(self, parent):
        top = tk.Frame(parent, bg=BG)
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(0, weight=1)
        self.summary_title = tk.Label(
            top,
            text="回测概览",
            bg=BG,
            fg=TEXT,
            font=(self.font_family, 13, "bold"),
            anchor="w",
        )
        self.summary_title.grid(row=0, column=0, sticky="w")
        self.signal_label = tk.Label(
            top,
            text="",
            bg=BG,
            fg=MUTED,
            font=(self.font_family, 9),
            anchor="e",
        )
        self.signal_label.grid(row=0, column=1, sticky="e")
        self.summary_sub = tk.Label(
            top,
            text="选择策略与参数后开始回测，结果将在此展示。",
            bg=BG,
            fg=DIM,
            font=(self.font_family, 9),
            anchor="w",
        )
        self.summary_sub.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        cards = tk.Frame(parent, bg=BG)
        cards.grid(row=1, column=0, sticky="ew", pady=(12, 14))
        for col in range(6):
            cards.grid_columnconfigure(col, weight=1, uniform="metric")
        self.cards = {
            "return": MetricCard(cards, "总收益", self.font_family),
            "annual": MetricCard(cards, "年化收益", self.font_family),
            "drawdown": MetricCard(cards, "最大回撤", self.font_family),
            "sharpe": MetricCard(cards, "Sharpe", self.font_family),
            "win": MetricCard(cards, "胜率", self.font_family),
            "trades": MetricCard(cards, "交易次数", self.font_family),
        }
        for idx, card in enumerate(self.cards.values()):
            card.grid(row=0, column=idx, sticky="ew", padx=(0 if idx == 0 else 6, 0 if idx == 5 else 0))
        self.notebook = ttk.Notebook(parent)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        chart_tab = tk.Frame(self.notebook, bg=CARD)
        price_tab = tk.Frame(self.notebook, bg=CARD)
        trades_tab = tk.Frame(self.notebook, bg=CARD)
        log_tab = tk.Frame(self.notebook, bg=CARD)
        self.notebook.add(chart_tab, text="净值与回撤")
        self.notebook.add(price_tab, text="K线与交易点")
        self.notebook.add(trades_tab, text="交易明细")
        self.notebook.add(log_tab, text="运行日志")
        self.equity_chart = EquityChart(chart_tab, self.font_family, self.settings)
        self.equity_chart.pack(fill="both", expand=True, padx=2, pady=2)
        self.kline_chart = KLineChart(price_tab, self.font_family, self.settings)
        self.kline_chart.pack(fill="both", expand=True, padx=2, pady=2)
        self._build_trade_table(trades_tab)
        self._build_log(log_tab)

    def _build_trade_table(self, parent):
        wrapper = tk.Frame(parent, bg=CARD)
        wrapper.pack(fill="both", expand=True)
        columns = (
            "no", "entry_date", "exit_date", "hold", "entry_price", "exit_price",
            "shares", "pnl", "return_pct", "entry_reason", "exit_reason",
        )
        headings = {
            "no": "#",
            "entry_date": "买入日",
            "exit_date": "卖出日",
            "hold": "持有",
            "entry_price": "买入价",
            "exit_price": "卖出价",
            "shares": "股数",
            "pnl": "净盈亏",
            "return_pct": "收益率",
            "entry_reason": "入场原因",
            "exit_reason": "离场原因",
        }
        widths = {
            "no": 38, "entry_date": 94, "exit_date": 94, "hold": 48,
            "entry_price": 78, "exit_price": 78, "shares": 70, "pnl": 92,
            "return_pct": 76, "entry_reason": 190, "exit_reason": 170,
        }
        self.trade_tree = ttk.Treeview(wrapper, columns=columns, show="headings", style="Treeview")
        for col in columns:
            self.trade_tree.heading(col, text=headings[col], anchor="w")
            self.trade_tree.column(col, width=widths[col], minwidth=widths[col] // 2, anchor="w", stretch=col in ("entry_reason", "exit_reason"))
        self.trade_tree.tag_configure("win", foreground=self.settings.rise_color)
        self.trade_tree.tag_configure("loss", foreground=self.settings.fall_color)
        yscroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.trade_tree.yview)
        xscroll = ttk.Scrollbar(wrapper, orient="horizontal", command=self.trade_tree.xview)
        self.trade_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.trade_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        wrapper.grid_rowconfigure(0, weight=1)
        wrapper.grid_columnconfigure(0, weight=1)
        self.trade_summary = tk.Label(
            wrapper,
            text="暂无成交",
            bg=CARD,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="w",
        )
        self.trade_summary.grid(row=2, column=0, sticky="ew", padx=8, pady=4)

    def _build_log(self, parent):
        self.log_text = tk.Text(
            parent,
            bg="#000000",
            fg="#ffffff",
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            wrap="word",
            padx=12,
            pady=10,
            font=(self.font_family, 9),
            state="disabled",
        )
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.log_text.tag_configure("info", foreground="#ffffff")
        self.log_text.tag_configure("ok", foreground=GREEN)
        self.log_text.tag_configure("warn", foreground=AMBER)
        self.log_text.tag_configure("error", foreground=RED)
        self.log_text.tag_configure("dim", foreground=DIM)

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        self.status_text = tk.Label(
            bar,
            text="就绪 · 在线数据源 AKShare",
            bg=PANEL,
            fg=MUTED,
            font=(self.font_family, 8),
            anchor="w",
            padx=12,
            pady=6,
        )
        self.status_text.pack(side="left")
        self.status_right = tk.Label(
            bar,
            text="",
            bg=PANEL,
            fg=DIM,
            font=(self.font_family, 8),
            anchor="e",
            padx=12,
            pady=6,
        )
        self.status_right.pack(side="right")

    def _apply_session(self):
        metas = list_strategies()
        self.strategy_meta_map = {meta.name: meta for meta in metas}
        names = [meta.name for meta in metas]
        self.strategy_combo.configure(values=names)
        saved_slug = self.session.get("strategy", "sm701")
        selected = next((meta.name for meta in metas if meta.slug == saved_slug), names[0] if names else "")
        if selected:
            self.var_strategy_name.set(selected)
            self._on_strategy_changed(self.session.get("strategy_params", {}))
        else:
            self.strategy_desc.configure(text="没有发现可用策略，请检查 strategies/plugins")
        self._log("股票策略工坊已启动", "ok")
        if MODEL_LOAD_ERRORS:
            for error in MODEL_LOAD_ERRORS:
                self._log(f"模型插件加载失败: {error}", "warn")
        if STRATEGY_LOAD_ERRORS:
            for error in STRATEGY_LOAD_ERRORS:
                self._log(f"策略插件加载失败: {error}", "warn")
        self._log("数据源: AKShare（东方财富/腾讯/新浪多通道）+ 本地 SQLite 缓存", "dim")

    def _show_load_warnings(self):
        self.status_right.configure(text="Ctrl+Enter 回测 · Ctrl+E 导出")

    def _current_meta(self):
        return self.strategy_meta_map.get(self.var_strategy_name.get())

    def _on_strategy_changed(self, saved_params: dict[str, Any] | None = None):
        meta = self._current_meta()
        for child in self.params_frame.winfo_children():
            child.destroy()
        self.param_vars.clear()
        self.model_combo_maps.clear()
        if meta is None:
            return
        self.strategy_desc.configure(text=meta.description)
        saved_params = saved_params or {}
        models = list_models()
        for spec in meta.params:
            row = tk.Frame(self.params_frame, bg=PANEL)
            row.pack(fill="x", pady=4)
            tk.Label(
                row,
                text=spec.label,
                bg=PANEL,
                fg=MUTED,
                font=(self.font_family, 9),
                width=13,
                anchor="w",
            ).pack(side="left")
            saved = saved_params.get(spec.key, spec.default)
            if spec.kind == "bool":
                var = tk.BooleanVar(value=bool(saved))
                ttk.Checkbutton(row, text="启用", variable=var).pack(side="left", anchor="w")
            elif spec.kind == "choice":
                var = tk.StringVar(value=str(saved))
                ttk.Combobox(
                    row,
                    textvariable=var,
                    values=[str(v) for v in spec.choices],
                    state="readonly",
                    width=15,
                ).pack(side="left", fill="x", expand=True)
            elif spec.kind == "model":
                labels: dict[str, str] = {}
                reverse: dict[str, str] = {}
                for model_meta in models:
                    label = f"{model_meta.name} ({model_meta.slug})"
                    labels[model_meta.slug] = label
                    reverse[label] = model_meta.slug
                saved_slug = str(saved or "")
                var = tk.StringVar(value=labels.get(saved_slug, next(iter(labels.values()), "")))
                ttk.Combobox(
                    row,
                    textvariable=var,
                    values=list(labels.values()),
                    state="readonly",
                    width=15,
                ).pack(side="left", fill="x", expand=True)
                self.model_combo_maps[spec.key] = reverse
            else:
                var = tk.StringVar(value=str(saved))
                ttk.Entry(row, textvariable=var, width=15).pack(side="left", fill="x", expand=True)
            self.param_vars[spec.key] = var
            if spec.help:
                tk.Label(
                    self.params_frame,
                    text=spec.help,
                    bg=PANEL,
                    fg=DIM,
                    font=(self.font_family, 7),
                    anchor="w",
                ).pack(fill="x", padx=(88, 0))
        self._update_model_status()

    def _model_slug_from_params(self) -> str:
        meta = self._current_meta()
        if not meta or not meta.requires_model:
            return ""
        chosen = None
        for spec in meta.params:
            if spec.kind == "model":
                var = self.param_vars.get(spec.key)
                if var is not None:
                    value = var.get()
                    chosen = self.model_combo_maps.get(spec.key, {}).get(value, value)
                break
        return str(chosen or meta.default_model or "")

    def _update_model_status(self):
        meta = self._current_meta()
        if not meta or not meta.requires_model:
            self.model_status.configure(text="", fg=DIM)
            return
        slug = self._model_slug_from_params()
        if not slug:
            self.model_status.configure(text="模型：未选择", fg=AMBER)
            return
        try:
            model = create_model(slug)
            if model.available():
                self.model_status.configure(
                    text=f"模型：{model.meta.name} · v{model.meta.version} · 已就绪 · 特征复权 {model.meta.adjust}",
                    fg=GREEN,
                )
            else:
                self.model_status.configure(text=f"模型 {slug} 不可用，请检查 artifacts", fg=RED)
        except Exception as exc:
            self.model_status.configure(text=f"模型加载失败：{exc}", fg=RED)

    def _open_quant_window(self):
        if self.quant_window is not None and self.quant_window.winfo_exists():
            self.quant_window.lift()
            self.quant_window.focus_set()
            return
        try:
            base_request = self._collect_request()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)
            return
        self.quant_window = QuantDeployWindow(self, base_request)

    def _open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_set()
            return
        self.settings_window = SettingsWindow(self)

    def apply_settings(self, settings: UserSettings):
        save_settings(settings)
        self.settings = settings
        self.equity_chart.settings = settings
        self.kline_chart.settings = settings
        self.equity_chart.redraw()
        self.kline_chart.redraw()
        self.trade_tree.tag_configure("win", foreground=settings.rise_color)
        self.trade_tree.tag_configure("loss", foreground=settings.fall_color)
        if self.custom_editor is not None and self.custom_editor.winfo_exists():
            self.custom_editor.indicator_defaults = settings.indicator_defaults
        if self.market_window is not None and self.market_window.winfo_exists():
            try:
                self.market_window.apply_theme()
            except Exception:
                pass
        self._refresh_current_theme()
        self._log("个性化设置已保存并应用", "ok")

    def _refresh_current_theme(self):
        if self.output is None:
            return
        metrics = self.output.result.metrics
        total = metrics.get("total_return_pct")
        annual = metrics.get("annual_return_pct")
        dd = metrics.get("max_drawdown_pct")
        sharpe = metrics.get("sharpe")
        rise = self.settings.rise_color
        fall = self.settings.fall_color
        self.cards["return"].value.configure(fg=rise if (total or 0) >= 0 else fall)
        self.cards["annual"].value.configure(fg=rise if (annual or 0) >= 0 else fall)
        self.cards["drawdown"].value.configure(fg=fall if dd is not None and dd < 0 else TEXT)
        self.cards["sharpe"].value.configure(fg=rise if (sharpe or 0) >= 1 else TEXT)
        latest = self.output.latest_signal
        if latest:
            if latest["entry"]:
                self.signal_label.configure(fg=self.settings.buy_color)
            elif latest["exit"]:
                self.signal_label.configure(fg=self.settings.sell_color)
            else:
                self.signal_label.configure(fg=MUTED)

    def _open_import_dialog(self, on_done=None):
        if self.import_dialog is not None and self.import_dialog.winfo_exists():
            if on_done is not None:
                self.import_dialog.on_done = on_done
            self.import_dialog.lift()
            self.import_dialog.focus_set()
            return
        self.import_dialog = DataImportDialog(self, on_done=on_done)

    def _open_market_window(self):
        if self.market_window is not None and self.market_window.winfo_exists():
            self.market_window.lift()
            self.market_window.focus_set()
            return
        try:
            base_request = self._collect_request()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)
            return
        self.market_window = MarketBacktestWindow(self, base_request)

    def refresh_local_stats(self):
        try:
            summary = CacheStore(DATA_DIR / "cache.db").summary()
        except Exception:
            summary = {}
        if not summary:
            text = "本地库: 暂无数据（点击顶部「导入数据」）"
        else:
            parts = []
            for adjust in ("qfq", "hfq", ""):
                info = summary.get(adjust)
                if info:
                    label = adjust or "raw"
                    parts.append(f"{label} {info['symbols']}只/{info['bars']:,}根")
            text = "本地库: " + " · ".join(parts) if parts else "本地库: 暂无数据"
        if hasattr(self, "local_stats_label"):
            self.local_stats_label.configure(text=text)
        market_window = getattr(self, "market_window", None)
        if market_window is not None and market_window.winfo_exists():
            try:
                market_window.refresh_data_summary()
            except Exception:
                pass

    def _open_custom_editor(self):
        if self.custom_editor is not None and self.custom_editor.winfo_exists():
            self.custom_editor.lift()
            self.custom_editor.focus_set()
            return
        initial = None
        meta = self._current_meta()
        if meta and str(meta.slug).startswith("custom_"):
            initial = get_definition(meta.slug)
        self.custom_editor = CustomStrategyEditor(self, initial=initial)

    def _current_param_values(self) -> dict[str, Any]:
        meta = self._current_meta()
        if meta is None:
            return {}
        params: dict[str, Any] = {}
        for spec in meta.params:
            var = self.param_vars.get(spec.key)
            if var is None:
                continue
            value = var.get()
            if spec.kind == "model":
                value = self.model_combo_maps.get(spec.key, {}).get(value, value)
            params[spec.key] = value
        return params

    def refresh_strategy_list(self, select_slug: str | None = None):
        current_meta = self._current_meta()
        current_slug = current_meta.slug if current_meta else None
        current_params = self._current_param_values() if current_meta else {}
        metas = list_strategies()
        self.strategy_meta_map = {meta.name: meta for meta in metas}
        names = [meta.name for meta in metas]
        self.strategy_combo.configure(values=names)
        target = next((meta for meta in metas if meta.slug == select_slug), None) if select_slug else None
        if target is None:
            target = next((meta for meta in metas if meta.slug == current_slug), None)
        if target is None:
            target = metas[0] if metas else None
        if target is None:
            self.strategy_desc.configure(text="没有发现可用策略，请检查 strategies/plugins")
            return
        same_strategy = current_slug == target.slug
        self.var_strategy_name.set(target.name)
        self._on_strategy_changed(current_params if same_strategy else {})
        if CUSTOM_ERRORS:
            for error in CUSTOM_ERRORS:
                self._log(f"自定义策略文件加载失败: {error}", "warn")

    def _collect_request(self) -> RunRequest:
        meta = self._current_meta()
        if meta is None:
            raise ValueError("请选择策略")
        try:
            initial_cash = float(self.var_cash.get().replace(",", ""))
            commission_pct = float(self.var_commission.get())
            stamp_pct = float(self.var_stamp.get())
            slippage = float(self.var_slippage.get())
            position_pct = float(self.var_position.get())
            stop_loss = float(self.var_stop.get())
            take_profit = float(self.var_take.get())
            hold_days = int(float(self.var_hold.get()))
        except ValueError as exc:
            raise ValueError(f"交易参数格式错误: {exc}") from exc
        if initial_cash <= 0:
            raise ValueError("初始资金必须大于 0")
        if commission_pct < 0 or stamp_pct < 0 or slippage < 0:
            raise ValueError("佣金、印花税、滑点不能为负数")
        if not 0 < position_pct <= 100:
            raise ValueError("仓位应在 0~100 之间")
        if not 0 <= stop_loss < 100:
            raise ValueError("止损应为 0~100 之间的百分比")
        if take_profit < 0:
            raise ValueError("止盈不能为负数")
        if hold_days < 0:
            raise ValueError("最长持有天数不能为负数")
        start = pd.Timestamp(self.var_start.get().strip()).normalize()
        end = pd.Timestamp(self.var_end.get().strip()).normalize()
        if start >= end:
            raise ValueError("开始日期必须早于结束日期")
        params: dict[str, Any] = {}
        for spec in meta.params:
            var = self.param_vars.get(spec.key)
            if var is None:
                continue
            value = var.get()
            if spec.kind == "model":
                value = self.model_combo_maps.get(spec.key, {}).get(value, value)
            params[spec.key] = spec.normalize(value)
        config = BacktestConfig(
            initial_cash=initial_cash,
            commission_rate=commission_pct / 100.0,
            stamp_tax_rate=stamp_pct / 100.0,
            slippage_bps=slippage,
            position_pct=position_pct / 100.0,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            max_hold_days=hold_days,
            close_at_end=bool(self.var_close_end.get()),
        )
        return RunRequest(
            symbol=self.var_symbol.get().strip(),
            start=str(start.date()),
            end=str(end.date()),
            strategy=meta.slug,
            strategy_params=params,
            config=config,
            benchmark_symbol=self.var_benchmark.get().strip(),
            trade_adjust=self.var_adjust.get(),
            offline=bool(self.var_offline.get()),
        )

    def _start_run(self):
        if self.running:
            return
        try:
            request = self._collect_request()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)
            return
        self.running = True
        self.run_button.configure(state=tk.DISABLED, text="回测运行中...")
        self.export_button.configure(state=tk.DISABLED)
        self.progress.start(12)
        self.status_text.configure(text="正在加载数据并执行回测 ...")
        self.left_status.configure(text="正在连接 AKShare / 本地缓存 ...")
        self._log("", "info")
        self._log(
            f"开始回测 · {request.symbol} · {request.start} ~ {request.end} · "
            f"{self._current_meta().name}",
            "ok",
        )

        def work():
            try:
                output = execute(request, progress=lambda message: self.msg_queue.put(("log", message)))
                self.msg_queue.put(("done", output))
            except Exception as exc:
                self.msg_queue.put(("error", (str(exc), traceback.format_exc())))

        threading.Thread(target=work, daemon=True).start()

    def _poll_queue(self):
        while True:
            try:
                kind, payload = self.msg_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload), "info")
                self.left_status.configure(text=str(payload))
            elif kind == "done":
                self._on_run_done(payload)
            elif kind == "error":
                self._on_run_error(*payload)
        self._poll_after = self.after(100, self._poll_queue)

    def _on_run_done(self, output: RunOutput):
        self.running = False
        self.progress.stop()
        self.run_button.configure(state=tk.NORMAL, text="开始回测   Ctrl+Enter")
        self.output = output
        self.export_button.configure(state=tk.NORMAL)
        self.left_status.configure(text="回测完成")
        self.status_text.configure(text="回测完成")
        metrics = output.result.metrics
        total = metrics.get("total_return_pct")
        annual = metrics.get("annual_return_pct")
        dd = metrics.get("max_drawdown_pct")
        sharpe = metrics.get("sharpe")
        win = metrics.get("win_rate_pct")
        n = metrics.get("trade_count")
        bench = metrics.get("benchmark_return_pct")
        self.cards["return"].set(
            fmt_pct(total),
            f"基准 {fmt_pct(bench)}" if bench is not None else "无基准",
            self.settings.rise_color if (total or 0) >= 0 else self.settings.fall_color,
        )
        self.cards["annual"].set(
            fmt_pct(annual),
            "按 252 日年化",
            self.settings.rise_color if (annual or 0) >= 0 else self.settings.fall_color,
        )
        self.cards["drawdown"].set(
            fmt_pct(dd),
            "区间最大回撤",
            self.settings.fall_color if dd is not None and dd < 0 else TEXT,
        )
        self.cards["sharpe"].set(
            fmt_num(sharpe, 2),
            "日频 / 无风险利率 0",
            self.settings.rise_color if (sharpe or 0) >= 1 else TEXT,
        )
        self.cards["win"].set(fmt_pct(win, signed=False), "已平仓交易", TEXT)
        self.cards["trades"].set(str(n if n is not None else "--"), f"期末 {fmt_money(metrics.get('final_equity'))}", TEXT)
        self.summary_title.configure(text=f"{output.symbol} · {output.strategy_meta.name}")
        self.summary_sub.configure(
            text=(
                f"{metrics.get('start')} ~ {metrics.get('end')} · "
                f"{'离线示例' if self.var_offline.get() else 'AKShare'} · "
                f"初始资金 {fmt_money(output.result.config.initial_cash)} · "
                f"{'含模型 ' + output.model_meta.name if output.model_meta else '规则策略'}"
            )
        )
        latest = output.latest_signal
        if latest:
            if latest["entry"]:
                signal_text = f"最新信号 · {latest['date']} · 买入观察 · 评分 {latest['score']:.1f}"
                color = self.settings.buy_color
            elif latest["exit"]:
                signal_text = f"最新信号 · {latest['date']} · 离场观察 · 评分 {latest['score']:.1f}"
                color = self.settings.sell_color
            else:
                signal_text = f"最新信号 · {latest['date']} · 无动作 · 评分 {latest['score']:.1f}"
                color = MUTED
            self.signal_label.configure(text=signal_text, fg=color)
        else:
            self.signal_label.configure(text="", fg=MUTED)
        self.equity_chart.set_data(output)
        self.kline_chart.set_data(output)
        self._fill_trades(output)
        self._log(
            f"完成 · 收益 {fmt_pct(total)} · 年化 {fmt_pct(annual)} · 回撤 {fmt_pct(dd)} · "
            f"交易 {n} 笔 · 胜率 {fmt_pct(win, signed=False)}",
            "ok",
        )
        for warning in output.warnings:
            self._log(f"提示：{warning}", "warn")
        source_parts = [f"{key}={value}" for key, value in output.data_sources.items()]
        self.status_right.configure(text=" · ".join(source_parts))
        self.notebook.select(0)

    def _fill_trades(self, output: RunOutput):
        for item in self.trade_tree.get_children():
            self.trade_tree.delete(item)
        trades = output.result.trades
        if trades.empty:
            self.trade_summary.configure(text="暂无成交（可调整策略参数或时间区间）")
            return
        for idx, row in enumerate(trades.itertuples(index=False), start=1):
            pnl = float(row.pnl)
            tag = "win" if pnl >= 0 else "loss"
            self.trade_tree.insert(
                "",
                "end",
                values=(
                    idx,
                    pd.Timestamp(row.entry_date).strftime("%Y-%m-%d"),
                    pd.Timestamp(row.exit_date).strftime("%Y-%m-%d"),
                    int(row.holding_days),
                    fmt_num(row.entry_price, 3),
                    fmt_num(row.exit_price, 3),
                    int(row.shares),
                    f"{pnl:+,.2f}",
                    fmt_pct(row.return_pct),
                    row.entry_reason,
                    row.exit_reason,
                ),
                tags=(tag,),
            )
        wins = int((trades["pnl"] > 0).sum())
        self.trade_summary.configure(
            text=f"共 {len(trades)} 笔 · 盈利 {wins} 笔 · 亏损 {len(trades) - wins} 笔 · 累计净盈亏 {trades['pnl'].sum():+,.2f}"
        )

    def _on_run_error(self, message: str, details: str):
        self.running = False
        self.progress.stop()
        self.run_button.configure(state=tk.NORMAL, text="开始回测   Ctrl+Enter")
        self.left_status.configure(text="回测失败")
        self.status_text.configure(text="回测失败")
        self._log(f"回测失败：{message}", "error")
        if details:
            self._log(details, "dim")
        messagebox.showerror("回测失败", message, parent=self)
        self.notebook.select(3)

    def _export(self):
        if self.output is None:
            return
        directory = filedialog.askdirectory(
            parent=self,
            title="选择导出目录",
            initialdir=str(PROJECT_ROOT / "exports"),
        )
        if not directory:
            return
        try:
            report = export_output(self.output, directory)
            self._log(f"已导出回测报告: {report}", "ok")
            messagebox.showinfo("导出完成", f"结果已导出到：\n{directory}", parent=self)
        except Exception as exc:
            self._log(f"导出失败: {exc}", "error")
            messagebox.showerror("导出失败", str(exc), parent=self)

    def _open_doc(self, name: str):
        path = DOCS / name
        if not path.exists():
            messagebox.showwarning("文档不存在", f"未找到文档：{path}", parent=self)
            return
        webbrowser.open(path.as_uri())

    def _open_data_dir(self):
        webbrowser.open((PROJECT_ROOT / "data").as_uri())

    def _log(self, message: str, tag: str = "info"):
        if not message:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n", tag)
            self.log_text.configure(state="disabled")
            return
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _save_session(self):
        import json

        meta = self._current_meta()
        params = self._current_param_values()
        data = {
            "symbol": self.var_symbol.get(),
            "start": self.var_start.get(),
            "end": self.var_end.get(),
            "benchmark": self.var_benchmark.get(),
            "adjust": self.var_adjust.get(),
            "offline": bool(self.var_offline.get()),
            "strategy": meta.slug if meta else "",
            "strategy_params": params,
            "initial_cash": self.var_cash.get(),
            "commission_pct": self.var_commission.get(),
            "stamp_pct": self.var_stamp.get(),
            "slippage_bps": self.var_slippage.get(),
            "position_pct": self.var_position.get(),
            "stop_loss_pct": self.var_stop.get(),
            "take_profit_pct": self.var_take.get(),
            "max_hold_days": self.var_hold.get(),
            "close_at_end": bool(self.var_close_end.get()),
        }
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _on_close(self):
        try:
            self._save_session()
        except Exception:
            pass
        self.destroy()

    def destroy(self):
        try:
            self.after_cancel(self._poll_after)
        except Exception:
            pass
        super().destroy()


def main():
    app = StrategyStudio()
    app.mainloop()


if __name__ == "__main__":
    main()
