"""
strategy.py - 策略模块

职责：根据价格数据生成交易信号。

这里实现的是最经典的「双均线策略」：
    - MA5（5日均线）上穿 MA20（20日均线） -> 金叉 -> 买入
    - MA5 下穿 MA20 -> 死叉 -> 卖出

信号含义：
    signal = 1  表示这天持仓（满仓）
    signal = 0  表示这天空仓

为什么从简单策略开始？
    1. 逻辑透明，你能清楚地知道每一笔交易为什么发生
    2. 跑通整个链路（数据->策略->回测->出图）比策略本身更重要
    3. 有了基准，后面上AI才知道AI是在帮忙还是添乱
"""

import pandas as pd
from config import MA_SHORT, MA_LONG, USE_TREND_FILTER


def generate_signals(df):
    """
    计算均线并生成交易信号。

    参数:
        df - data_fetcher 返回的 DataFrame，必须包含 close 列

    返回:
        在 df 基础上增加以下列:
        - ma_short  - 短期均线
        - ma_long   - 长期均线
        - signal    - 交易信号（1=持仓, 0=空仓）
        - trade     - 当天是否发生交易（1=买入, -1=卖出, 0=无操作）
    """
    df = df.copy()

    # 计算移动平均线
    # rolling(N).mean() 就是最近 N 天收盘价的平均值
    df["ma_short"] = df["close"].rolling(MA_SHORT).mean()
    df["ma_long"] = df["close"].rolling(MA_LONG).mean()

    # 生成持仓信号
    # 基础条件: 短均线在长均线上方（ma_short > ma_long）
    # 这里用 shift(1) 是因为：今天的均线要用今天的收盘价算，
    # 但今天收盘前你不知道今天的收盘价，所以信号要延迟一天执行
    # 这是避免「未来函数」的关键 —— 用昨天能知道的信息，决定今天的操作
    df["signal"] = (df["ma_short"] > df["ma_long"]).astype(int)

    # 趋势过滤: 用5日变化判断方向，比单日更平滑，避免噪音导致频繁进出
    # 只有当MA20在最近5天内整体上升时，才认为趋势向上
    if USE_TREND_FILTER:
        trend_up = (df["ma_long"] > df["ma_long"].shift(5)).astype(int)
        df["signal"] = df["signal"] * trend_up

    df["signal"] = df["signal"].shift(1).fillna(0)  # 延迟一天

    # 标记交易点：信号从0变1是买入，从1变0是卖出
    df["trade"] = 0
    df.loc[(df["signal"] == 1) & (df["signal"].shift(1) == 0), "trade"] = 1    # 买入
    df.loc[(df["signal"] == 0) & (df["signal"].shift(1) == 1), "trade"] = -1   # 卖出

    # 统计交易次数
    buy_count = (df["trade"] == 1).sum()
    sell_count = (df["trade"] == -1).sum()
    print(f"[策略] 双均线策略 MA{MA_SHORT}/MA{MA_LONG}")
    print(f"       买入信号: {buy_count} 次, 卖出信号: {sell_count} 次")

    return df


if __name__ == "__main__":
    from data_fetcher import fetch_data
    df = fetch_data()
    df = generate_signals(df)
    print("\n有交易的日期:")
    trades = df[df["trade"] != 0][["date", "close", "ma_short", "ma_long", "trade"]]
    print(trades.head(20))
