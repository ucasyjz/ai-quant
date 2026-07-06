"""
trading_calendar.py - A股交易日历

职责：判断某天是否为交易日，下一个交易日是什么时候。
A股规则：周一至周五交易，遇法定节假日休市。
当前实现基于周末检测 + 中国法定节假日硬编码。
"""

from datetime import datetime, timedelta, date


# 2026年中国法定节假日（沪深休市日）
# 数据来源：国务院办公厅通知，需每年更新
CN_HOLIDAYS_2026 = {
    # 元旦
    date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3),
    # 春节 (1月29日除夕) — 1月28日-2月3日休市
    date(2026, 1, 28), date(2026, 1, 29), date(2026, 1, 30),
    date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 2), date(2026, 2, 3),
    # 清明节 — 4月4日-4月6日
    date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6),
    # 劳动节 — 5月1日-5月5日
    date(2026, 5, 1), date(2026, 5, 2), date(2026, 5, 3),
    date(2026, 5, 4), date(2026, 5, 5),
    # 端午节 — 6月19日-6月21日
    date(2026, 6, 19), date(2026, 6, 20), date(2026, 6, 21),
    # 中秋节 — 9月25日-9月27日
    date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27),
    # 国庆节 — 10月1日-10月7日
    date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3),
    date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7),
}


def is_trading_day(d: date = None) -> bool:
    """
    判断是否为A股交易日。
    
    Args:
        d: 要检查的日期，默认今天
        
    Returns:
        True 表示今天开市，False 表示休市
    """
    if d is None:
        d = date.today()
    
    # 周六日休市
    if d.weekday() >= 5:
        return False
    
    # 法定节假日休市
    if d in CN_HOLIDAYS_2026:
        return False
    
    return True


def next_trading_day(d: date = None) -> date:
    """
    找到下一个交易日。
    
    Args:
        d: 参考日期（包含），默认今天
        
    Returns:
        下一个交易日的 date 对象
    """
    if d is None:
        d = date.today()
    
    # 先检查 d 本身是不是交易日
    if is_trading_day(d):
        return d
    
    # 往后找
    nd = d + timedelta(days=1)
    while not is_trading_day(nd):
        nd += timedelta(days=1)
    return nd


def prev_trading_day(d: date = None) -> date:
    """
    找到最近一个已过的交易日。
    
    Args:
        d: 参考日期（不包含），默认今天
        
    Returns:
        最近一个交易日的 date 对象
    """
    if d is None:
        d = date.today()
    
    nd = d - timedelta(days=1)
    while not is_trading_day(nd):
        nd -= timedelta(days=1)
    return nd


def trading_day_countdown(d: date = None) -> dict:
    """
    返回距下一个交易日的倒计时信息。
    
    Returns:
        dict with keys:
        - is_trading: 今天是否在交易中
        - next_date: 下一个交易日日期
        - prev_date: 上一个交易日日期
        - days_until: 距下一交易日还有几天
        - message: 人类可读的提示语
    """
    today = d or date.today()
    now = datetime.now()
    
    trading = is_trading_day(today)
    next_td = next_trading_day(today)
    prev_td = prev_trading_day(today)
    
    if trading:
        # 盘中：9:30-15:00
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            msg = "等待开盘 (9:30)"
        elif now.hour >= 15:
            msg = "今日已收盘"
        else:
            msg = "交易中"
    else:
        delta = (next_td - today).days
        if delta == 1:
            msg = f"明日 ({next_td.strftime('%m/%d')}) 开盘"
        else:
            msg = f"距下一交易日还有 {delta} 天 ({next_td.strftime('%m/%d')})"
    
    return {
        "is_trading": trading,
        "today": today.strftime("%Y-%m-%d"),
        "next_date": next_td.strftime("%Y-%m-%d"),
        "prev_date": prev_td.strftime("%Y-%m-%d"),
        "days_until": (next_td - today).days,
        "message": msg,
        "next_weekday": next_td.strftime("%A"),
    }


if __name__ == "__main__":
    info = trading_day_countdown()
    print(f"今天: {info['today']}")
    print(f"是否交易日: {info['is_trading']}")
    print(f"上一个交易日: {info['prev_date']}")
    print(f"下一个交易日: {info['next_date']}")
    print(f"信息: {info['message']}")
