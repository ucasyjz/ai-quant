"""
simulate_month2.py - 第二个月模拟

从当前 portfolio.json 的持仓状态继续，往后推30个交易日。
AI每天打分、生成交易计划、执行买卖，最后输出第二个月的完整报告。
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


def simulate_month2():
    """第二个月：从现有持仓继续模拟30天"""

    print("=" * 65)
    print("  第二个月模拟 - A股AI选股系统")
    print("=" * 65)

    # 加载现有持仓（不重置）
    portfolio = load_portfolio()
    start_value = calc_total_value(portfolio, _get_current_prices(portfolio))

    print(f"\n  起始状态: 从第一月末持仓继续")
    print(f"  起始资产: ¥{start_value:,.0f}")
    print(f"  现金:     ¥{portfolio['cash']:,.0f}")
    print(f"  持仓数:   {len(portfolio['holdings'])} 只")
    for code, h in portfolio["holdings"].items():
        print(f"    {h['name']:<10} {h['shares']:>8}股 @ ¥{h['buy_price']:.2f}")
    print()

    universe = get_universe()

    # 基准价格：用当前持仓的买入价 + 其他股票的市场价
    base_prices = {
        "600519": 1133, "000858": 82, "300750": 161, "002594": 218,
        "510300": 2.59, "510500": 5.35, "512100": 4.81, "159915": 3.90,
        "600036": 44, "601398": 5.48, "000725": 2.67, "002475": 29,
        "600276": 34, "000538": 60, "600887": 30.21, "000651": 45,
        "601899": 8.14, "600019": 5.18, "600050": 6.10, "002230": 57,
        "601318": 41, "000333": 77, "000876": 9.78, "512880": 0.99,
        "512760": 1.53, "512690": 2.82, "512010": 1.25, "515030": 1.39,
        "515790": 1.03,
    }

    # 生成310个交易日 = 280天历史(含第一月) + 30天第二月
    # 用不同的随机种子，让第二个月行情跟第一个月不一样
    dates = pd.bdate_range(
        start=datetime.now() - timedelta(days=460),
        periods=310
    )
    total_days = len(dates)

    stock_params = {}
    for code, name, sector, stype in universe:
        seed = int(code[:6].lstrip('0') or '0')
        # 第二个月用不同的种子，行情走不一样的路
        np.random.seed(seed + 300)
        stock_params[code] = {
            "drift": np.random.uniform(-0.0008, 0.0012),   # 趋势范围更大
            "vol": np.random.uniform(0.018, 0.040),          # 波动更大
            "base_vol": np.random.uniform(1e6, 5e7),
        }

    all_data = {}
    all_prices = {}

    for code, name, sector, stype in universe:
        seed = int(code[:6].lstrip('0') or '0')
        np.random.seed(seed + 400)
        params = stock_params[code]

        n = total_days
        returns = np.random.normal(params["drift"], params["vol"], n)

        # 加入更频繁的regime切换，让第二个月更有挑战性
        for i in range(n):
            regime = (i // 50) % 4  # 每50天切换一次，4种状态
            if regime == 0:
                returns[i] += 0.0008   # 强牛
            elif regime == 1:
                returns[i] -= 0.0005   # 熊市
            elif regime == 2:
                returns[i] += 0.0003   # 温和上涨
            # regime 3: 震荡

        prices = np.zeros(n)
        prices[0] = base_prices.get(code, 10.0)
        for i in range(1, n):
            prices[i] = prices[i - 1] * (1 + returns[i])
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

    print(f"  模拟天数: 30 个交易日（第二个月）")
    print(f"  历史数据: {total_days - 30} 天（用于因子计算）")
    print()

    history_len = total_days - 30  # 前280天是历史（含第一月）

    month1_end_value = start_value
    prev_trades_count = len(portfolio["trades"])

    # 逐日模拟第二个月
    for day_idx in range(30):
        current_idx = history_len + day_idx
        current_date = dates[current_idx]

        # 取截止当天的数据
        day_data = {}
        day_prices = {}
        for code in all_data:
            df = all_data[code].iloc[:current_idx + 1].copy()
            day_data[code] = df
            day_prices[code] = round(df["close"].iloc[-1], 2)

        # 多因子打分
        scores = score_universe(day_data)
        for s in scores:
            s["name"] = get_stock_name(s["code"])

        # 生成交易计划
        plan = generate_trading_plan(portfolio, scores, day_prices)

        # 执行交易
        portfolio = execute_plan(portfolio, plan)

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
        return_pct = (total_value / month1_end_value - 1) * 100
        total_return_pct = (total_value / INITIAL_CASH - 1) * 100
        n_buy = len(plan.get("buy", []))
        n_sell = len(plan.get("sell", []))
        n_hold = len(plan.get("hold", []))

        print(f"  Day {day_num:2d} | {current_date.strftime('%m-%d')} | "
              f"资产 ¥{total_value:>10,.0f} | "
              f"本月 {return_pct:+6.1f}% | 累计 {total_return_pct:+6.1f}% | "
              f"持仓 {len(portfolio['holdings'])} | "
              f"买{n_buy} 卖{n_sell} 持{n_hold}")

    # 保存最终持仓
    save_portfolio(portfolio)

    # === 第二个月报告 ===
    print("\n" + "=" * 65)
    print("  第二个月模拟结果报告")
    print("=" * 65)

    final_value = calc_total_value(portfolio, day_prices)
    month2_return = (final_value / month1_end_value - 1) * 100
    total_return = (final_value / INITIAL_CASH - 1) * 100

    # 本月新交易
    new_trades = portfolio["trades"][prev_trades_count:]
    n_new_trades = len(new_trades)
    buy_trades = [t for t in new_trades if t["action"] == "买入"]
    sell_trades = [t for t in new_trades if t["action"] == "卖出"]

    # 胜率
    winning = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losing = [t for t in sell_trades if t.get("pnl", 0) <= 0]
    win_rate = len(winning) / len(sell_trades) * 100 if sell_trades else 0

    # 本月最大回撤
    month_snapshots = portfolio["daily_snapshots"][-30:]
    peak = month1_end_value
    max_dd = 0
    for s in month_snapshots:
        if s["total_value"] > peak:
            peak = s["total_value"]
        dd = (s["total_value"] - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd

    print(f"\n  === 第二个月业绩 ===")
    print(f"  月初资产:     ¥{month1_end_value:,.0f}")
    print(f"  月末资产:     ¥{final_value:,.0f}")
    print(f"  本月收益:     {month2_return:+.1f}%")
    print(f"  本月最大回撤: {max_dd:.1f}%")
    print(f"  本月交易:     {n_new_trades}笔 (买{len(buy_trades)} + 卖{len(sell_trades)})")
    if sell_trades:
        print(f"  本月胜率:     {win_rate:.1f}% ({len(winning)}胜 / {len(losing)}负)")

    print(f"\n  === 两个月累计 ===")
    print(f"  初始资金:     ¥{INITIAL_CASH:,.0f}")
    print(f"  当前资产:     ¥{final_value:,.0f}")
    print(f"  累计收益:     {total_return:+.1f}%")
    print(f"  总交易笔数:   {len(portfolio['trades'])}笔")

    # 当前持仓
    print(f"\n  {'='*60}")
    print(f"  当前持仓:")
    print(f"  {'代码':<8} {'名称':<12} {'持仓':>8} {'成本':>10} {'现价':>10} {'盈亏%':>8}")
    print(f"  {'-'*60}")
    for code, h in portfolio["holdings"].items():
        price = day_prices.get(code, h["buy_price"])
        pnl_pct = (price / h["buy_price"] - 1) * 100
        print(f"  {code:<8} {h['name']:<12} {h['shares']:>8} "
              f"¥{h['buy_price']:>9.2f} ¥{price:>9.2f} {pnl_pct:>+7.1f}%")

    print(f"\n  现金: ¥{portfolio['cash']:,.0f}")

    # 本月交易明细
    if new_trades:
        print(f"\n  {'='*60}")
        print(f"  本月交易明细:")
        print(f"  {'日期':<12} {'操作':<5} {'名称':<10} {'股数':>8} {'价格':>10} {'盈亏':>10}")
        print(f"  {'-'*60}")
        for t in new_trades:
            pnl_str = f"¥{t['pnl']:+,.0f}" if t.get("pnl", 0) != 0 else "-"
            print(f"  {t['date']:<12} {t['action']:<5} {t['name']:<10} "
                  f"{t['shares']:>8} ¥{t['price']:>9.2f} {pnl_str:>10}")

    # 净值曲线
    print(f"\n  {'='*60}")
    print(f"  本月每日净值:")
    print(f"  {'日期':<12} {'总资产':>12} {'本月收益':>8} {'累计收益':>8}")
    print(f"  {'-'*44}")
    for s in month_snapshots:
        m_ret = (s["total_value"] / month1_end_value - 1) * 100
        t_ret = (s["total_value"] / INITIAL_CASH - 1) * 100
        print(f"  {s['date']:<12} ¥{s['total_value']:>11,.0f} {m_ret:>+7.1f}% {t_ret:>+7.1f}%")

    # 保存报告
    report = {
        "month": 2,
        "month_start_value": round(month1_end_value, 2),
        "month_end_value": round(final_value, 2),
        "month_return": round(month2_return, 2),
        "total_return": round(total_return, 2),
        "max_drawdown": round(max_dd, 2),
        "n_new_trades": n_new_trades,
        "win_rate": round(win_rate, 1),
        "new_trades": new_trades,
        "month_snapshots": month_snapshots,
    }
    with open("simulation_report_month2.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  报告已保存: simulation_report_month2.json")
    print(f"  持仓已保存: portfolio.json")

    return report, day_prices, all_data


def _get_current_prices(portfolio):
    """用持仓的买入价作为当前价估算"""
    prices = {}
    for code, h in portfolio["holdings"].items():
        prices[code] = h["buy_price"]
    return prices


if __name__ == "__main__":
    report, day_prices, all_data = simulate_month2()
