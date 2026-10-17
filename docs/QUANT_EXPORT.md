# 生成量化与 SSH 自动安装

> 版本 0.1.1 · 当前采用 AKQuant 信号回放回测架构

## 1. 入口

主界面左侧策略区：

**生成量化 · AKQuant 项目 / SSH 自动安装**

点击后会打开「生成量化 · AKQuant」窗口。窗口使用打开时的策略快照：

- 当前策略与参数
- 股票代码
- 回测区间
- 复权口径
- 初始资金、费率、仓位等交易设置

## 2. 本地生成

1. 选择输出目录（默认 `exports/quant/`）。
2. 点击「生成本地 AKQuant 项目」。
3. 桌面端执行策略并生成信号。
4. 在新目录中生成完整 AKQuant 项目。

生成结构：

```text
akquant_<策略>_<代码>_<时间>/
├── config.json
├── data/
│   ├── bars.csv
│   └── signals.csv
├── quant_app/
│   ├── __init__.py
│   ├── strategy.py
│   └── backtest.py
├── reference/
│   ├── reference_trades.csv
│   └── reference_metrics.json
├── requirements.txt
├── run.sh
└── README.md
```

### 信号语义

- 桌面端策略在 T 日收盘产生 entry/exit 信号。
- 信号写入 `data/signals.csv`。
- `SignalReplayStrategy` 在 AKQuant 的 T 日 `on_bar` 中读取信号并提交订单。
- AKQuant 默认在下一根 K 线执行，实现 T+1 口径。
- 手续费、印花税、过户费、T+1、整手由 AKQuant 回测配置执行。

## 3. 本地运行生成项目

```bash
cd akquant_xxxx
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m quant_app.backtest --check
.venv/bin/python -m quant_app.backtest
```

或：

```bash
./run.sh
```

结果写入项目的 `output/backtest_result.txt`。

## 4. SSH 自动安装

在生成窗口填写：

| 字段 | 说明 |
|---|---|
| IP / 主机 | 服务器地址 |
| 端口 | 默认 22 |
| 用户名 | 默认 root |
| 密码 | 只用一次，不持久化 |
| 远程目录 | 默认 `stock_strategy_quant` |
| 安装后运行回测 | 可选 |

点击「生成并 SSH 安装」后：

1. 如尚未生成，先在本地生成项目。
2. 打包项目（不含 `.venv`、`__pycache__`、`output`）。
3. 用 SSH/SFTP 上传。
4. 远程创建目录、解压、`chmod +x run.sh`。
5. 检查 Python >= 3.10。
6. 创建远程 `.venv`。
7. 安装 `requirements.txt`（含 `akquant==0.3.67`）。
8. 执行 `quant_app.backtest --check` 数据/依赖自检。
9. 可选运行一次 AKQuant 回测。

完成后远程启动：

```bash
cd <远程目录>
./run.sh
```

## 5. 安全说明

- SSH 密码只保存在窗口内存中；关闭窗口后清空。
- 密码不写入 `config/settings.json`、不写入项目文件、不写日志。
- 当前使用 Paramiko 的 `AutoAddPolicy`，首次连接自动信任主机指纹，并在日志中显示指纹。
- 生产环境建议：
  - 使用 SSH Key 代替密码；
  - 使用受限用户，不使用 root；
  - 在 known_hosts 中预置主机指纹；
  - 限制服务器出口和目录权限。

## 6. 当前限制

- 生成物是 **回测/信号回放项目**，不是实盘交易机器人。
- 实时行情重算、模拟盘/实盘券商接口、定时任务和 systemd 服务未包含在 0.1.1。
- 信号是生成时的历史快照；之后行情变化不会自动更新。
- 生成的 AKQuant 回测结果与桌面回测可能因撮合细节/成交价格不同而有差异。
- 远程服务器需要 Python 3.10+ 和可访问 PyPI 的网络。

## 7. 后续方向

- 将策略条件翻译为 AKQuant 原生 `on_bar` 逻辑，去掉信号回放。
- 接入 AKQuant live feed 与模拟盘/券商通道。
- 加入 systemd 服务、日志轮转和自动更新。
- 支持 SSH Key、跳板机、Docker 部署。
