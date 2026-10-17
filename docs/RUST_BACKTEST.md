# Rust 回测加速

> 单股和全市场回测优先使用 Rust 撮合引擎；不可用时自动回退 Python。

## 1. 架构

```text
Python 指标/信号/策略/数据
        │
        ▼
core/rust_engine.py (ctypes)
        │
        ▼
core/native/libbtcore.so  ←  rust/backtest_core.rs
```

- Python 负责：行情、指标、策略信号、模型预测、基准、指标汇总。
- Rust 负责：逐日撮合、T+1、整手、资金、佣金、印花税、过户费、滑点、止损/止盈/最长持有、资金曲线。
- Rust 返回成交记录索引、退出原因码、资金数组和期末持仓。

## 2. 构建

```bash
cd ~/桌面/stock_strategy_studio
./rust/build.sh
```

命令：

```bash
rustc --edition=2021 -O -C target-cpu=native --crate-type cdylib \
  rust/backtest_core.rs -o core/native/libbtcore.so
```

依赖：Rust 1.70+，Linux x86_64。预编译库已随项目保留。

## 3. 使用

- 自动：单股 `core/runner.py` 与全市场 `core/market.py` 使用 `run_backtest_auto` / `run_backtest_native`。
- 全市场窗口有「Rust 加速」开关。
- 查看状态：主界面运行日志 / 全市场窗口底部引擎标识。

## 4. 回退策略

- `libbtcore.so` 不存在、无法加载或执行失败 → 自动使用 Python `run_backtest`。
- 全市场某只股票 Rust 执行异常 → 该股票回退 Python，并写入警告。
- 功能与统计口径保持一致；测试 `tests/test_native.py` 对逐笔成交和关键指标做一致性校验。

## 5. 性能

- 回测撮合循环从 Python 移到 Rust，单股测试约 4~5 倍加速；
- 全市场主要耗时仍在指标计算和数据库 I/O，Rust 降低撮合与资金曲线成本；
- 建议使用 SSD 和本地导入的全市场行情。
