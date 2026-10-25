# 策略接口文档

> 接口版本 1.0 · 适用于股票策略工坊
> 目标：不修改回测引擎和 GUI，通过插件扩展自定义买卖规则。
> 内置示例：`dual_ma`、`breakout`、`sm701`、`example_rsi`，以及程序内条件编辑器生成的自定义策略。

---

## 1. 快速开始

在 `strategies/plugins/` 新建 `my_strategy.py`：

```python
from typing import Any

import pandas as pd

from core.indicators import rsi
from core.params import ParamSpec, StrategyMeta
from core.strategy_api import BaseStrategy, StrategyContext, empty_signals
from strategies.registry import register_strategy


@register_strategy
class MyStrategy(BaseStrategy):
    meta = StrategyMeta(
        slug="my_strategy",
        name="我的策略",
        description="示例：RSI 低于 35 买入，高于 60 卖出。",
        params=(
            ParamSpec("rsi_period", "RSI 周期", "int", 14, 2, 60, 1),
            ParamSpec("buy_level", "买入阈值", "float", 35.0, 1, 50, 1),
            ParamSpec("sell_level", "卖出阈值", "float", 60.0, 30, 95, 1),
        ),
    )

    def generate(self, bars: pd.DataFrame, params: dict[str, Any], context: StrategyContext) -> pd.DataFrame:
        dates = pd.DatetimeIndex(pd.to_datetime(bars["date"]).dt.normalize())
        out = empty_signals(dates)
        values = rsi(bars["close"].astype(float), int(params["rsi_period"]))
        out["entry"] = (values <= float(params["buy_level"])).fillna(False).to_numpy()
        out["exit"] = (values >= float(params["sell_level"])).fillna(False).to_numpy()
        out["score"] = (100.0 - values).fillna(50.0).clip(0, 100).to_numpy()
        out.loc[out["entry"], "entry_reason"] = "RSI 超卖"
        out.loc[out["exit"], "exit_reason"] = "RSI 回升"
        return out
```

保存后重启 GUI，策略下拉框会自动出现「我的策略」。参数表单也会根据 `ParamSpec` 自动生成。

---

## 2. 程序内自定义策略编辑器

不需要写代码：点击主界面策略区的「打开自定义策略编辑器」。

- 42 个技术指标，分价格、趋势、动量、波动、量能、通道六组。
- 入场条件和离场条件分别支持 AND / OR。
- 每个条件由「左侧指标 + 操作符 + 右侧数值/指标」组成。
- 支持 `>`、`>=`、`<`、`<=`、上穿/金叉、下穿/死叉、介于区间、较前值上升/下降。
- 支持自定义指标参数，例如 MA 周期、MACD 快慢线、KDJ N/M1/M2、布林带宽倍数等。
- 编辑器可新建、载入、另存为、删除，保存后立即出现在主界面策略下拉框。
- 保存位置：`strategies/custom/*.json`，可直接复制备份或纳入版本管理。
- 右上角「指标库」按钮展示全部指标、参数和说明。
- 「示例：放量突破 20 日高」是内置的 JSON 示例，可直接编辑。

### 2.1 条件 JSON 结构

```json
{
  "left": {"indicator": "ma", "params": {"period": 5}},
  "op": "cross_above",
  "right": {"type": "indicator", "indicator": "ma", "params": {"period": 20}}
}
```

- 左侧/右侧指标来自 `core/indicator_catalog.py`。
- 右侧 `type` 可以是 `value` 或 `indicator`。
- 数值型：`{"type": "value", "value": 1.5}`。
- 区间：`{"type": "value", "value": 30, "value2": 70}`，配合 `"op": "between"`。
- 上升/下降操作符不需要右侧比较对象。

策略文件结构：

```json
{
  "slug": "custom_20261017_120000",
  "name": "我的策略",
  "description": "示例",
  "entry_logic": "all",
  "exit_logic": "any",
  "entry_conditions": [],
  "exit_conditions": [],
  "created_at": "2026-10-17 12:00:00",
  "updated_at": "2026-10-17 12:00:00"
}
```

自定义策略由 `strategies/custom.py::CustomRuleStrategy` 解释执行，信号格式与代码插件一致。
条件编辑器只使用技术指标；如果要使用 SM701 或其他模型信号，请编写插件策略或扩展编辑器。

---

## 3. 目录与自动发现

```text
strategies/
├── registry.py             # 注册表与插件加载器（勿改）
├── builtin.py              # 内置策略
├── custom.py               # 条件策略引擎
├── custom_store.py         # 自定义策略 JSON 读写
├── custom/
│   ├── custom_example_breakout.json
│   └── your_custom_strategy.json
└── plugins/
    ├── example_rsi.py      # 完整示例插件
    └── your_strategy.py    # 你的策略（自动加载）
```

- 每个插件文件一个策略类，文件名不要以下划线开头。
- 使用 `@register_strategy` 注册，`slug` 必须唯一。
- 自定义 JSON 每次读取注册表时自动发现，无需重启。
- 插件/JSON 加载失败只记录日志，不影响其他策略和主程序。
- GUI 的策略列表、参数表单都由 `StrategyMeta` 和 `ParamSpec` 自动生成。

---

## 4. 生命周期

```text
选择策略 → 读取 StrategyMeta → 自动渲染参数表单
        → 用户点击回测
        → 加载行情（含 420 天预热）
        → 如策略 requires_model，加载模型与模型行情
        → strategy.generate(bars, params, context)
        → 回测引擎按 T+1 / 整手 / 费用 / 风控执行
```

`generate()` 只负责生成信号，不负责下单、扣费、仓位和 T+1。这样策略可以保持简单，
回测和未来实盘执行共用同一套执行规则。

---

## 5. 数据与上下文

### 5.1 `bars`

按日期升序的标准 DataFrame：

| 列 | 说明 |
|---|---|
| `date` | 交易日 |
| `open` / `high` / `low` / `close` | 行情价格，默认前复权 |
| `volume` | 成交量（手） |
| `amount` | 成交额（元） |

`bars` 包含回测开始日前约 420 个自然日的数据，用于指标预热。不要假设第一行就是回测起始日。

### 5.2 `StrategyContext`

```python
@dataclass
class StrategyContext:
    benchmark: pd.DataFrame | None      # 基准指数 OHLCV
    model_bars: pd.DataFrame | None     # 模型所需复权口径的行情
    model: BaseModel | None             # 已加载的模型实例
    model_context: ModelContext         # 传给模型的市场上下文
    extras: dict                        # 预留
```

如果 `StrategyMeta.requires_model=True`，运行器会自动：
1. 从策略参数 `model` 读取模型 slug；
2. 加载对应模型；
3. 若模型 `meta.adjust` 与交易复权不同，再加载一份对应口径的行情；
4. 传入 `model`、`model_bars` 和 `model_context`。

---

## 6. 输出信号格式

`generate()` 返回以日期为索引的 DataFrame：

| 列 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `entry` | bool | 是 | T 日收盘出现入场意图 |
| `exit` | bool | 是 | T 日收盘出现离场意图 |
| `score` | float | 否 | 0~100，用于展示和排序 |
| `entry_reason` | str | 否 | 入场原因，写入交易明细 |
| `exit_reason` | str | 否 | 离场原因，写入交易明细 |

可以使用 `core.strategy_api.empty_signals(index)` 创建标准空表，再用 `.loc` 填充原因。
如果使用 `RangeIndex` 的指标序列，请用 `.to_numpy()` 赋值或先转换到日期索引，避免索引错位。

### 信号语义

- 信号在 T 日**收盘后**生成。
- 引擎在 T+1 **开盘**成交（若 T 日之后还有交易日）。
- 不在回测区间外的信号上开仓。
- 同一时刻只做多、最多一只股票、单笔全平；当前版本不做加仓和做空。
- 引擎还会根据界面上的止损、止盈、最长持有天数生成额外离场信号。
- 期末默认按最后收盘价清仓；如果最后一天才买入，受 T+1 限制不会当日卖出。

---

## 7. 参数定义 `ParamSpec`

```python
ParamSpec(
    key="fast_ma",          # 写入 params 字典的键
    label="快线周期",        # GUI 标签
    kind="int",             # int / float / bool / choice / text / model
    default=10,
    minimum=2,              # 仅用于文档/校验提示
    maximum=120,
    step=1,
    choices=(),             # kind=choice 时使用
    help="提示文字",
)
```

- 参数值在进入策略前会按类型归一化。
- `kind="model"` 会在 GUI 中自动列出已注册模型，并把选中的 slug 写入参数。
- 布尔值在 GUI 中显示为复选框。
- 校验失败时抛出 `StrategyError` 或 `ValueError`，错误会在 GUI 日志中显示。

---

## 8. 使用模型的策略

参考 `Sm701Strategy`：

```python
@register_strategy
class Sm701Strategy(BaseStrategy):
    meta = StrategyMeta(
        slug="sm701",
        name="SM701 概率模型",
        params=(
            ParamSpec("model", "预测模型", "model", "sm701"),
            ParamSpec("buy_threshold", "h1 买入概率阈值", "float", 0.55, 0.50, 0.95, 0.01),
            ParamSpec("sell_threshold", "h1 卖出概率阈值", "float", 0.45, 0.05, 0.70, 0.01),
            ParamSpec("trend_threshold", "h5/h10 趋势确认阈值", "float", 0.50, 0.05, 0.95, 0.01),
        ),
        requires_model=True,
        default_model="sm701",
    )

    def generate(self, bars, params, context):
        pred = context.model.predict(context.model_bars, {}, context.model_context)
        prob = pred.set_index("date")["score"]
        p5 = pred.set_index("date")["score_h5"]
        p10 = pred.set_index("date")["score_h10"]
        ...
```

要点：
- 预测行与交易行按日期对齐；缺失时最多前向填充 5 日。
- 模型和交易行情可以是不同复权口径，但日期要来自同一交易日历。
- 阈值、市场过滤、仓位和退出规则属于策略层，不要写进模型。
- SM701 三周期共振：h1 达标且 h5/h10 均高于趋势阈值才入场；h1 低于卖出阈值或 h5/h10 任一走弱离场。

---

## 9. 内置策略

| slug | 名称 | 逻辑 |
|---|---|---|
| `dual_ma` | 双均线趋势 | 快线上穿慢线且收盘在慢线上方入场；死叉或跌破慢线离场 |
| `breakout` | 通道突破 | 收盘突破前 N 日高点入场；跌破前 M 日低点离场；可放量过滤 |
| `sm701` | SM701 概率模型 | h1 分数达到买入阈值且 h5/h10 共振向上入场；h1 低于卖出阈值或 h5/h10 走弱离场；可加大盘 MA20 过滤 |
| `example_rsi` | 示例插件 · RSI 反转 | RSI 超卖入场、回到中枢离场；可只做 MA20 上方 |
| `custom_*` | 自定义条件策略 | 由程序内编辑器生成，指标/比较/AND-OR 自由组合 |

---

## 10. 回测执行规则

- A 股 T+1：当日买入不可当日卖出。
- 100 股整手；买入按可用资金和仓位比例计算可买手数。
- 买入价：T+1 开盘 × (1 + 滑点)；卖出价：T+1 开盘 × (1 - 滑点)。
- 手续费：佣金（默认万 2.5，最低 5 元）+ 卖出印花税 0.05% + 过户费 0.001%。
- 止损/止盈/最长持有：按收盘价触发，下一交易日开盘执行。
- 每一笔交易记录净盈亏、收益率、费用和出入场原因。
- 期末默认清仓；可选保留持仓按市值结算。

这些规则位于 `core/backtest.py`，修改它们会影响所有策略。

---

## 11. 检查清单

- [ ] `slug` 唯一，`meta` 为 `StrategyMeta`。
- [ ] 输出索引是日期，或包含 `date` 列。
- [ ] `entry` / `exit` 为布尔值，无 NaN。
- [ ] 指标基于 T 日及以前的数据，无未来函数。
- [ ] 使用 `.to_numpy()` 或日期索引避免 RangeIndex 对齐问题。
- [ ] 回测区间前有足够预热数据；需要 60 日均线就至少多取 60 个交易日。
- [ ] 资金不足、无信号时能正常返回。
- [ ] 在样本外区间验证，并检查交易次数和回撤是否合理。

---

## 12. 调试建议

- 先在命令行运行：

  ```bash
  .venv/bin/python cli.py --symbol sz000001 --strategy my_strategy --offline --start 2023-01-01
  ```

- 在 GUI 的「运行日志」中查看数据源、缓存和模型加载信息。
- 如果信号全为空，检查日期索引是否对齐、窗口长度是否足够。
- 如果出现 `Cannot mask with non-boolean array containing NA / NaN`，通常是把
  RangeIndex 的布尔序列赋给了日期索引的 DataFrame，使用 `.to_numpy()` 即可。
