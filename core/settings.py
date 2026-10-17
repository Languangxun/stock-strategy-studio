from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.indicator_catalog import get_indicator


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_FILE = PROJECT_ROOT / "config" / "settings.json"
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
CURATED_DEFAULTS = {
    "ma": {"period": 20},
    "ema": {"period": 20},
    "rsi": {"period": 14},
    "macd_dif": {"fast": 12, "slow": 26, "signal": 9},
    "macd_dea": {"fast": 12, "slow": 26, "signal": 9},
    "macd_hist": {"fast": 12, "slow": 26, "signal": 9},
    "kdj_k": {"n": 9, "m1": 3, "m2": 3},
    "kdj_d": {"n": 9, "m1": 3, "m2": 3},
    "kdj_j": {"n": 9, "m1": 3, "m2": 3},
    "boll_upper": {"period": 20, "width": 2.0},
    "boll_mid": {"period": 20, "width": 2.0},
    "boll_lower": {"period": 20, "width": 2.0},
    "boll_width": {"period": 20, "width": 2.0},
    "atr": {"period": 14},
    "atr_pct": {"period": 14},
    "volume_ratio": {"period": 5},
    "volume_ma": {"period": 5},
    "bias": {"period": 6},
}


def default_indicator_defaults() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in CURATED_DEFAULTS.items()}


def is_hex_color(value: str) -> bool:
    return bool(COLOR_RE.match(str(value or "")))


def _color(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if is_hex_color(text) else fallback


def contrast_text(color: str) -> str:
    if not is_hex_color(color):
        return "#ffffff"
    value = color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#000000" if luminance > 0.62 else "#ffffff"


def blend_hex(foreground: str, background: str, alpha: float) -> str:
    if not is_hex_color(foreground):
        foreground = "#ffffff"
    if not is_hex_color(background):
        background = "#000000"
    alpha = max(0.0, min(float(alpha), 1.0))
    fg = foreground.lstrip("#")
    bg = background.lstrip("#")
    parts = []
    for idx in (0, 2, 4):
        f = int(fg[idx:idx + 2], 16)
        b = int(bg[idx:idx + 2], 16)
        parts.append(int(round(f * alpha + b * (1.0 - alpha))))
    return "#" + "".join(f"{value:02x}" for value in parts)


@dataclass
class UserSettings:
    schema_version: int = 1
    rise_color: str = "#ff4d4d"
    fall_color: str = "#00ff9c"
    buy_color: str = "#ff4d4d"
    sell_color: str = "#00ff9c"
    strategy_color: str = "#00d4ff"
    benchmark_color: str = "#ffd400"
    drawdown_color: str = "#00ff9c"
    ma_type: str = "MA"
    ma_periods: list[int] = field(default_factory=lambda: [5, 10, 20])
    ma_colors: list[str] = field(default_factory=lambda: ["#00d4ff", "#ffd400", "#ff66cc"])
    indicator_defaults: dict[str, dict[str, Any]] = field(default_factory=default_indicator_defaults)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "colors": {
                "rise": self.rise_color,
                "fall": self.fall_color,
                "buy": self.buy_color,
                "sell": self.sell_color,
                "strategy": self.strategy_color,
                "benchmark": self.benchmark_color,
                "drawdown": self.drawdown_color,
                "ma": list(self.ma_colors),
            },
            "chart": {
                "ma_type": self.ma_type,
                "ma_periods": list(self.ma_periods),
                "ma_colors": list(self.ma_colors),
            },
            "indicator_defaults": self.indicator_defaults,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "UserSettings":
        defaults = cls()
        colors = raw.get("colors") or {}
        chart = raw.get("chart") or {}
        ma_type = str(chart.get("ma_type", defaults.ma_type)).upper()
        if ma_type not in ("MA", "EMA"):
            ma_type = defaults.ma_type
        periods_raw = chart.get("ma_periods") or defaults.ma_periods
        periods: list[int] = []
        for index in range(3):
            try:
                period = int(periods_raw[index])
            except (IndexError, TypeError, ValueError):
                period = defaults.ma_periods[index]
            periods.append(max(1, min(period, 500)))
        colors_raw = chart.get("ma_colors") or colors.get("ma") or defaults.ma_colors
        ma_colors = []
        for index in range(3):
            fallback = defaults.ma_colors[index]
            try:
                value = colors_raw[index]
            except (IndexError, TypeError):
                value = fallback
            ma_colors.append(_color(value, fallback))
        indicator_defaults = default_indicator_defaults()
        incoming = raw.get("indicator_defaults") or {}
        for key, values in incoming.items():
            spec = get_indicator(key)
            if spec is None or not isinstance(values, dict):
                continue
            target = indicator_defaults.setdefault(key, {})
            for param in spec.params:
                if param.key in values:
                    try:
                        target[param.key] = param.normalize(values[param.key])
                    except (TypeError, ValueError):
                        pass
        return cls(
            schema_version=int(raw.get("schema_version", 1)),
            rise_color=_color(colors.get("rise"), defaults.rise_color),
            fall_color=_color(colors.get("fall"), defaults.fall_color),
            buy_color=_color(colors.get("buy"), defaults.buy_color),
            sell_color=_color(colors.get("sell"), defaults.sell_color),
            strategy_color=_color(colors.get("strategy"), defaults.strategy_color),
            benchmark_color=_color(colors.get("benchmark"), defaults.benchmark_color),
            drawdown_color=_color(colors.get("drawdown"), defaults.drawdown_color),
            ma_type=ma_type,
            ma_periods=periods,
            ma_colors=ma_colors,
            indicator_defaults=indicator_defaults,
        )


def load_settings(path: str | Path | None = None) -> UserSettings:
    target = Path(path) if path else SETTINGS_FILE
    if not target.exists():
        return UserSettings()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return UserSettings.from_dict(raw)
    except Exception:
        pass
    return UserSettings()


def save_settings(settings: UserSettings, path: str | Path | None = None) -> Path:
    target = Path(path) if path else SETTINGS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return target
