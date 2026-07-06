"""
test_dashboard.py - 仪表盘预览测试（不执行交易，不修改任何状态文件）

只做：拉数据 → 打分 → 生成计划(不执行) → 渲染仪表盘 → 用示例数据渲染复盘面板
"""
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

from generate_daily import fetch_universe_data
from signal_engine import score_universe
from portfolio import load_portfolio, generate_trading_plan, calc_total_value
from stock_universe import get_stock_name
from dashboard import generate_dashboard, append_review_to_dashboard
from trading_calendar import trading_day_countdown

print("=" * 50)
print("  仪表盘预览测试（不修改任何状态）")
print("=" * 50)

print("\n>>> 第1步: 获取行情数据")
data_dict, prices = fetch_universe_data()

print("\n>>> 第2步: 多因子打分")
scores = score_universe(data_dict)
for s in scores:
    s["name"] = get_stock_name(s["code"])
print(f"  打分完成，{len(scores)} 只标的")

print("\n>>> 第3步: 生成交易计划(不执行)")
portfolio = load_portfolio()
plan = generate_trading_plan(portfolio, scores, prices)
total_value = calc_total_value(portfolio, prices)
print(f"  总资产: ¥{total_value:,.0f}")
print(f"  买入: {len(plan['buy'])}  卖出: {len(plan['sell'])}  持有: {len(plan['hold'])}")

print("\n>>> 第4步: 生成数据")
td = trading_day_countdown()
print(f"  交易日状态: {td['message']}")
path = generate_dashboard(portfolio, scores, plan, prices, data_dict, td)
print(f"  数据文件: data.json")
print(f"  前端页面: {path}")

print("\n>>> 第5步: 渲染复盘面板(示例数据)")
# 手动构造示例复盘数据，不调用 self_review（避免修改文件）
review_result = {
    "date": "2026-07-05",
    "adjustments": [
        {"factor": "trend", "old_weight": 0.30, "new_weight": 0.32,
         "change": 0.02, "reason": "贡献度+0.85，命中率62%，加权"},
        {"factor": "mean_reversion", "old_weight": 0.20, "new_weight": 0.18,
         "change": -0.02, "reason": "贡献度-0.42，命中率38%，降权"},
    ],
}
review_report = """=== AI复盘报告 2026-07-05 ===

【信号准确度】
  昨天发出4个买入信号，3个命中(今天涨了)，命中率75.0%
  打脸的信号:
    京东方A: 预测买入但实际跌1.2%

【因子归因】
  趋势: 贡献度+0.85 命中率62% (+)
  动量: 贡献度+0.32 命中率55% (+)
  量能: 贡献度+0.15 命中率51% (+)
  均值回归: 贡献度-0.42 命中率38% (-)
  表现最好的: 趋势
  表现最差的: 均值回归

【仓位配置】
  总资产: ¥1,000,000
  持仓4只, 现金0.5%, 最大单只24.9%
  板块分布: 科技24.9%, 医药24.9%, 消费24.9%, 银行24.9%

【风险控制】
  组合最大回撤: 0.0%

【AI自我调整】
  趋势: 0.3→0.32 (贡献度+0.85，命中率62%，加权)
  均值回归: 0.2→0.18 (贡献度-0.42，命中率38%，降权)

【明日策略】
  基于今日复盘微调了参数，明天用新参数扫描"""

append_review_to_dashboard(path, review_report, review_result)

print("\n" + "=" * 50)
print("  完成！打开 dashboard.html 查看效果")
print("=" * 50)
