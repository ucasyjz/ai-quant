# A股AI量化选股系统

[![GitHub Actions - 收盘扫描](https://img.shields.io/badge/GitHub%20Actions-收盘%20扫描-blue?logo=github-actions)](.github/workflows/daily-scan.yml)
[![GitHub Actions - 盘中交易](https://img.shields.io/badge/GitHub%20Actions-盘中%20交易-green?logo=github-actions)](.github/workflows/realtime-trade.yml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-yellow?logo=python)](https://www.python.org)
[![模拟盘](https://img.shields.io/badge/账户-模拟盘-orange)]()

> 一个完全跑在 GitHub Actions 上的A股多因子量化系统，**不用开机、不用花钱、不用每天点**。每天 9:25 自动开始盘中交易，15:35 自动收盘扫描+复盘+参数自调整。

---

## ✨ 特性

- 🧠 **4因子评分引擎** —— 趋势 / 动量 / 量能 / 均值回归，权重 AI 自学习
- 🤖 **盘中自动交易** —— 9:25-15:10 持续运行，严格遵守 A 股 T+1 / 涨跌停 / 100股手数 / 印花税佣金
- 📊 **每日可视化仪表盘** —— 持仓、收益、因子归因、交易记录
- 🔁 **AI 自我复盘** —— 8 维度评估每日表现，**自动调参**（不卡死周末循环）
- 💾 **portfolio.json 唯一真相源** —— GitHub main 分支为权威，本地永远只读
- 🛡️ **三重数据防线** —— 防过期价格、防同日重跑、跑前自动备份
- ☁️ **零成本云端运行** —— GitHub Actions 公开仓库完全免费

---

## 📊 系统概览

```
┌─────────────────────────────────────────────────────────────┐
│  GitHub Actions 云端（唯一运行环境）                         │
│                                                              │
│  9:25 启动 ────────► 15:10 自动停止                          │
│  ┌──────────────────────────────┐                            │
│  │  realtime_monitor.py (盘中)  │  每30秒扫描                │
│  │  - 拉取实时价                │  - 买入：涨幅>1% 且评分≥25  │
│  │  - 止盈/止损/T+1判断         │  - 卖出：止盈10% / 止损5%   │
│  └──────────────────────────────┘                            │
│              │ commit + push                                 │
│              ▼                                               │
│  ┌──────────────────────────────┐                            │
│  │  15:35 收盘后                │                            │
│  │  generate_daily.py           │                            │
│  │  1. 全市场扫描候选池         │                            │
│  │  2. 拉K线 → 4因子打分        │                            │
│  │  3. 生成交易计划             │                            │
│  │  4. 执行模拟交易             │                            │
│  │  5. 生成仪表盘 HTML          │                            │
│  │  6. AI 8维度复盘 + 调参      │                            │
│  └──────────────────────────────┘                            │
│              │                                               │
│              ▼                                               │
│       portfolio.json ← 唯一持仓真相源                        │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 快速开始

### 前置条件

- 一个 **GitHub 公开仓库**（私有仓库每月仅 2000 分钟免费额度，跑不起日内交易）
- 本地 Python 3.11+ 环境（仅用于开发/调试，不参与实际运行）

### 部署步骤

#### 1. 克隆并配置

```bash
git clone <你的仓库地址> ai-quant
cd ai-quant
pip install -r requirements.txt
```

#### 2. 初始化持仓（首次）

```bash
python main.py
# 编辑 portfolio.json 调整初始资金（默认 ¥1,000,000）
git add portfolio.json
git commit -m "init: 初始持仓"
git push
```

#### 3. 启用 GitHub Actions

- 进入仓库 → **Settings → Actions → General**
- 勾选 **Allow all actions and reusable workflows**
- 进入 **Settings → Pages**（可选）—— 用于托管仪表盘

#### 4. 触发首次运行

- **Actions** 标签 → 选 **A股盘中实时交易** → **Run workflow**
- 选 **A股AI选股每日扫描** → **Run workflow**

✅ 之后系统**全自动运行**，不需要再管它。

---

## 📁 项目结构

```
ai-quant/
├── generate_daily.py          # 每日扫描主入口（6步流水线）
├── realtime_monitor.py        # 盘中自动交易系统
├── data_fetcher.py            # 并行K线拉取（akshare / 新浪）
├── signal_engine.py           # 4因子评分引擎
├── portfolio.py               # 持仓管理 + 交易计划生成
├── risk_manager.py            # 止盈/止损/仓位控制
├── self_review.py             # AI 8维度复盘 + 参数自调整
├── dashboard.py               # 可视化仪表盘生成
├── config.py                  # 全局配置（初始资金/手续费/因子权重）
├── stock_universe.py          # 候选池管理（剔除创业板/科创板/北交所）
├── trading_calendar.py        # A股交易日历
├── strategy_params.json       # 可调参数（因子权重/buy_threshold）
├── portfolio.json             # 🔒 持仓真相源
├── .github/workflows/
│   ├── daily-scan.yml         # 每日 15:35 收盘扫描
│   └── realtime-trade.yml     # 每日 9:25 盘中交易
└── backups/                   # 每次运行自动备份
```

---

## 🧠 4因子评分引擎

每个因子在 -100 到 +100 之间打分，按权重加权后得到总分（-100 ~ +100）。

| 因子 | 权重 | 含义 | 选股偏好 |
|------|------|------|---------|
| **趋势 (Trend)** | 30% | MA5/MA10/MA20 多头排列强度 | 强势多头高分 |
| **动量 (Momentum)** | 30% | 5日/20日涨幅加速度 | 涨速高分 |
| **量能 (Volume)** | 20% | 成交量/换手率异常放大 | 放量高分 |
| **均值回归 (Mean Reversion)** | 20% | 偏离 MA20 的程度 | -30~-60 表明有回调压力 |

**买入门槛**：`总分 ≥ buy_threshold`（默认 25，AI 会根据历史命中率动态调整）

**关键发现**（来自近一周实盘）：

- 📈 **量能因子**是区分涨跌的关键 —— 正量能几乎必涨，负量能几乎必跌
- ⚠️ **趋势因子**容易满分(100)但会误导，需结合量能判断
- 📉 **均值回归 -30~-60** 表明已涨多，回调压力大

---

## 🤖 交易规则（严格 A 股规则）

### 买入

- 盘中涨幅 > 1%
- 当前评分 ≥ buy_threshold（默认 25）
- 未涨停（涨幅 < 9.5%）
- 现金充足，单只 ≤ 25% 总仓位
- 总持仓 ≤ 5 只

### 卖出

- 🟢 **止盈**：盈利 > 10%
- 🔴 **止损**：亏损 > 5%
- 📉 **评分止损**：持仓评分 < sell_threshold（-20）连续出现
- 🆕 **T+1 强约束**：当天买入的股票**次日才能卖**（已硬编码）

### 仓位

- 单只 ≤ 25% 总资产
- 总持仓 ≤ 5 只
- 100 股整手（A 股规则）

### 费用

- 买入：万 2.5 佣金（最低 5 元）
- 卖出：万 2.5 佣金 + 千 1 印花税
- 滑点：0.1%

---

## 📈 数据新鲜度三重防线

历史上发生过 2 次"价格错乱"事故（系统显示涨 5%，实际是用了 3 天前的数据），为此加了 3 道防线：

| 防线 | 检查点 | 处理 |
|------|--------|------|
| **1. END_DATE 动态取今天** | 不再硬编码日期 | 拉取数据时强制用今天 |
| **2. 缓存数据校验** | 拉数据前检查 cache 最后一行日期 | 过期 → 强制重新拉取 |
| **3. 全局新鲜度校验** | 拉完后逐只检查 | 过期数据 → 报警 + 不参与交易 |

---

## 🛠️ 本地开发

### 单次运行

```bash
# 收盘扫描（开发/调试）
python generate_daily.py --force

# 盘中交易（仅本地调试，云端用 --cloud）
python realtime_monitor.py
```

### 回测

```bash
python backtest.py            # 单次回测
python batch_simulate.py      # 批量回测
```

### 手动推送 portfolio（一般不需要）

> ⚠️ **警告**：本地 portfolio.json 是**只读**的。GitHub main 分支是唯一真相源。如需修改，直接编辑云端文件或用 GitHub 网页编辑。

```bash
# 从云端拉取最新持仓（推荐）
git pull origin main

# 强制推送（仅紧急情况）
GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never git push origin main
```

---

## ⚙️ 配置项

### `config.py`

```python
INITIAL_CASH = 1_000_000        # 初始资金（模拟盘）
COMMISSION_RATE = 0.00025        # 佣金万2.5
STAMP_TAX_RATE = 0.001          # 印花税千1
SLIPPAGE = 0.001                # 滑点
```

### `strategy_params.json`

```json
{
  "factor_weights": {
    "trend": 0.30,
    "momentum": 0.30,
    "volume": 0.20,
    "mean_reversion": 0.20
  },
  "buy_threshold": 25
}
```

> 💡 权重和阈值会由 `self_review.py` 在每个交易日自动调优，**非交易日不调**（防止周末假数据导致死循环）。

---

## 🐛 已知限制

| 限制 | 原因 | 解决方式 |
|------|------|----------|
| GitHub Actions cron 不保证准时 | 免费账户限制，可能延迟 5-30 分钟 | 偶尔在 Actions 页面手动点 Run workflow |
| 私有仓库 2000 分钟/月不够 | 盘中交易需 ~6600 分钟/月 | **必须用公开仓库** |
| 周末/节假日不交易 | A 股不开市 | trading_calendar 自动判断 |

---

## 📝 经验教训（踩过的坑）

1. **portfolio.json 不要在本地跑** —— 本地 + 云端会分叉，导致持仓覆盖
2. **commit/push 要加 rebase** —— 不然两个 workflow 会互相冲突
3. **buy_threshold 不能在非交易日自调** —— 周末命中率=0%，AI 会越调越高死循环
4. **估值要用收盘价** —— 用买入价估值会虚高 3-4 万
5. **持仓不在候选池要保留** —— `continue` 跳过会让持仓从评估消失

---

## 📄 License

MIT License - 仅供学习交流，不构成投资建议。模拟盘，盈亏自负。

---

## 🙏 致谢

- 数据源：[akshare](https://github.com/akfamily/akshare)
- 运行平台：[GitHub Actions](https://github.com/features/actions)
