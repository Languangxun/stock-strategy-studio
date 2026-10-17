# SM701 模型说明

- 文件：`lgbm_model.txt`
- 类型：LightGBM binary classifier，预测“次日开盘买入、次日收盘卖出”的上涨概率。
- 特征：44 维，实现见 `models/sm701_features.py`。
- 训练使用的行情：A 股后复权日线；模型适配器声明 `adjust="hfq"`。
- 打分输出：0~1。
- 默认策略阈值：买入 0.55，卖出 0.45（`strategies/builtin.py::Sm701Strategy`）。

## 特征分组

- 动量：ret1 / ret5 / ret10 / ret20 / ret60
- 均线：ma5r / ma10r / ma20r / ma60r / ma5_20 / ma20_60
- 波动：vol20 / vol60 / volr / atr_n
- 超买超卖：rsi6 / rsi14 / kdj_k / kdj_d / kdj_j
- 趋势：macd_dif / macd_dea / macd_hist / bollpos
- 位置与形态：hilo20 / cpos / amp / gap
- 量能：vr5 / vr20 / logvol / updays20
- 市场环境：mkt1 / mkt5 / mkt20 / breadth5
- 横截面排名：r_ret5 / r_ret20 / r_ret60 / r_ma20r / r_rsi14 / r_vr20 / r_vol20 / r_logvol

## 使用限制

- 单标的回测时，8 个横截面排名特征使用中性值 0.5。
- `mkt1/mkt5/mkt20/breadth5` 由基准指数近似，训练时是全市场口径。
- 特征复权口径必须为后复权；交易可以使用前复权。
- 该模型是研究示例，不代表未来收益或投资建议。

来源：本机原有 `sm701` / `ai-quant` 量化项目资产。
