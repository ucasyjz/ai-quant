"""
batch_simulate.py - 30天批量模拟

不用等30天，现在就模拟跑完30个交易日的完整流程：
    每天生成新的行情数据（模拟价格变化）→ 多因子打分 → 生成交易计划 → 执行

这样你能立刻看到"一个月后模拟组合是什么表现"。
"""

import os
import sys
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

os.environ["PYTHONIOENCODING"] = "utf-8"

from stock_universe import get_universe, get_stock_name
from signal_engine import score_universe
from portfolio import (
    load_portfolio, save_portfolio, generate_trading_plan,
    execute_plan, calc_total_value
)
from config import INITIAL_CASH


def simulate_30_days():
    """模拟30个交易日的完整AI选股流程"""

    print("=" * 65)
    print("  30天批量模拟 - A股AI选股系统")
    print("=" * 65)

    # 初始化持仓（清空重新开始）
    portfolio = {
        "cash": INITIAL_CASH,
        "holdings": {},
        "trades": [],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_update": None,
        "daily_snapshots": [],
    }

    universe = get_universe()
    base_prices = {
        "600519": 1133, "000858": 82, "300750": 161, "002594": 218,
        "510300": 2.59, "510500": 5.35, "512100": 2.79, "159915": 3.90,
        "600036": 44, "601398": 4.26, "000725": 2.67, "002475": 29,
        "600276": 34, "000538": 60, "600887": 26, "000651": 45,
        "601899": 7.89, "600019": 5.18, "600050": 6.10, "002230": 57,
        "601318": 41, "000333": 77, "000876": 9.78, "512880": 0.99,
        "512760": 1.53, "512690": 2.82, "512010": 0.59, "515030": 1.39,
        "515790": 1.03,
    }

    # 生成280个交易日（约250天历史 + 30天模拟）
    # bdate_range生成工作日，需要足够的日历跨度
    stock_params = {}
    for code, name, sector, stype in universe:
        seed = int(code[:6].lstrip('0') or '0')
        np.random.seed(seed + 100)
        stock_params[code] = {
            "drift": np.random.uniform(-0.0005, 0.001),    # 日均趋势
            "vol": np.random.uniform(0.015, 0.035),          # 日波动率
            "base_vol": np.random.uniform(1e6, 5e7),         # 基础成交量
        }

    # 直接生成280个工作日
    dates = pd.bdate_range(
        start=datetime.now() - timedelta(days=420),  # 420日历日 ≈ 300工作日
        periods=280
    )
    total_days = len(dates)  # 280

    # 为每只股票生成完整价格序列
    all_prices = {}
    all_data = {}

    for code, name, sector, stype in universe:
        seed = int(code[:6].lstrip('0') or '0')
        np.random.seed(seed + 200)
        params = stock_params[code]

        n = len(dates)  # 280
        returns = np.random.normal(params["drift"], params["vol"], n)

        # 加入一些趋势变化（模拟牛熊切换）
        for i in range(n):
            # 每60天换一次regime
            regime = (i // 60) % 3
            if regime == 0:
                returns[i] += 0.0005   # 牛市偏涨
            elif regime == 1:
                returns[i] -= 0.0003   # 熊市偏跌
            # regime 2: 震荡

        prices = np.zeros(n)
        prices[0] = base_prices.get(code, 10.0)
        for i in range(1, n):
            prices[i] = prices[i-1] * (1 + returns[i])
            if prices[i] < prices[0] * 0.3:
                prices[i] = prices[0] * 0.3

        volumes = np.random.lognormal(
            mean=np.log(params["base_vol"]), sigma=0.5, size=n
        ).astype(int)

        df = pd.DataFrame({
            "date": dates[:n],
            "open": prices * (1 + np.random.uniform(-0.005, 0.005, n)),
            "close": prices,
            "high": prices * (1 + np.abs(np.random.normal(0, 0.008, n))),
            "low": prices * (1 - np.abs(np.random.normal(0, 0.008, n))),
            "volume": volumes,
        })
        all_data[code] = df
        all_prices[code] = prices

    print(f"\n  初始资金: ¥{INITIAL_CASH:,.0f}")
    print(f"  股票池: {len(universe)} 只")
    print(f"  模拟天数: 30 个交易日")
    print(f"  历史数据: {total_days - 30} 天（用于因子计算）")
    print()

    history_len = total_days - 30  # 前250天是历史

    # 逐日模拟
    for day_idx in range(30):
        current_idx = history_len + day_idx
        current_date = dates[current_idx]

        # 取截止当天的数据（切片）
        day_data = {}
        day_prices = {}
        day_open_prices = {}
        for code in all_data:
            df = all_data[code].iloc[:current_idx + 1].copy()
            day_data[code] = df
            day_prices[code] = round(df["close"].iloc[-1], 2)
            day_open_prices[code] = round(df["open"].iloc[-1], 2)

        # 多因子打分
        scores = score_universe(day_data)
        for s in scores:
            s["name"] = get_stock_name(s["code"])

        # 生成交易计划（买入用开盘价）
        plan = generate_trading_plan(portfolio, scores, day_prices, day_open_prices)

        # 执行交易（快照估值用收盘价）
        portfolio = execute_plan(portfolio, plan, close_prices=day_prices)

        # 记录当日净值
        total_value = calc_total_value(portfolio, day_prices)
        portfolio["daily_snapshots"].append({
            "date": current_date.strftime("%Y-%m-%d"),
            "cash": round(portfolio["cash"], 2),
            "total_value": round(total_value, 2),
            "n_holdings": len(portfolio["holdings"]),
        })

        # 打印当日摘要
        day_num = day_idx + 1
        return_pct = (total_value / INITIAL_CASH - 1) * 100
        n_buy = len(plan.get("buy", []))
        n_sell = len(plan.get("sell", []))
        n_hold = len(plan.get("hold", []))

        print(f"  Day {day_num:2d} | {current_date.strftime('%m-%d')} | "
              f"资产 ¥{total_value:>10,.0f} | 收益 {return_pct:+6.1f}% | "
              f"持仓 {len(portfolio['holdings'])} | "
              f"买{n_buy} 卖{n_sell} 持{n_hold}")

    # 保存最终持仓
    save_portfolio(portfolio)

    # === 生成30天报告 ===
    print("\n" + "=" * 65)
    print("  30天模拟结果报告")
    print("=" * 65)

    final_value = calc_total_value(portfolio, day_prices)
    total_return = (final_value / INITIAL_CASH - 1) * 100
    n_trades = len(portfolio["trades"])
    buy_trades = [t for t in portfolio["trades"] if t["action"] == "买入"]
    sell_trades = [t for t in portfolio["trades"] if t["action"] == "卖出"]

    # 计算胜率
    winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losing = [t for t in sell_trades if t.get("pnl", 0) <= 0]
    win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0

    # 最大回撤
    snapshots = portfolio["daily_snapshots"]
    peak = INITIAL_CASH
    max_dd = 0
    for s in snapshots:
        if s["total_value"] > peak:
            peak = s["total_value"]
        dd = (s["total_value"] - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd

    print(f"\n  初始资金:     ¥{INITIAL_CASH:,.0f}")
    print(f"  最终资产:     ¥{final_value:,.0f}")
    print(f"  总收益:       {total_return:+.1f}%")
    print(f"  最大回撤:     {max_dd:.1f}%")
    print(f"  总交易笔数:   {n_trades} (买{len(buy_trades)} + 卖{len(sell_trades)})")
    print(f"  胜率:         {win_rate:.1f}% ({len(winning)}胜 / {len(losing)}负)")

    if sell_trades:
        avg_win = np.mean([t["pnl_pct"] for t in winning]) if winning else 0
        avg_loss = np.mean([t["pnl_pct"] for t in losing]) if losing else 0
        print(f"  平均盈利:     +{avg_win:.1f}%")
        print(f"  平均亏损:     {avg_loss:.1f}%")
        print(f"  盈亏比:       {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "")

    # 当前持仓
    print(f"\n  {'='*50}")
    print(f"  当前持仓:")
    print(f"  {'代码':<8} {'名称':<12} {'持仓':>8} {'成本':>10} {'现价':>10} {'盈亏%':>8}")
    print(f"  {'-'*50}")
    for code, h in portfolio["holdings"].items():
        price = day_prices.get(code, h["buy_price"])
        pnl_pct = (price / h["buy_price"] - 1) * 100
        print(f"  {code:<8} {h['name']:<12} {h['shares']:>8} "
              f"¥{h['buy_price']:>9.2f} ¥{price:>9.2f} {pnl_pct:>+7.1f}%")

    print(f"\n  现金: ¥{portfolio['cash']:,.0f}")

    # 净值曲线数据
    print(f"\n  {'='*50}")
    print(f"  每日净值:")
    print(f"  {'日期':<12} {'总资产':>12} {'收益率':>8} {'持仓数':>6}")
    print(f"  {'-'*42}")
    for s in snapshots:
        ret = (s["total_value"] / INITIAL_CASH - 1) * 100
        print(f"  {s['date']:<12} ¥{s['total_value']:>11,.0f} {ret:>+7.1f}% {s['n_holdings']:>5}")

    # 保存报告到JSON
    report = {
        "initial_cash": INITIAL_CASH,
        "final_value": round(final_value, 2),
        "total_return": round(total_return, 2),
        "max_drawdown": round(max_dd, 2),
        "n_trades": n_trades,
        "win_rate": round(win_rate, 1),
        "snapshots": snapshots,
        "trades": portfolio["trades"],
    }
    with open("simulation_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  报告已保存: simulation_report.json")
    print(f"  持仓已保存: portfolio.json")
    print(f"  打开 dashboard.html 查看可视化仪表盘")

    return report


if __name__ == "__main__":
    # 先清空旧持仓
    if os.path.exists("portfolio.json"):
        os.remove("portfolio.json")

    report = simulate_30_days()
