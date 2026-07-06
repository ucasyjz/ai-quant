"""
plotting.py - 可视化模块（含风控标注）

职责：画四张图
图1: 价格 + 均线 + 买卖点 + 风控触发点
图2: 资金曲线 + 熔断线
图3: 回撤曲线 + 熔断阈值线
图4: 单笔交易盈亏分布
"""

import os
import tempfile

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "mplconfig"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from config import INITIAL_CASH, MA_SHORT, MA_LONG, STOCK_CODE, STOCK_NAME
from config import MAX_PORTFOLIO_DRAWDOWN, STOP_LOSS, TAKE_PROFIT, TRAILING_STOP

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def plot_results(df, stats, save_path="backtest_result.png"):
    """画回测结果图（含风控标注）"""
    fig, axes = plt.subplots(4, 1, figsize=(14, 13),
                             gridspec_kw={"height_ratios": [3, 2, 1.5, 1.5]})

    risk_str = ""
    rs = stats.get("risk_stats", {})
    if rs:
        risk_str = (f"  |  止损{rs.get('stop_loss_hits',0)} 止盈{rs.get('take_profit_hits',0)} "
                    f"移止{rs.get('trailing_stop_hits',0)} 熔断{rs.get('circuit_breaker_hits',0)}")

    fig.suptitle(
        f"{STOCK_NAME}({STOCK_CODE}) 双均线 MA{MA_SHORT}/MA{MA_LONG} + 三层风控  |  "
        f"收益 {stats['total_return']:+.1f}% vs 持有 {stats['buy_hold_return']:+.1f}%  |  "
        f"回撤 {stats['max_drawdown']:.1f}% 夏普 {stats['sharpe']:.2f}"
        f"{risk_str}",
        fontsize=12, fontweight="bold", y=0.99
    )

    dates = df["date"]
    risk_events = stats.get("risk_events", [])

    # ========== 图1: 价格 + 均线 + 买卖点 + 风控点 ==========
    ax1 = axes[0]
    ax1.plot(dates, df["close"], color="#888780", linewidth=1, label="收盘价", alpha=0.8)
    ax1.plot(dates, df["ma_short"], color="#D85A30", linewidth=1.2, label=f"MA{MA_SHORT}", alpha=0.9)
    ax1.plot(dates, df["ma_long"], color="#185FA5", linewidth=1.2, label=f"MA{MA_LONG}", alpha=0.9)

    # 策略买卖点
    buys = df[df["trade"] == 1]
    sells = df[df["trade"] == -1]
    if len(buys) > 0:
        ax1.scatter(buys["date"], buys["close"], marker="^", color="#E24B4A",
                     s=80, zorder=5, label="策略买入", edgecolors="white", linewidths=0.5)
    if len(sells) > 0:
        ax1.scatter(sells["date"], sells["close"], marker="v", color="#1D9E75",
                     s=80, zorder=5, label="策略卖出", edgecolors="white", linewidths=0.5)

    # 风控触发点（用不同颜色和标记）
    if risk_events:
        risk_df = pd.DataFrame(risk_events)
        # 止损 - 橙色叉
        sl = risk_df[risk_df["type"] == "stop_loss"]
        if len(sl) > 0:
            ax1.scatter(sl["date"], sl["price"], marker="x", color="#BA7517",
                         s=120, zorder=6, label=f"止损({len(sl)})", linewidths=2)
        # 止盈 - 紫色星
        tp = risk_df[risk_df["type"] == "take_profit"]
        if len(tp) > 0:
            ax1.scatter(tp["date"], tp["price"], marker="*", color="#7F77DD",
                         s=150, zorder=6, label=f"止盈({len(tp)})", edgecolors="white", linewidths=0.5)
        # 移动止损 - 粉色叉
        ts = risk_df[risk_df["type"] == "trailing_stop"]
        if len(ts) > 0:
            ax1.scatter(ts["date"], ts["price"], marker="s", color="#D4537E",
                         s=80, zorder=6, label=f"移动止损({len(ts)})", edgecolors="white", linewidths=0.5)
        # 熔断 - 大红圈
        cb = risk_df[risk_df["type"] == "circuit_breaker"]
        if len(cb) > 0:
            ax1.scatter(cb["date"], cb["price"], marker="o", color="#E24B4A",
                         s=200, zorder=6, facecolors="none", linewidths=2,
                         label=f"熔断({len(cb)})")

    ax1.set_ylabel("价格 (元)")
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.9, ncol=2)
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=4))

    # ========== 图2: 资金曲线 ==========
    ax2 = axes[1]
    ax2.plot(dates, df["portfolio"], color="#534AB7", linewidth=1.5, label="策略资产(含风控)", zorder=3)
    buy_hold = INITIAL_CASH * (df["close"] / df["close"].iloc[0])
    ax2.plot(dates, buy_hold, color="#B4B2A9", linewidth=1.2, label="买入持有", linestyle="--", zorder=2)
    ax2.axhline(y=INITIAL_CASH, color="#888780", linewidth=0.8, linestyle=":", alpha=0.5)

    # 标注熔断触发点
    if risk_events:
        cb_events = [e for e in risk_events if e["type"] == "circuit_breaker"]
        for e in cb_events:
            idx = df[df["date"] == e["date"]].index
            if len(idx) > 0:
                ax2.axvline(x=e["date"], color="#E24B4A", linewidth=1, linestyle=":", alpha=0.4)

    ax2.set_ylabel("资产 (元)")
    ax2.legend(loc="upper left", fontsize=9, framealpha=0.9)
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=4))

    # ========== 图3: 回撤曲线 ==========
    ax3 = axes[2]
    running_max = df["portfolio"].cummax()
    drawdown = (df["portfolio"] - running_max) / running_max * 100
    ax3.fill_between(dates, drawdown, 0, color="#E24B4A", alpha=0.25)
    ax3.plot(dates, drawdown, color="#A32D2D", linewidth=1, label="回撤")

    # 画熔断阈值线
    ax3.axhline(y=-MAX_PORTFOLIO_DRAWDOWN * 100, color="#BA7517", linewidth=1.5,
                linestyle="--", alpha=0.7, label=f"熔断线({-MAX_PORTFOLIO_DRAWDOWN*100:.0f}%)")

    ax3.set_ylabel("回撤 (%)")
    ax3.set_xlabel("日期")
    ax3.legend(loc="lower left", fontsize=9, framealpha=0.9)
    ax3.grid(True, alpha=0.3)
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=4))

    # ========== 图4: 单笔交易盈亏 ==========
    ax4 = axes[3]
    trade_records = stats.get("trade_records", [])
    buys_list = [t for t in trade_records if t["action"] == "BUY"]
    sells_list = [t for t in trade_records if t["action"].startswith("SELL")]

    trade_pnls = []
    for i, s in enumerate(sells_list):
        if i < len(buys_list):
            pnl = (s["price"] - buys_list[i]["price"]) / buys_list[i]["price"] * 100
            trade_pnls.append(pnl)

    if trade_pnls:
        colors = ["#E24B4A" if p >= 0 else "#1D9E75" for p in trade_pnls]
        ax4.bar(range(len(trade_pnls)), trade_pnls, color=colors, alpha=0.7, edgecolor="white", linewidth=0.5)
        ax4.axhline(y=0, color="#888780", linewidth=0.8)
        ax4.axhline(y=-STOP_LOSS * 100, color="#BA7517", linewidth=1, linestyle="--", alpha=0.5, label=f"止损线({-STOP_LOSS*100:.0f}%)")
        ax4.axhline(y=TAKE_PROFIT * 100, color="#7F77DD", linewidth=1, linestyle="--", alpha=0.5, label=f"止盈线({TAKE_PROFIT*100:.0f}%)")
        ax4.set_ylabel("单笔盈亏 (%)")
        ax4.set_xlabel("交易序号")
        ax4.legend(loc="upper right", fontsize=8, framealpha=0.9)
        ax4.grid(True, alpha=0.3, axis="y")
    else:
        ax4.text(0.5, 0.5, "无完整交易记录", ha="center", va="center", transform=ax4.transAxes, fontsize=12, color="#888780")

    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    print(f"[图表] 已保存到: {save_path}")
    return save_path
