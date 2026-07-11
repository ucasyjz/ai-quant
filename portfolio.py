"""
portfolio.py - 模拟持仓管理

管理虚拟持仓、计算盈亏、跟踪历史交易。
不是真实交易，是让你看到"如果按AI信号操作，会是什么结果"。

持仓数据存在 portfolio.json，每次运行更新。
第一次运行时自动创建空组合 + 100万虚拟资金。
"""

import json
import os
from datetime import datetime, timedelta
from config import INITIAL_CASH, POSITION_RATIO, COMMISSION_RATE, STAMP_TAX_RATE, SLIPPAGE
from stock_universe import get_lot_size

PORTFOLIO_FILE = "portfolio.json"
PARAMS_FILE = "strategy_params.json"

# 默认参数（文件不存在时用这个）
MAX_POSITIONS = 5
MAX_SINGLE_RATIO = 0.25
MIN_SCORE_TO_BUY = 20
MIN_SCORE_TO_SELL = -20
HARD_STOP_LOSS = -0.10


def load_strategy_params():
    """
    从 strategy_params.json 读取可调参数。
    复盘模块会调整这些参数，让策略每天进化。
    """
    global MAX_POSITIONS, MAX_SINGLE_RATIO, MIN_SCORE_TO_BUY
    global MIN_SCORE_TO_SELL, HARD_STOP_LOSS, POSITION_RATIO
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, "r", encoding="utf-8") as f:
                params = json.load(f)
            MAX_POSITIONS = params.get("max_positions", 5)
            MAX_SINGLE_RATIO = params.get("max_single_ratio", 0.25)
            MIN_SCORE_TO_BUY = params.get("buy_threshold", 20)
            MIN_SCORE_TO_SELL = params.get("sell_threshold", -20)
            HARD_STOP_LOSS = params.get("hard_stop_loss", -0.10)
            POSITION_RATIO = params.get("position_ratio", 0.80)
        except Exception:
            pass


def load_portfolio():
    """加载持仓数据，不存在则创建初始状态"""
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    # 初始状态
    portfolio = {
        "cash": INITIAL_CASH,
        "holdings": {},  # {code: {"name", "shares", "cost", "buy_date", "buy_price"}}
        "trades": [],    # 历史交易记录
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_update": None,
        "daily_snapshots": [],  # 每日净值快照
    }
    save_portfolio(portfolio)
    return portfolio


def save_portfolio(portfolio):
    """保存持仓数据"""
    portfolio["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)


def calc_total_value(portfolio, prices):
    """
    计算当前总资产
    prices: {code: current_price}
    """
    total = portfolio["cash"]
    for code, h in portfolio["holdings"].items():
        price = prices.get(code, h["buy_price"])
        total += h["shares"] * price
    return total


def generate_trading_plan(portfolio, scores, prices, open_prices=None):
    """
    根据AI评分生成今日交易计划

    价格逻辑：
        - 买入价 = 开盘价（模拟"明天早晨开盘买"）
        - 卖出价 = 收盘价（收盘后决定卖出）
        - 估值价 = 收盘价（持仓用收盘价估值）
        - 如果 open_prices 未提供，买入价回退到收盘价（兼容旧调用）

    逻辑：
        1. 先处理卖出：持仓中评分低于-20的，全部卖出
        2. 再处理买入：评分高于20的，按分数排序，买入前N只
        3. 仓位控制：单只最多25%，总仓位最多80%
        4. 最多同时持5只

    返回:
        {
            "sell": [(code, name, reason, shares, price)],
            "buy": [(code, name, reason, shares, price, amount)],
            "hold": [(code, name, score)],
        }
    """
    plan = {"sell": [], "buy": [], "hold": []}
    total_value = calc_total_value(portfolio, prices)

    # 加载最新策略参数（复盘模块可能会调整买卖阈值和仓位）
    load_strategy_params()

    # === 卖出判断 ===
    for code, h in list(portfolio["holdings"].items()):
        score_data = next((s for s in scores if s["code"] == code), None)
        if score_data is None:
            # 持仓不在评分池中：用收盘价估值，标记"评分缺失"但仍计入Hold
            current_price = prices.get(code, h["buy_price"])
            pnl_pct = (current_price / h["buy_price"] - 1) * 100
            plan["hold"].append({
                "code": code,
                "name": h["name"],
                "score": 0,
                "pnl_pct": round(pnl_pct, 1),
                "shares": h["shares"],
                "note": "评分缺失（不在候选池中）",
            })
            continue

        composite = score_data["composite"]
        current_price = prices.get(code, h["buy_price"])
        pnl_pct = (current_price / h["buy_price"] - 1) * 100

        # 卖出条件1: AI评分跌破卖出线
        # 卖出条件2: 硬止损 - 单只亏损超过止损线，不管评分直接砍
        sell_reason = None
        if pnl_pct < HARD_STOP_LOSS * 100:
            sell_reason = f"硬止损触发，亏损{pnl_pct:.1f}%超过{HARD_STOP_LOSS*100:.0f}%红线"
        elif composite < MIN_SCORE_TO_SELL:
            sell_reason = f"AI评分{composite:.0f}，触发卖出信号"

        if sell_reason:
            plan["sell"].append({
                "code": code,
                "name": h["name"],
                "reason": sell_reason,
                "shares": h["shares"],
                "price": current_price,
                "cost": h["buy_price"],
                "pnl_pct": round(pnl_pct, 1),
                "score": composite,
            })
        else:
            plan["hold"].append({
                "code": code,
                "name": h["name"],
                "score": composite,
                "pnl_pct": round(pnl_pct, 1),
                "shares": h["shares"],
            })

    # === 买入判断 ===
    # 可用仓位
    n_current = len(portfolio["holdings"]) - len(plan["sell"])
    n_slots = MAX_POSITIONS - n_current

    if n_slots > 0:
        # 候选：评分>20 且不在持仓中（或刚被卖出）
        sell_codes = {s["code"] for s in plan["sell"]}
        candidates = [
            s for s in scores
            if s["composite"] >= MIN_SCORE_TO_BUY
            and s["code"] not in portfolio["holdings"]
        ]

        # 按分数排序，取前n_slots只
        candidates = candidates[:n_slots]

        for c in candidates:
            code = c["code"]
            # 买入价用开盘价（模拟"明天早晨开盘买"），没传open_prices则回退收盘价
            buy_price = open_prices.get(code, prices.get(code, c.get("close", 10))) if open_prices else prices.get(code, c.get("close", 10))
            name = c.get("name", code)

            # 仓位计算：总资产的25%或可用现金的80%，取较小值
            # 注意：买入时要扣除佣金+滑点，所以可用资金要预留手续费
            fee_factor = 1 + COMMISSION_RATE + SLIPPAGE  # 约 1.0013
            target_amount = total_value * MAX_SINGLE_RATIO
            available = (portfolio["cash"] * POSITION_RATIO) / fee_factor
            buy_amount = min(target_amount, available)

            lot = get_lot_size(code)  # 主板100股，科创板200股

            if buy_amount < buy_price * lot:
                continue  # 买不起1手

            # A股整数倍
            shares = int(buy_amount / buy_price / lot) * lot
            if shares <= 0:
                continue

            actual_amount = shares * buy_price

            plan["buy"].append({
                "code": code,
                "name": name,
                "reason": f"AI评分{c['composite']:.0f}，{c['signal']}，信心{c['confidence']:.0f}%",
                "shares": shares,
                "price": buy_price,
                "amount": actual_amount,
                "score": c["composite"],
                "factors": {
                    "trend": c["trend"],
                    "momentum": c["momentum"],
                    "volume": c["volume"],
                    "mean_reversion": c["mean_reversion"],
                },
            })

    return plan


def execute_plan(portfolio, plan, close_prices=None):
    """执行交易计划，更新持仓

    close_prices: 收盘价字典，用于每日快照中计算持仓市值。
    如果不传，回退用 buy_price 估值（兼容旧调用）。
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # 执行卖出
    for s in plan["sell"]:
        code = s["code"]
        if code not in portfolio["holdings"]:
            continue
        h = portfolio["holdings"][code]
        gross = s["shares"] * s["price"]

        # 卖出费用: 佣金 + 印花税(卖出单向) + 滑点
        sell_commission = gross * COMMISSION_RATE
        stamp_tax = gross * STAMP_TAX_RATE
        slippage_cost = gross * SLIPPAGE
        net_proceeds = gross - sell_commission - stamp_tax - slippage_cost

        portfolio["cash"] += net_proceeds

        # 计算真实盈亏（含所有费用）
        real_pnl = net_proceeds - h["cost"]
        real_pnl_pct = (real_pnl / h["cost"] * 100) if h["cost"] > 0 else 0

        # 记录交易
        portfolio["trades"].append({
            "date": today,
            "code": code,
            "name": h["name"],
            "action": "卖出",
            "shares": s["shares"],
            "price": s["price"],
            "amount": gross,
            "net_amount": round(net_proceeds, 2),
            "cost": h["cost"],
            "fees": round(sell_commission + stamp_tax + slippage_cost, 2),
            "pnl": round(real_pnl, 2),
            "pnl_pct": round(real_pnl_pct, 2),
            "reason": s["reason"],
        })

        del portfolio["holdings"][code]

    # 执行买入
    for b in plan["buy"]:
        code = b["code"]
        if code in portfolio["holdings"]:
            continue  # 已持有，跳过

        gross = b["amount"]
        buy_commission = gross * COMMISSION_RATE
        slippage_cost = gross * SLIPPAGE
        total_cost = gross + buy_commission + slippage_cost

        if portfolio["cash"] < total_cost:
            continue  # 钱不够（含手续费）

        portfolio["cash"] -= total_cost
        portfolio["holdings"][code] = {
            "name": b["name"],
            "shares": b["shares"],
            "buy_price": b["price"],
            "cost": round(total_cost, 2),
            "buy_date": today,
        }

        portfolio["trades"].append({
            "date": today,
            "code": code,
            "name": b["name"],
            "action": "买入",
            "shares": b["shares"],
            "price": b["price"],
            "amount": gross,
            "net_amount": round(total_cost, 2),
            "cost": round(total_cost, 2),
            "fees": round(buy_commission + slippage_cost, 2),
            "pnl": 0,
            "pnl_pct": 0,
            "reason": b["reason"],
        })

    # 记录每日快照（用收盘价估值持仓市值）
    total_value = portfolio["cash"]
    for code, h in portfolio["holdings"].items():
        if close_prices and code in close_prices and close_prices[code] > 0:
            val_price = close_prices[code]
            h["last_price"] = close_prices[code]  # 更新最新收盘价，供dashboard使用
        else:
            val_price = h.get("last_price", h["buy_price"])
        total_value += h["shares"] * val_price

    portfolio["daily_snapshots"].append({
        "date": today,
        "cash": round(portfolio["cash"], 2),
        "total_value": round(total_value, 2),
        "n_holdings": len(portfolio["holdings"]),
    })

    # 只保留最近90天快照
    if len(portfolio["daily_snapshots"]) > 90:
        portfolio["daily_snapshots"] = portfolio["daily_snapshots"][-90:]

    save_portfolio(portfolio)
    return portfolio


if __name__ == "__main__":
    p = load_portfolio()
    print(f"创建时间: {p['created_at']}")
    print(f"现金: {p['cash']:,.0f}")
    print(f"持仓: {len(p['holdings'])} 只")
    print(f"历史交易: {len(p['trades'])} 笔")
