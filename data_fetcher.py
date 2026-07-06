"""
data_fetcher.py - 数据获取模块

职责：获取A股日K数据，整理成标准格式，存本地缓存。
调 fetch_data(stock_code) 即可，支持多只股票。

数据源优先级：
    1. 本地缓存（最快）
    2. 腾讯自选股 westock-data（Node 脚本，沙箱环境首选）
    3. akshare / 东方财富（用户本机运行）
    4. 模拟数据（兜底）

数据格式（每一行代表一天的行情）:
    date    - 交易日期
    open    - 开盘价
    close   - 收盘价
    high    - 最高价
    low     - 最低价
    volume  - 成交量（手）
"""

import os
import re
import subprocess
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
from config import STOCK_CODE, START_DATE, END_DATE, ADJUST, DATA_CACHE

# westock-data Node.js 脚本路径（WorkBuddy 内置 skill）
_NODE_BIN = os.path.expanduser(
    r"~\\.workbuddy\\binaries\\node\\versions\\22.22.2\\node.exe"
)
_WESTOCK_SCRIPT = (
    r"D:\\workbuddy\\resources\\app.asar.unpacked"
    r"\\resources\\builtin-skills\\westock-data\\scripts\\index.js"
)


def _code_to_westock_symbol(code):
    """将 6 位股票代码转成 westock-data 的 sh/sz 前缀格式"""
    if not code or len(code) < 6:
        return None
    first = code[0]
    if first in ("6", "5"):
        return f"sh{code}"
    elif first in ("0", "3", "1"):
        return f"sz{code}"
    return None


def _run_westock(args, timeout=30):
    """调用 westock-data Node 脚本，返回 stdout 字符串"""
    if not os.path.exists(_NODE_BIN) or not os.path.exists(_WESTOCK_SCRIPT):
        raise RuntimeError("westock-data script not found")
    cmd = [_NODE_BIN, _WESTOCK_SCRIPT] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"westock-data exited {result.returncode}: {result.stderr[:200]}")
    return result.stdout


def _parse_markdown_table(text):
    """
    将 westock-data 输出的 pipe-delimited markdown 表格解析为 list[dict]。

    表格格式示例：
        | col1 | col2 |
        | --- | --- |
        | val1 | val2 |
    """
    lines = text.strip().split("\n")
    rows = []
    header = None
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        # 跳过分隔行 (|---|---|)
        if re.match(r"^\|[\s\-:]+\|", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if header is None:
            header = cells
        else:
            if len(cells) == len(header):
                rows.append(dict(zip(header, cells)))
    return rows


def _fetch_westock_kline(code):
    """通过 westock-data 获取 K 线数据，返回标准 DataFrame"""
    symbol = _code_to_westock_symbol(code)
    if symbol is None:
        raise ValueError(f"无法识别代码: {code}")

    # 日期格式转换：20220101 -> 2022-01-01
    start = f"{START_DATE[:4]}-{START_DATE[4:6]}-{START_DATE[6:8]}"
    end = f"{END_DATE[:4]}-{END_DATE[4:6]}-{END_DATE[6:8]}"

    print(f"[数据] 腾讯自选股拉取 {code} ({symbol}) K线...")
    print(f"       范围: {start} ~ {end}, 前复权")

    stdout = _run_westock([
        "kline", symbol,
        "--period", "day",
        "--fq", "qfq",
        "--start", start,
        "--end", end,
    ])

    rows = _parse_markdown_table(stdout)
    if not rows:
        raise RuntimeError("westock-data 返回空数据")

    df = pd.DataFrame(rows)

    # 重命名列：westock 用 "last" 表示收盘价，"amount" 是成交额，"exchange" 是换手率
    col_map = {
        "date": "date",
        "open": "open",
        "last": "close",
        "high": "high",
        "low": "low",
        "volume": "volume",
    }
    df = df.rename(columns=col_map)

    # 只保留标准列
    std_cols = ["date", "open", "close", "high", "low", "volume"]
    df = df[[c for c in std_cols if c in df.columns]].copy()

    # 类型转换
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "close", "high", "low", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("date").reset_index(drop=True)

    print(f"[数据] {code} 拉取完成，共 {len(df)} 个交易日")
    return df


def _generate_mock_data(stock_code=None):
    """
    生成模拟A股日K数据（几何布朗运动模型）。

    当所有真实数据源都不可用时兜底。
    """
    code = stock_code or STOCK_CODE
    print(f"[数据] 所有数据源不可用，{code} 使用模拟数据...")
    dates = pd.bdate_range(START_DATE, END_DATE)
    n = len(dates)

    # 用代码的数字部分作为种子，确保同一只股票每次生成一样的数据
    seed_val = int(code[:6].lstrip('0') or '0') % 100000
    np.random.seed(seed_val + 42)

    # 根据股票代码给一个合理的初始价格
    price_map = {"510300": 4.0, "600519": 1700.0, "000001": 12.0, "300750": 500.0,
                 "601398": 5.5, "600036": 38.0, "000858": 160.0, "002594": 260.0,
                 "002475": 38.0, "600276": 48.0, "000538": 58.0, "600887": 30.0,
                 "000651": 42.0, "601899": 14.0, "600019": 7.5, "600050": 5.5,
                 "002230": 48.0, "601318": 48.0, "000333": 58.0, "000876": 13.0,
                 "512880": 1.1, "512760": 1.3, "512690": 1.6, "512010": 0.55,
                 "515030": 1.9, "515790": 1.4, "510500": 6.5, "512100": 2.8,
                 "159915": 3.2, "000725": 5.0}
    initial_price = price_map.get(code, 10.0)
    drift = 0.0002
    volatility = 0.012 if code.startswith("51") or code.startswith("15") else 0.018

    # 生成收盘价序列
    returns = np.random.normal(drift, volatility, n)
    close = initial_price * np.cumprod(1 + returns)

    # 根据收盘价生成 OHLV
    open_prices = close * (1 + np.random.normal(0, 0.003, n))
    high = np.maximum(close, open_prices) * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = np.minimum(close, open_prices) * (1 - np.abs(np.random.normal(0, 0.005, n)))
    volume = np.random.randint(300000, 3000000, n).astype(float)

    df = pd.DataFrame({
        "date": dates[:n],
        "open": np.round(open_prices, 3),
        "close": np.round(close, 3),
        "high": np.round(high, 3),
        "low": np.round(low, 3),
        "volume": volume,
    })
    print(f"[数据] 模拟数据生成完成，共 {len(df)} 个交易日")
    return df


def _is_data_fresh(df):
    """
    检查缓存数据是否新鲜：最后一条数据的日期必须是今天或最近交易日。

    这是防止"价格错误"的核心防线：
    - 如果今天是交易日，数据最后日期必须是今天
    - 如果今天是周末/节假日，数据最后日期必须是上个交易日
    - 无论如何，数据最后日期不能比3天前更早（超过3天一定有问题）
    """
    from datetime import datetime, timedelta
    last_date = pd.Timestamp(df["date"].iloc[-1])
    today = pd.Timestamp(datetime.now().date())

    # 如果最后数据日期就是今天 → 新鲜
    if last_date >= today:
        return True

    # 如果最后数据日期是昨天或前天（可能周末/节假日没数据） → 也算新鲜
    if last_date >= today - timedelta(days=3):
        # 但要确认：如果今天是交易日，数据不应该停留在3天前
        # 简单判断：周一到周五是可能的交易日，周六周日不是
        if today.weekday() < 5:  # 今天是工作日
            # 如果今天是周一，上周五的数据可以接受（周末没数据）
            # 如果今天是周二到周五，数据不应该停留在2天前
            gap = (today - last_date).days
            if today.weekday() == 0:  # 周一
                return gap <= 3  # 上周五的数据可以
            else:
                return gap <= 1  # 工作日最多容忍1天gap（T+1延迟）
        else:  # 周末
            return True  # 周末没新数据，上个周五的数据就行
    return False


def fetch_data(stock_code=None):
    """
    获取A股日K数据。

    Args:
        stock_code: 股票代码，如 "600519"。不传则用 config.STOCK_CODE

    数据源优先级：本地缓存 > 腾讯自选股 > akshare > 模拟数据
    
    缓存策略（三重防线防止价格错误）：
        1. 数据新鲜度校验：最后一条数据日期必须是今天/最近交易日，否则强制重新拉取
        2. 文件时间校验：缓存文件超过30分钟也重新拉取
        3. 两者都通过才用缓存，否则重新拉取
    """
    code = stock_code or STOCK_CODE

    # 每只股票独立缓存
    cache_file = f"cache_{code}.csv"

    # 优先读缓存（三重防线）
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file, parse_dates=["date"])
        
        # 防线1: 数据新鲜度校验 —— 最后一条数据日期必须是最近交易日
        if not _is_data_fresh(df):
            last_date = str(df["date"].iloc[-1])[:10]
            print(f"[数据] {code} 缓存数据过期（最后日期={last_date}），强制重新拉取")
        else:
            # 防线2: 文件时间校验 —— 超过30分钟也重新拉
            import time
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime < 1800:
                print(f"[数据] {code} 缓存新鲜且未过期，直接使用")
                return df
            else:
                print(f"[数据] {code} 缓存已超30分钟，重新拉取以获取最新价")

    # 尝试1: 腾讯自选股 westock-data
    try:
        df = _fetch_westock_kline(code)
        if df is not None and len(df) >= 10:
            df.to_csv(cache_file, index=False)
            print(f"[数据] {code} 已缓存到: {cache_file}")
            return df
    except Exception as e:
        print(f"[数据] {code} westock-data 失败: {type(e).__name__}: {e}")

    # 尝试2: akshare / 东方财富
    try:
        import akshare as ak
        print(f"[数据] 从东方财富拉取 {code} 日K数据...")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=START_DATE,
            end_date=END_DATE,
            adjust=ADJUST,
        )

        rename_map = {
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
        }
        df = df.rename(columns=rename_map)
        cols = ["date", "open", "close", "high", "low", "volume"]
        df = df[[c for c in cols if c in df.columns]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        df.to_csv(cache_file, index=False)
        print(f"[数据] {code} 拉取完成，共 {len(df)} 个交易日")
        print(f"       已缓存到: {cache_file}")
        return df

    except Exception as e:
        print(f"[数据] {code} akshare 失败: {type(e).__name__}")

    # 尝试3: 模拟数据兜底
    return _generate_mock_data(code)


# --- 批量并行拉取 ---

_fetch_lock = threading.Lock()


def _fetch_one(code):
    """单只股票的 K 线拉取（线程安全，加锁防止打印混乱）"""
    try:
        df = fetch_data(code)
        if df is not None and len(df) >= 10:
            close_price = df["close"].iloc[-1]
            open_price = df["open"].iloc[-1]
            with _fetch_lock:
                print(f"  [{code}] close={close_price:.2f} open={open_price:.2f}")
            return code, df, close_price, open_price
    except Exception as e:
        with _fetch_lock:
            print(f"  [{code}] 失败: {e}")
    return code, None, None, None


def fetch_batch_kline(codes, max_workers=10):
    """
    并行拉取多只股票的 K 线数据。

    Args:
        codes: 股票代码列表
        max_workers: 并发线程数（默认 10）

    Returns:
        data_dict: {code: DataFrame}   — 成功拉取的 K 线
        prices:    {code: float}       — 最新收盘价（用于估值和卖出）
        open_prices: {code: float}     — 最新开盘价（用于买入）
    """
    print(f"[数据] 并行拉取 {len(codes)} 只候选标的数据 (并发{max_workers})...")
    data_dict = {}
    prices = {}
    open_prices = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, code): code for code in codes}
        for future in as_completed(futures):
            code, df, close_price, open_price = future.result()
            if df is not None:
                data_dict[code] = df
                prices[code] = close_price
                open_prices[code] = open_price

    print(f"[数据] 并行拉取完成: {len(data_dict)}/{len(codes)} 成功")
    return data_dict, prices, open_prices


def get_live_price(code):
    """
    获取单只股票的最新实时价格（不走缓存，实时查询）。

    Args:
        code: 6 位股票代码
    Returns:
        float: 最新价，失败返回 None
    """
    symbol = _code_to_westock_symbol(code)
    if symbol is None:
        return None
    try:
        stdout = _run_westock(["quote", symbol], timeout=15)
        rows = _parse_markdown_table(stdout)
        if rows and "price" in rows[0]:
            return float(rows[0]["price"])
    except Exception:
        pass
    return None


if __name__ == "__main__":
    # 测试：拉工商银行
    df = fetch_data("601398")
    print("\n前5行数据预览:")
    print(df.head())
    print(f"\n最新价: {df['close'].iloc[-1]:.2f}")
    print(f"数据范围: {df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")
    print(f"共 {len(df)} 条记录")
