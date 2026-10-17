# 模型接口文档

> 接口版本 1.0 · 适用于股票策略工坊
> 目标：让任意“次日上涨概率 / 打分模型”以插件形式接入，不修改回测引擎和 GUI。
> 内置示例：`sm701`（LightGBM，44 维量价 + 市场环境特征）。

---

## 1. 快速开始

在 `models/plugins/` 新建 `my_model.py`：

```python
from pathlib import Path

import numpy as np
import pandas as pd

from core.model_api import BaseModel, ModelContext
from core.params import ModelMeta
from models.registry import register_model


@register_model
class MyModel(BaseModel):
    meta = ModelMeta(
        slug="my_model",
        name="我的预测模型",
        version="1.0",
        description="示例：用 20 日动量生成 0~1 分数。",
        adjust="qfq",
    )

    def predict(self, bars: pd.DataFrame, params: dict, context: ModelContext) -> pd.DataFrame:
        close = bars["close"].astype(float)
        score = (1.0 / (1.0 + np.exp(-close.pct_change(20) * 20))).fillna(0.5)
        return pd.DataFrame({
            "date": pd.to_datetime(bars["date"]).dt.normalize(),
            "score": score,
            "reason": [f"动量分 {v:.3f}" for v in score],
        })
```

重启 GUI 后，只要某个策略把参数 `model` 指向 `my_model`，即可在界面中选择该模型。
当前内置的「SM701 概率模型」会读取策略参数中的 `model` 字段。

---

## 2. 目录与自动发现

```text
models/
├── registry.py              # 注册表与插件加载器（勿改）
├── sm701_model.py           # 内置模型示例
├── sm701_features.py        # SM701 的 44 维特征实现
├── artifacts/
│   └── sm701/
│       └── lgbm_model.txt   # 模型二进制文件
└── plugins/
    └── your_model.py        # 你的模型插件（自动加载）
```

- 每个 `models/plugins/*.py` 一个模型类，不能以下划线开头。
- 使用 `@register_model` 注册；`ModelMeta.slug` 必须唯一。
- 插件加载失败只会记录日志，不影响主程序；错误会显示在 GUI 运行日志中。
- 模型文件放在 `models/artifacts/<slug>/` 下，`create_model(slug)` 会自动把该目录作为
  `artifact_dir` 传给模型构造函数。

---

## 3. 数据类与元信息

### 3.1 `ModelMeta`

```python
ModelMeta(
    slug="sm701",                 # 唯一标识，用于策略参数与 GUI 选择
    name="SM701 LightGBM",        # 展示名称
    version="1.0",                # 模型版本
    description="...",           # 一句说明
    adjust="hfq",                 # 需要的行情复权口径：qfq / hfq / 空字符串
    params=(),                    # 可选：模型自己的参数定义（ParamSpec）
)
```

`adjust` 很重要：
- 如果模型训练时使用的是后复权，必须填 `"hfq"`。运行器会额外加载一份后复权行情，
  但交易和图表仍使用界面选择的复权口径。
- 如果模型对复权不敏感，填 `"qfq"` 可复用交易行情，减少一次网络请求。
- `sm701` 训练使用后复权数据，因此示例中为 `"hfq"`。

### 3.2 输入：`bars`

`predict()` 收到的 `bars` 是标准 DataFrame，按日期升序：

| 列 | 类型 | 说明 |
|---|---|---|
| `date` | datetime64 | 交易日（已归一化到 00:00） |
| `open` | float | 开盘价 |
| `high` | float | 最高价 |
| `low` | float | 最低价 |
| `close` | float | 收盘价 |
| `volume` | float | 成交量（手） |
| `amount` | float | 成交额（元，可能为 0） |

附加属性：
- `bars.attrs["source"]`：`eastmoney` / `tencent` / `sina` / `sample` / `cache`
- `bars.attrs["warning"]`：数据降级提示（可能为空）

### 3.3 输入：`ModelContext`

```python
@dataclass
class ModelContext:
    benchmark: pd.DataFrame | None       # 基准指数：date/open/high/low/close/volume
    universe: dict[str, pd.DataFrame]    # 可选：同批股票行情，供横截面特征使用
    extras: dict                         # 预留扩展字段
```

当前 GUI 是单标的模式，`universe` 通常只包含当前股票。SM701 的横截面排名特征
在单标的场景下退化为中性值 0.5；市场环境特征 `mkt1/mkt5/mkt20/breadth5` 由基准指数近似。
如果你的模型需要完整截面，可在未来的批量回测扩展中复用 `universe`，无需修改接口。

### 3.4 输出

必须返回 DataFrame，列如下：

| 列 | 必需 | 说明 |
|---|---|---|
| `date` | 是 | 交易日，必须与输入日期对应 |
| `score` | 是 | 0~1 的上涨概率/分数；策略层负责阈值 |
| `reason` | 否 | 可读的原因，写入交易明细和日志 |

要求：
- **严格因果**：第 T 行的分数只能使用 T 日收盘及以前的数据，不能出现未来数据。
- 可以一次返回整段历史的分数；引擎会按日期与交易行情对齐。
- 日期缺失时策略层会前向填充最多 5 个交易日；仍缺失则不产生信号。
- 模型应输出 0~1 分数；内置 SM701 策略会对分数做裁切，NaN 建议在模型内部处理。
- 若数据不足（例如 SM701 需要至少 65 根 K 线），返回空 DataFrame 或空列即可。

---

## 4. 生命周期与加载

```python
from models.registry import create_model

model = create_model("sm701")      # 自动传入 models/artifacts/sm701
model.available()                  # 模型文件是否可用
model.meta                         # ModelMeta
pred = model.predict(bars, {}, context)
```

- `BaseModel.__init__(artifact_dir)` 可以加载模型文件；不要在导入时做重计算。
- 建议模型加载失败时抛出 `ModelError`，界面会显示可读错误。
- 运行器按需加载：只有选中需要模型的策略时才会导入 LightGBM / 加载模型文件。
- 模型文件不要提交到公共仓库；本项目的 `sm701` 已随项目保留，供本地示例使用。

---

## 5. SM701 示例

### 5.1 组成

- 模型：LightGBM `binary` 目标，预测“次日开盘买入、次日收盘卖出”的上涨概率。
- 特征：44 维，包含动量、均线、波动、RSI、MACD、KDJ、布林、ATR、量比、
  市场收益、市场宽度和横截面排名。
- 模型文件：`models/artifacts/sm701/lgbm_model.txt`。
- 适配器：`models/sm701_model.py`；特征实现：`models/sm701_features.py`。
- 策略层：`strategies/builtin.py` 中的 `Sm701Strategy`，默认买入阈值 0.55、卖出阈值 0.45。
- 模型使用后复权行情；交易模拟使用界面选择的复权口径（默认前复权）。

### 5.2 最小适配器

```python
@register_model
class Sm701Model(BaseModel):
    meta = ModelMeta(slug="sm701", name="SM701 LightGBM", adjust="hfq")

    def __init__(self, artifact_dir: Path | None = None):
        root = artifact_dir or Path(__file__).parent / "artifacts" / "sm701"
        super().__init__(root)
        import lightgbm as lgb
        self.booster = lgb.Booster(model_file=str(root / "lgbm_model.txt"))

    def predict(self, bars, params, context):
        features = compute_features(bars, context.benchmark)
        matrix = features[self.booster.feature_name()].astype("float32")
        scores = self.booster.predict(matrix).clip(0, 1)
        return pd.DataFrame({
            "date": features["date"],
            "score": scores,
            "reason": [f"SM701 P(次日上涨)={p:.3f}" for p in scores],
        })
```

### 5.3 特征与口径注意事项

- 训练使用后复权价格，`vol20` / `vol60` 等绝对波动特征对价格尺度敏感，
  因此示例模型声明 `adjust="hfq"`。
- 单标的回测时，8 个横截面排名特征以 0.5 中性值填充。
- `mkt1/mkt5/mkt20` 用所选基准指数计算；`breadth5` 用基准指数近 5 日上涨占比近似。
  这与训练时的全市场口径不完全一致，结果应视为演示而非实盘承诺。
- 模型分数只是信号源，最终交易还受阈值、手续费、T+1、整手、止损止盈和资金限制影响。

---

## 6. 自定义模型检查清单

- [ ] `slug` 唯一，`meta` 为 `ModelMeta`。
- [ ] `adjust` 与训练数据一致；SM701 使用 `hfq`。
- [ ] `predict` 输出 `date` 和 0~1 的 `score`。
- [ ] 特征只使用当日及以前的数据。
- [ ] 数据不足时安全返回空结果。
- [ ] 模型文件放 `models/artifacts/<slug>/`，缺失时抛出 `ModelError`。
- [ ] 在 GUI 中通过「SM701 概率模型」的模型下拉选择，或新增一个使用该模型的策略。
- [ ] 用历史样本外区间验证，不只看训练集表现。
- [ ] 不把包含未来信息的基本面/公告字段错配到信号日。

---

## 7. 常见问题

**Q：模型加载后 GUI 没有模型选项？**
A：确认文件在 `models/plugins/`，类使用了 `@register_model`，并且重启了 GUI。
运行日志会显示插件加载错误。

**Q：SM701 分数一直偏低/偏高？**
A：检查是否使用后复权行情、股票是否有足够历史数据、基准是否成功加载。
单标的横截面退化和市场环境近似也会影响分数。

**Q：策略回测没有交易？**
A：可能分数没有达到阈值、数据不足、行情区间太短，或资金不足一手。
查看运行日志中的提示，并可下调买入阈值做验证。

**Q：可以接大语言模型吗？**
A：可以。`BaseModel.predict()` 的输入是行情 DataFrame，输出是每日 0~1 分数。
把 LLM 的结论映射成数值分数并缓存，即可作为模型接入；建议不要在回测中逐日发起
网络请求，且必须保证没有未来信息。
