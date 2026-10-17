# 本地行情导入

> 目标：把 CSV / SQLite 行情快速写入 `data/cache.db`，供单股与全市场回测优先读取。

## 1. 入口

- 主界面顶部「导入数据」。
- 导入窗口内直接拖入文件/文件夹，或使用选择文件/文件夹、扫描导入文件夹、打开导入文件夹。
- 默认导入文件夹：`data/import/`。
- 支持多文件、多文件夹、递归扫描。

## 2. 复权口径

- 自动识别：文件名含 `qfq` / `前复权` → qfq，`hfq` / `后复权` → hfq，`raw` / `不复权` → raw。
- 文件名没有标记时：CSV 默认 qfq，SQLite 默认 hfq；可在导入窗口手动指定。
- 单股回测默认交易复权 qfq；SM701 模型使用 hfq。两种口径可以分别导入。

## 3. CSV 格式

必需列（中英文均可）：

| 含义 | 可用列名 |
|---|---|
| 日期 | date / datetime / trade_date / 日期 / 交易日期 |
| 开盘 | open / 开盘 / 开盘价 |
| 最高 | high / 最高 / 最高价 |
| 最低 | low / 最低 / 最低价 |
| 收盘 | close / 收盘 / 收盘价 |
| 股票代码 | symbol / code / ticker / 股票代码 / 代码（可选） |
| 成交量 | volume / vol / 成交量（可选） |
| 成交额 | amount / turnover / 成交额（可选） |

- 单股票文件没有代码列时，从文件名识别，例如 `sz000001_qfq.csv`、`600519.csv`。
- 日期支持 `YYYY-MM-DD` 与 `YYYYMMDD`。
- 编码自动尝试 UTF-8 / GB18030 / GBK。

## 4. SQLite 格式

支持以下表结构：

- 本项目 `bars(symbol, adjust, date, open, high, low, close, volume, amount, ...)`。
- 常见本地库 `daily_bars(code, date, open, high, low, close, vol)`。
- 其他具有 `symbol/code`、`date`、`open/high/low/close` 的行情表可通过扩展识别。

导入 SQLite 时优先使用 SQL ATTACH + INSERT 批量复制，避免把整库读入内存。
源码：`core/importer.py`。

## 5. 本地优先规则

- `DataFeed.load_stock()` 先查 `data/cache.db`；缓存覆盖请求区间时直接返回，不发网络请求。
- 全市场回测只读本地数据库，不访问 AKShare。
- 未覆盖的区间仍可按原有 AKShare 多通道补拉（单股模式）。
- 本地股票池筛选 A 股代码：`sh60*`、`sh68*`、`sz00*`、`sz30*`、`bj*`，自动排除指数/ETF。

## 6. 常见问题

- 导入后全市场仍为空：检查导入复权口径是否与窗口选择一致；检查最少 K 线设置。
- 日期解析失败：确认列名和日期格式，或先用 `pandas.to_datetime` 清洗。
- 同名数据重复导入：使用 UPSERT，以 `(symbol, adjust, date)` 为主键覆盖。
- 导入自己的缓存库：不能选择当前正在使用的 `data/cache.db`。
