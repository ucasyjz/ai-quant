"""
signal_engine.py - 多因子AI信号引擎

这是系统的核心：不是简单均线了，而是4个因子综合打分，
每只股票得到 -100 到 +100 的分数。

四个因子：
    1. 趋势因子 (30%) - MA均线位置 + 均线斜率，判断大方向
    2. 动量因子 (30%) - RSI + MACD + 价格动量，判断涨跌力度
    3. 量能因子 (20%) - 量比 + OBV趋势，判断资金参与度
    4. 均值回归 (20%) - 布林带位置，判断超买超卖

分数含义：
    +60 ~ +100  强烈买入  (多个因子共振看多)
    +20 ~ +60   建议买入
    -20 ~ +20   中性观望
    -60 ~ -20   建议卖出
    -100 ~ -60  强烈卖出
"""

import numpy as np
import pandas as pd
import json
import os

# ========== 策略参数（可被复盘模块动态调整）==========
PARAMS_FILE = "strategy_params.json"

# 默认权重（文件不存在时用这个）
W_TREND = 0.30
W_MOMENTUM = 0.30
W_VOLUME = 0.20
W_MEANREV = 0.20


def load_factor_weights():
    """
    从 strategy_params.json 读取当前因子权重。
    复盘模块会调整这个文件，让策略每天进化。
    文件不存在时用默认权重。
    """
    global W_TREND, W_MOMENTUM, W_VOLUME, W_MEANREV
    if os.path.exists(PARAMS_FILE):
        try:
            with open(PARAMS_FILE, "r", encoding="utf-8") as f:
                params = json.load(f)
            fw = params.get("factor_weights", {})
            W_TREND = fw.get("trend", 0.30)
            W_MOMENTUM = fw.get("momentum", 0.30)
            W_VOLUME = fw.get("volume", 0.20)
            W_MEANREV = fw.get("mean_reversion", 0.20)
        except Exception:
            pass
    return W_TREND, W_MOMENTUM, W_VOLUME, W_MEANREV


def _ema(series, span):
    """指数移动平均"""
    return series.ewm(span=span, adjust=False).mean()


def calc_trend_factor(df):
    """
    趋势因子：均线位置 + 趋势斜率

    逻辑：
        - 价格在MA20上方 → 趋势向上
        - MA5在MA20上方 → 短期强势
        - MA20斜率向上 → 中期趋势确立
        - 价格离MA20越远 → 趋势越强

    返回: -100 到 +100
    """
    close = df["close"]
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    score = pd.Series(0.0, index=df.index)

    # 1. 价格 vs MA20 (权重40%)
    price_above_ma20 = (close > ma20).astype(float)  # 1或0
    # 离MA20的距离（标准化）
    dev = (close - ma20) / ma20 * 100  # 百分比偏离
    dev_score = np.clip(dev * 5, -40, 40)  # 偏离8%打满
    score += price_above_ma20 * 20 + dev_score * 0.5

    # 2. MA5 vs MA20 金叉/死叉 (权重30%)
    ma5_above = (ma5 > ma20).astype(float)
    score += ma5_above * 30

    # 3. MA20斜率 (权重30%) - 5天前 vs 今天
    ma20_slope = (ma20 - ma20.shift(5)) / ma20.shift(5) * 100
    slope_score = np.clip(ma20_slope * 10, -30, 30)
    score += slope_score

    # 4. MA60长期趋势加分
    if ma60.iloc[-1] == ma60.iloc[-1]:  # 非NaN
        above_ma60 = (close.iloc[-1] > ma60.iloc[-1])
        if above_ma60:
            score.iloc[-1] += 10

    # 限制在 -100 到 100
    score = score.clip(-100, 100)
    return score


def calc_momentum_factor(df):
    """
    动量因子：RSI + MACD + 价格动量

    逻辑：
        - RSI > 50 多头，< 50 空头；> 70 超买，< 30 超卖
        - MACD柱 > 0 多头动能
        - 近期涨幅正动量

    返回: -100 到 +100
    """
    close = df["close"]
    score = pd.Series(0.0, index=df.index)

    # 1. RSI(14)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))

    # RSI评分: 50是中性, 70+超买(减分), 30-超卖(减分)
    # 最佳区间: 50-70 (温和多头)
    rsi_score = (rsi - 50) * 2  # 50→0, 70→+40, 30→-40
    rsi_score = np.clip(rsi_score, -50, 50)
    score += rsi_score * 0.4  # 权重40%

    # 2. MACD
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd = ema12 - ema26
    signal = _ema(macd, 9)
    macd_hist = macd - signal

    # MACD柱状图标准化
    macd_norm = macd_hist / (close * 0.01)  # 相对价格的百分比
    macd_score = np.clip(macd_norm * 20, -30, 30)
    score += macd_score * 0.3  # 权重30%

    # 3. 价格动量 (5日和20日收益率)
    ret_5d = close.pct_change(5) * 100
    ret_20d = close.pct_change(20) * 100

    mom_score = np.clip(ret_5d * 1.5 + ret_20d * 0.5, -30, 30)
    score += mom_score * 0.3  # 权重30%

    score = score.clip(-100, 100)
    return score


def calc_volume_factor(df):
    """
    量能因子：量比 + OBV趋势

    逻辑：
        - 放量上涨 → 资金进场 → 看多
        - 缩量上涨 → 无人跟风 → 谨慎
        - 放量下跌 → 资金出逃 → 看空
        - OBV趋势向上 → 累积资金流入

    返回: -100 到 +100
    """
    close = df["close"]
    volume = df["volume"]
    score = pd.Series(0.0, index=df.index)

    # 1. 量比 (今天成交量 / 20日平均成交量)
    vol_ma20 = volume.rolling(20).mean()
    vol_ratio = volume / (vol_ma20 + 1)

    # 量价关系: 涨+放量=多, 跌+放量=空, 涨+缩量=弱多
    daily_ret = close.pct_change()
    vol_price = daily_ret * np.log1p(vol_ratio) * 200  # 放大效果
    vol_price = np.clip(vol_price, -40, 40)
    score += vol_price * 0.5  # 权重50%

    # 2. OBV趋势 (On Balance Volume)
    obv = pd.Series(0.0, index=df.index)
    for i in range(1, len(df)):
        if close.iloc[i] > close.iloc[i - 1]:
            obv.iloc[i] = obv.iloc[i - 1] + volume.iloc[i]
        elif close.iloc[i] < close.iloc[i - 1]:
            obv.iloc[i] = obv.iloc[i - 1] - volume.iloc[i]
        else:
            obv.iloc[i] = obv.iloc[i - 1]

    # OBV 5日均线斜率
    obv_ma5 = obv.rolling(5).mean()
    obv_slope = (obv - obv_ma5) / (obv_ma5.abs() + 1) * 100
    obv_score = np.clip(obv_slope * 0.5, -40, 40)
    score += obv_score * 0.5  # 权重50%

    score = score.clip(-100, 100)
    return score


def calc_mean_reversion_factor(df):
    """
    均值回归因子：布林带位置

    逻辑：
        - 价格在布林带上轨附近 → 短期超买 → 减分
        - 价格在布林带下轨附近 → 短期超卖 → 加分
        - 中轨(MA20)附近 → 中性

    注意：这个因子跟趋势因子方向相反！
        趋势因子说"涨了是好事"，
        均值回归说"涨太多了要回调"。
        两者的张力构成了买卖信号的平衡。

    返回: -100 到 +100
    """
    close = df["close"]
    ma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()

    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20

    # 布林带位置百分比: 0%=下轨, 50%=中轨, 100%=上轨
    boll_pos = (close - lower) / (upper - lower + 1e-10) * 100

    # 评分逻辑:
    #   下轨附近(0-20%) → 超卖反弹预期 → 正分
    #   中轨(40-60%)    → 中性
    #   上轨附近(80-100%) → 超买回调预期 → 负分
    score = pd.Series(0.0, index=df.index)

    # 线性映射: 50% → 0分, 0% → +60分, 100% → -60分
    score = (50 - boll_pos) * 1.2
    score = score.clip(-60, 60)

    return score


def score_stock(df):
    """
    对单只股票进行多因子打分。

    参数:
        df - 含 close, volume, high, low 列的 DataFrame

    返回:
        dict: {
            'composite': 综合分数 (-100~100),
            'trend': 趋势分数,
            'momentum': 动量分数,
            'volume': 量能分数,
            'mean_reversion': 均值回归分数,
            'rsi': 当前RSI值,
            'boll_pos': 布林带位置,
            'signal': '强烈买入'|'买入'|'中性'|'卖出'|'强烈卖出',
            'confidence': 信心度 0-100%
        }
    """
    if len(df) < 60:
        return None

    # 每次打分前加载最新因子权重（复盘模块可能会调整）
    w_trend, w_mom, w_vol, w_mr = load_factor_weights()

    # 计算各因子
    trend = calc_trend_factor(df)
    momentum = calc_momentum_factor(df)
    volume = calc_volume_factor(df)
    meanrev = calc_mean_reversion_factor(df)

    # 取最新值
    t = trend.iloc[-1]
    m = momentum.iloc[-1]
    v = volume.iloc[-1]
    mr = meanrev.iloc[-1]

    # 加权综合分（权重可被复盘模块动态调整）
    composite = (t * w_trend + m * w_mom +
                 v * w_vol + mr * w_mr)

    # 计算RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rsi = (100 - (100 / (1 + avg_gain / (avg_loss + 1e-10)))).iloc[-1]

    # 布林带位置
    ma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20
    boll_pos = ((df["close"].iloc[-1] - lower.iloc[-1]) /
                (upper.iloc[-1] - lower.iloc[-1] + 1e-10) * 100)

    # 信号分类
    if composite >= 60:
        signal = "强烈买入"
        confidence = min(100, 60 + abs(composite) * 0.4)
    elif composite >= 20:
        signal = "买入"
        confidence = 40 + abs(composite) * 0.5
    elif composite > -20:
        signal = "中性"
        confidence = 30 + (20 - abs(composite)) * 1.0
    elif composite > -60:
        signal = "卖出"
        confidence = 40 + abs(composite) * 0.5
    else:
        signal = "强烈卖出"
        confidence = min(100, 60 + abs(composite) * 0.4)

    return {
        "composite": round(composite, 1),
        "trend": round(t, 1),
        "momentum": round(m, 1),
        "volume": round(v, 1),
        "mean_reversion": round(mr, 1),
        "rsi": round(rsi, 1),
        "boll_pos": round(boll_pos, 1),
        "signal": signal,
        "confidence": round(confidence, 1),
        "close": round(df["close"].iloc[-1], 2),
        "change_pct": round((df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100, 2),
    }


def score_universe(data_dict):
    """
    批量打分：对股票池里所有股票打分。

    参数:
        data_dict - {code: DataFrame} 每只股票的行情数据

    返回:
        排好序的列表，按综合分数从高到低
    """
    results = []
    for code, df in data_dict.items():
        s = score_stock(df)
        if s is None:
            continue
        s["code"] = code
        results.append(s)

    # 按综合分排序
    results.sort(key=lambda x: x["composite"], reverse=True)
    return results


if __name__ == "__main__":
    # 用模拟数据测试
    from data_fetcher import _generate_mock_data
    df = _generate_mock_data()
    result = score_stock(df)
    print("单股打分测试:")
    for k, v in result.items():
        print(f"  {k}: {v}")
