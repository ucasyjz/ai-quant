"""
backtest.py - 回测引擎（含风控）

职责：拿着策略信号 + 风控规则，模拟真实交易，计算资金曲线和绩效指标。

风控介入时机（每天循环里的顺序）：
    1. 先算当天资产，检查组合熔断 -> 触发则强制清仓
    2. 如果持仓，检查个股止损止盈 -> 触发则强制卖出
    3. 如果策略给出买入信号，检查是否允许买入（冷静期/涨停）
    4. 如果策略给出卖出信号，检查是否允许卖出（跌停）

绩效指标说明：
    - 总收益率 / 年化收益率 / 最大回撤 / 夏普比率
    - 胜率（盈利交易占比）
    - 风控统计：止损/止盈/移动止损/熔断各触发多少次
"""

import numpy as np
import pandas as pd
from config import INITIAL_CASH, COMMISSION_RATE, STAMP_TAX_RATE, SLIPPAGE
from risk_manager import RiskManager


def run_backtest(df):
    """
    执行回测（含三层风控）。
    """
    df = df.copy()
    n = len(df)

    position = 0
    cash = INITIAL_CASH
    portfolio = []
    trade_records = []
    buy_prices = []

    # 风控管理器
    rm = RiskManager()

    # 记录风控触发事件（用于画图标注）
    risk_events = []  # [{date, type, price}]

    for i in range(n):
        row = df.iloc[i]
        close = row["close"]
        signal = row["signal"]
        trade_signal = row["trade"]
        prev_close = df.iloc[i - 1]["close"] if i > 0 else close

        # ==================== 第1步：组合熔断检查 ====================
        # 先算当前资产（用昨天收盘价估，因为今天还没交易）
        current_value = cash + position * close

        if rm.check_circuit_breaker(current_value):
            # 熔断触发：强制清仓
            if position > 0:
                sell_price = close * (1 - SLIPPAGE)
                revenue = position * sell_price
                commission = max(revenue * COMMISSION_RATE, 5)
                stamp_tax = revenue * STAMP_TAX_RATE
                cash += (revenue - commission - stamp_tax)
                trade_records.append({
                    "date": row["date"], "action": "SELL_CB",
                    "price": round(sell_price, 3), "shares": position,
                    "reason": "circuit_breaker",
                })
                risk_events.append({"date": row["date"], "type": "circuit_breaker", "price": close})
                position = 0
                rm.on_sell()

        # ==================== 第2步：个股止损止盈检查 ====================
        if position > 0:
            force_sell = rm.check_exit(close, prev_close)
            if force_sell:
                # 判断是哪种触发
                pnl = (close - rm.entry_price) / rm.entry_price
                if pnl <= -0.08:
                    reason = "stop_loss"
                elif pnl >= 0.25:
                    reason = "take_profit"
                else:
                    reason = "trailing_stop"

                sell_price = close * (1 - SLIPPAGE)
                revenue = position * sell_price
                commission = max(revenue * COMMISSION_RATE, 5)
                stamp_tax = revenue * STAMP_TAX_RATE
                cash += (revenue - commission - stamp_tax)
                trade_records.append({
                    "date": row["date"], "action": "SELL_RISK",
                    "price": round(sell_price, 3), "shares": position,
                    "reason": reason,
                })
                risk_events.append({"date": row["date"], "type": reason, "price": close})
                position = 0
                rm.on_sell()

        # ==================== 第3步：策略信号执行 ====================
        if trade_signal == 1 and position == 0:
            # 买入信号 -> 检查风控是否允许
            allowed, reason = rm.check_entry(close, prev_close, current_value)
            if allowed:
                buy_price = close * (1 + SLIPPAGE)
                # 仓位控制：只用总资金的一定比例
                buyable_cash = rm.get_position_size(cash)
                shares = int(buyable_cash / buy_price / 100) * 100
                if shares > 0:
                    cost = shares * buy_price
                    commission = max(cost * COMMISSION_RATE, 5)
                    cash -= (cost + commission)
                    position = shares
                    rm.on_buy(buy_price)
                    buy_prices.append(buy_price)
                    trade_records.append({
                        "date": row["date"], "action": "BUY",
                        "price": round(buy_price, 3), "shares": shares,
                    })

        elif trade_signal == -1 and position > 0:
            # 卖出信号 -> 检查是否跌停
            if rm.check_limit_down_sell(close, prev_close):
                sell_price = close * (1 - SLIPPAGE)
                revenue = position * sell_price
                commission = max(revenue * COMMISSION_RATE, 5)
                stamp_tax = revenue * STAMP_TAX_RATE
                cash += (revenue - commission - stamp_tax)
                trade_records.append({
                    "date": row["date"], "action": "SELL",
                    "price": round(sell_price, 3), "shares": position,
                    "reason": "signal",
                })
                position = 0
                rm.on_sell()

        # ==================== 记录当天资产 ====================
        total = cash + position * close
        portfolio.append(total)

    df["portfolio"] = portfolio

    # ==================== 计算绩效 ====================
    df["daily_return"] = df["portfolio"].pct_change().fillna(0)

    final_value = df["portfolio"].iloc[-1]
    total_return = (final_value / INITIAL_CASH - 1) * 100
    trading_days = len(df)
    annual_return = ((final_value / INITIAL_CASH) ** (250 / trading_days) - 1) * 100

    running_max = df["portfolio"].cummax()
    drawdown = (df["portfolio"] - running_max) / running_max
    max_drawdown = drawdown.min() * 100

    rf_daily = 0.03 / 250
    excess_return = df["daily_return"] - rf_daily
    if df["daily_return"].std() > 0:
        sharpe = np.sqrt(250) * excess_return.mean() / df["daily_return"].std()
    else:
        sharpe = 0

    buy_hold_return = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100

    # 胜率
    win_rate = 0
    sells = [t for t in trade_records if t["action"].startswith("SELL")]
    wins = sum(1 for i, s in enumerate(sells) if i < len(buy_prices) and s["price"] > buy_prices[i])
    win_rate = (wins / len(sells) * 100) if sells else 0

    # 单笔最大盈利和亏损
    trade_pnls = []
    for i, s in enumerate(sells):
        if i < len(buy_prices):
            pnl = (s["price"] - buy_prices[i]) / buy_prices[i] * 100
            trade_pnls.append(pnl)
    max_win = max(trade_pnls) if trade_pnls else 0
    max_loss = min(trade_pnls) if trade_pnls else 0
    avg_pnl = np.mean(trade_pnls) if trade_pnls else 0

    # 风控统计
    risk_stats = rm.get_stats()

    stats = {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "n_trades": len(trade_records),
        "buy_hold_return": buy_hold_return,
        "final_value": final_value,
        "trading_days": trading_days,
        "trade_records": trade_records,
        "risk_events": risk_events,
        "risk_stats": risk_stats,
        "max_win": max_win,
        "max_loss": max_loss,
        "avg_pnl": avg_pnl,
        "n_complete_trades": len(trade_pnls),
    }

    return df, stats


def print_report(stats):
    """打印绩效报告（含风控统计）"""
    print("\n" + "=" * 55)
    print("           回测绩效报告（含风控）")
    print("=" * 55)
    print(f"  回测天数:       {stats['trading_days']} 天")
    print(f"  初始资金:       {INITIAL_CASH:,.0f} 元")
    print(f"  最终资产:       {stats['final_value']:,.2f} 元")
    print(f"  完整交易:       {stats['n_complete_trades']} 笔")
    print(f"  胜率:           {stats['win_rate']:.1f}%")
    print(f"  单笔最大盈利:   {stats['max_win']:+.2f}%")
    print(f"  单笔最大亏损:   {stats['max_loss']:+.2f}%")
    print(f"  平均每笔:       {stats['avg_pnl']:+.2f}%")
    print("-" * 55)
    print(f"  策略总收益:     {stats['total_return']:+.2f}%")
    print(f"  年化收益:       {stats['annual_return']:+.2f}%")
    print(f"  最大回撤:       {stats['max_drawdown']:.2f}%")
    print(f"  夏普比率:       {stats['sharpe']:.2f}")
    print("-" * 55)
    print(f"  买入持有收益:   {stats['buy_hold_return']:+.2f}%")
    print(f"  超额收益:       {stats['total_return'] - stats['buy_hold_return']:+.2f}%")

    # 风控统计
    rs = stats["risk_stats"]
    print("-" * 55)
    print("  [风控触发统计]")
    print(f"    止损触发:     {rs['stop_loss_hits']} 次")
    print(f"    止盈触发:     {rs['take_profit_hits']} 次")
    print(f"    移动止损:     {rs['trailing_stop_hits']} 次")
    print(f"    组合熔断:     {rs['circuit_breaker_hits']} 次")
    print(f"    涨停拦截:     {rs['limit_up_blocks']} 次")
    print(f"    跌停拦截:     {rs['limit_down_blocks']} 次")
    print("=" * 55)

    # 评价
    print("\n[评价]")
    if stats["sharpe"] > 1:
        print("  夏普 > 1，风险调整后收益不错")
    elif stats["sharpe"] > 0:
        print("  夏普 > 0但偏低，策略有一定效果但风险较高")
    else:
        print("  夏普 <= 0，策略不如无风险利率，需改进")

    if stats["total_return"] > stats["buy_hold_return"]:
        print("  策略跑赢买入持有")
    else:
        print("  策略跑输买入持有，选时拖了后腿")

    if abs(stats["max_drawdown"]) > 20:
        print(f"  最大回撤 {stats['max_drawdown']:.1f}% 仍较大，考虑收紧风控参数")
    elif abs(stats["max_drawdown"]) > 10:
        print(f"  最大回撤 {stats['max_drawdown']:.1f}% 可接受")
    else:
        print(f"  最大回撤 {stats['max_drawdown']:.1f}% 控制良好")

    total_risk = rs["stop_loss_hits"] + rs["take_profit_hits"] + rs["trailing_stop_hits"] + rs["circuit_breaker_hits"]
    if total_risk > 0:
        print(f"  风控共介入 {total_risk} 次，避免了更大亏损")
    print()
