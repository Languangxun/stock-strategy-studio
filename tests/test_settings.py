from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.settings import UserSettings, blend_hex, contrast_text, is_hex_color, load_settings, save_settings


class SettingsTest(unittest.TestCase):
    def test_defaults_are_red_up_green_down(self):
        settings = UserSettings()
        self.assertEqual(settings.rise_color.lower(), "#ff4d4d")
        self.assertEqual(settings.fall_color.lower(), "#00ff9c")
        self.assertEqual(settings.buy_color.lower(), "#ff4d4d")
        self.assertEqual(settings.sell_color.lower(), "#00ff9c")
        self.assertEqual(settings.ma_type, "MA")
        self.assertEqual(settings.ma_periods, [5, 10, 20])

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            settings = UserSettings(
                rise_color="#ff0000",
                fall_color="#00aa00",
                strategy_color="#123456",
                ma_type="EMA",
                ma_periods=[8, 21, 55],
                ma_colors=["#111111", "#222222", "#333333"],
            )
            settings.indicator_defaults["rsi"]["period"] = 21
            save_settings(settings, path)
            loaded = load_settings(path)
            self.assertEqual(loaded.rise_color, "#ff0000")
            self.assertEqual(loaded.fall_color, "#00aa00")
            self.assertEqual(loaded.ma_type, "EMA")
            self.assertEqual(loaded.ma_periods, [8, 21, 55])
            self.assertEqual(loaded.indicator_defaults["rsi"]["period"], 21)

    def test_invalid_values_fallback(self):
        settings = UserSettings.from_dict(
            {
                "colors": {"rise": "red", "fall": "#zzzzzz"},
                "chart": {"ma_type": "SMMA", "ma_periods": [0, "x", 9999], "ma_colors": ["bad"]},
                "indicator_defaults": {"rsi": {"period": "bad"}, "not_exists": {"x": 1}},
            }
        )
        self.assertEqual(settings.rise_color, "#ff4d4d")
        self.assertEqual(settings.fall_color, "#00ff9c")
        self.assertEqual(settings.ma_type, "MA")
        self.assertEqual(settings.ma_periods[0], 1)
        self.assertEqual(settings.ma_periods[1], 10)
        self.assertEqual(settings.ma_periods[2], 500)
        self.assertEqual(settings.indicator_defaults["rsi"]["period"], 14)

    def test_colors_helpers(self):
        self.assertTrue(is_hex_color("#00D4FF"))
        self.assertFalse(is_hex_color("blue"))
        self.assertEqual(contrast_text("#ffffff"), "#000000")
        self.assertEqual(contrast_text("#000000"), "#ffffff")
        blended = blend_hex("#ff0000", "#000000", 0.5)
        self.assertEqual(blended, "#800000")


if __name__ == "__main__":
    unittest.main()
