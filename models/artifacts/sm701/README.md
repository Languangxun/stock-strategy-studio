# SM701 模型说明

- 文件：`lgbm_model.txt`（h1）、`lgbm_model_h5.txt`（h5）、`lgbm_model_h10.txt`（h10）
- 来源：本机 `sm701` 项目生产定稿 `production/prod_h1/h5/h10_tech.txt`（2026-10-11）。
- 类型：三个 LightGBM binary classifier，分别预测 T+1 / T+5 / T+10 上涨概率。
- 特征：40 维 tech 特征，实现见 `models/sm701_features.py`；列序与 `production/features_tech.json` 一致。
- 训练使用的行情：A 股后复权日线；模型适配器声明 `adjust="hfq"`。
- 验证指标（AUC / IC / best_iter）：h1 0.5440 / 0.0145 / 1566；h5 0.5560 / 0.0461 / 1998；h10 0.5645 / 0.0665 / 1600。
- 打分输出：0~1；`predict` 同时返回 `score_h1` / `score_h5` / `score_h10`。
- 默认策略：h1 买入 0.55 / 卖出 0.45，h5 与 h10 趋势确认阈值 0.50（`strategies/builtin.py::Sm701Strategy`）。

## 特征分组

- 动量：ret1 / ret5 / ret10 / ret20 / ret60
- 均线：ma5r / ma10r / ma20r / ma60r / ma5_20 / ma20_60
- 波动：vol20 / vol60 / volr / atr_n
- 超买超卖：rsi6 / rsi14 / kdj_k / kdj_d / kdj_j
- 趋势：macd_dif / macd_dea / macd_hist / bollpos
- 位置与形态：hilo20 / cpos / amp / gap
- 量能：vr5 / vr20 / logvol / updays20
- 横截面排名：r_ret5 / r_ret20 / r_ret60 / r_ma20r / r_rsi14 / r_vr20 / r_vol20 / r_logvol

## 使用限制

- 单标的回测时，8 个横截面排名特征使用中性值 0.5。
- 三周期共振会提高换手：`trend_threshold` 越低越宽松，越高越严格。
- 特征复权口径必须为后复权；交易可以使用前复权。
- 该模型是研究示例，不代表未来收益或投资建议。

来源：本机 `sm701` 量化项目生产定稿。
