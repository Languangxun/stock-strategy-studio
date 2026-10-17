将 CSV / SQLite 行情文件拖入程序导入窗口，或直接放入本文件夹后点击“扫描导入文件夹”。

CSV 字段要求：
- 日期 / date
- 开盘 / open、最高 / high、最低 / low、收盘 / close
- 成交量 / volume（可选）、成交额 / amount（可选）
- 股票代码 / symbol（可选；没有时从文件名识别，如 sz000001.csv）

SQLite：
- 支持本项目 bars 表，也支持常见的 daily_bars(code,date,open,high,low,close,vol) 表。
- 导入复权口径在导入窗口选择；文件名包含 qfq/hfq 时会自动识别。

导入后写入 data/cache.db，单股回测与全市场回测都会优先读取本地数据。

Rust 回测加速已启用：NumPy 指标 + Rust 撮合引擎。
