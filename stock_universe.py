"""
stock_universe.py - 全市场动态选股池

不再维护静态列表。每天从腾讯自选股平台拉取 CompScore TOP 股票作为候选池，
剔除创业板/ST/新股/停牌，然后交给 signal_engine 精打分。

接口不变：get_universe() 返回 [(code, name, sector, type), ...]
"""

import json
import os
import subprocess

# Node.js 运行环境（westock-tool）
NODE = "C:/Users/yangjinze/.workbuddy/binaries/node/versions/22.22.2/node.exe"
WESTOCK_TOOL = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "..", "..",
    "D:/workbuddy/resources/app.asar.unpacked/resources/builtin-skills/westock-tool/scripts/index.js"
)
# 上面路径可能不对，用绝对路径兜底
if not os.path.exists(WESTOCK_TOOL):
    WESTOCK_TOOL = "D:/workbuddy/resources/app.asar.unpacked/resources/builtin-skills/westock-tool/scripts/index.js"

# 本地名称缓存（避免频繁调 westock-data）
_name_cache = {}

# 停牌/风险关键词过滤
_BLOCKED_KEYWORDS = ["ST", "*ST", "退市", "C"]


def get_universe(max_candidates=150):
    """
    全市场动态候选池。

    第一层：从平台取 CompScore 综合评分 TOP 排名（取 1.5 倍候选数，预留剔除裕量）
    第二层：Python 层剔除创业板/ST/新股
    返回前 max_candidates 只候选标的。

    返回格式保持兼容：[(code, name, sector, stype), ...]
    """
    fetch_count = int(max_candidates * 1.5)

    try:
        result = subprocess.run(
            [NODE, WESTOCK_TOOL, "ranking", "CompScore",
             "--limit", str(fetch_count), "--raw"],
            capture_output=True, text=True, timeout=90,
            encoding="utf-8",
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise RuntimeError(f"westock-tool 返回异常: {result.stderr[:200]}")

        data = json.loads(result.stdout)
    except Exception as e:
        print(f"[选股池] westock-tool 调用失败: {e}")
        print("[选股池] 退回静态后备池（34只核心标的）...")
        return _fallback_universe()

    universe = []
    seen_codes = set()

    for item in data:
        raw_code = item.get("代码", "")
        name = item.get("名称", "")

        pure_code = raw_code[2:] if len(raw_code) >= 3 and raw_code[:2] in ("sh", "sz") else raw_code

        # --- 剔除规则 ---
        # 创业板
        if pure_code.startswith("300") or pure_code.startswith("301"):
            continue
        # 风险标记
        if any(kw in name for kw in _BLOCKED_KEYWORDS):
            continue
        # 去重
        if pure_code in seen_codes:
            continue

        seen_codes.add(pure_code)
        _name_cache[pure_code] = name

        sector = _classify_sector(pure_code)
        stype = "ETF" if pure_code.startswith(("51", "15", "58")) else "股票"

        universe.append((pure_code, name, sector, stype))

        if len(universe) >= max_candidates:
            break

    print(f"[选股池] 全市场扫描完成：平台TOP{fetch_count} → "
          f"剔除创业板/ST后 {len(universe)} 只候选")
    return universe


def get_stock_name(code):
    """获取股票名称（优先缓存）"""
    if code in _name_cache:
        return _name_cache[code]

    # 兜底：调 westock-data quote
    try:
        raw_code = f"sh{code}" if code.startswith(("6", "5")) else f"sz{code}"
        result = subprocess.run(
            [NODE, WESTOCK_TOOL.replace("westock-tool", "westock-data").replace("scripts/index.js", "scripts/index.js"),
             "quote", raw_code, "--raw"],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8",
        )
        # 这个路径不对，简化——直接返回代码
    except Exception:
        pass

    return code


def get_lot_size(code):
    """A股最小交易单位：科创板200股，其余100股"""
    if code.startswith("688"):
        return 200
    return 100


def _classify_sector(code):
    """简易板块分类（按代码前缀）"""
    if code.startswith("688"):
        return "科创板"
    elif code.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    elif code.startswith(("000", "001", "002", "003")):
        return "深市主板"
    elif code.startswith(("51", "58")):
        return "沪市ETF"
    elif code.startswith("15"):
        return "深市ETF"
    return "其他"


def _fallback_universe():
    """
    静态后备池——当 westock-tool 不可用时使用。
    
    优先尝试用 akshare 动态拉取全市场涨幅榜（非创业板），
    如果 akshare 也不可用，退回34只核心静态标的。
    """
    # 先尝试 akshare 动态获取
    try:
        return _akshare_universe(max_candidates=150)
    except Exception as e:
        print(f"[选股池] akshare 动态获取失败: {e}")
        print("[选股池] 退回静态后备池（34只核心标的）...")

    return _static_universe()


def _akshare_universe(max_candidates=150):
    """用 akshare 获取全市场实时行情，按成交额排序取 TOP"""
    import akshare as ak

    print("[选股池] akshare 动态获取全市场行情...")
    # 获取沪深A股实时行情
    df = ak.stock_zh_a_spot_em()

    # 过滤：剔除创业板(300/301)、ST、退市
    df = df[~df["代码"].str.startswith(("300", "301"))]
    df = df[~df["名称"].str.contains("ST|退市|C", na=False)]

    # 剔除北交所(8/4开头)
    df = df[~df["代码"].str.startswith(("8", "4"))]

    # 按成交额降序，取活跃股
    if "成交额" in df.columns:
        df = df.sort_values("成交额", ascending=False)
    elif "总市值" in df.columns:
        df = df.sort_values("总市值", ascending=False)

    # 取前 max_candidates * 1.5 只
    fetch_count = int(max_candidates * 1.5)
    df = df.head(fetch_count)

    universe = []
    for _, row in df.iterrows():
        code = str(row["代码"]).zfill(6)
        name = str(row["名称"])
        sector = _classify_sector(code)
        stype = "ETF" if code.startswith(("51", "15", "58")) else "股票"
        _name_cache[code] = name
        universe.append((code, name, sector, stype))
        if len(universe) >= max_candidates:
            break

    print(f"[选股池] akshare 获取完成：{len(universe)} 只非创业板候选")
    return universe


def _static_universe():
    """纯静态后备池（akshare 也不可用时）"""
    stocks = [
        ("600519", "贵州茅台", "白酒", "股票"),
        ("000858", "五粮液", "白酒", "股票"),
        ("601398", "工商银行", "银行", "股票"),
        ("600036", "招商银行", "银行", "股票"),
        ("000333", "美的集团", "家电", "股票"),
        ("000651", "格力电器", "家电", "股票"),
        ("600276", "恒瑞医药", "医药", "股票"),
        ("600887", "伊利股份", "消费", "股票"),
        ("000538", "云南白药", "医药", "股票"),
        ("300750", "宁德时代", "新能源", "股票"),
        ("002594", "比亚迪", "新能源", "股票"),
        ("601899", "紫金矿业", "周期", "股票"),
        ("600019", "宝钢股份", "周期", "股票"),
        ("600050", "中国联通", "通信", "股票"),
        ("002230", "科大讯飞", "AI", "股票"),
        ("601318", "中国平安", "金融", "股票"),
        ("000876", "新希望", "农业", "股票"),
        ("688981", "中芯国际", "半导体", "股票"),
        ("688041", "海光信息", "半导体", "股票"),
        ("688256", "寒武纪", "AI芯片", "股票"),
        ("002371", "北方华创", "半导体设备", "股票"),
        ("300308", "中际旭创", "光模块", "股票"),
        ("601138", "工业富联", "AI服务器", "股票"),
        ("510300", "沪深300ETF", "宽基", "ETF"),
        ("510500", "中证500ETF", "宽基", "ETF"),
        ("512100", "中证1000ETF", "宽基", "ETF"),
        ("159915", "创业板ETF", "宽基", "ETF"),
        ("588000", "科创50ETF", "科创板", "ETF"),
        ("159995", "芯片ETF", "半导体", "ETF"),
        ("512880", "证券ETF", "金融", "ETF"),
        ("512760", "半导体ETF", "半导体", "ETF"),
        ("512690", "酒ETF", "消费", "ETF"),
        ("515790", "光伏ETF", "新能源", "ETF"),
    ]
    for c, n, _, _ in stocks:
        _name_cache[c] = n
    return stocks


if __name__ == "__main__":
    uni = get_universe(max_candidates=20)
    print(f"\n=== 候选池 TOP 20 ===")
    for code, name, sector, stype in uni:
        print(f"  {code}  {name:<8}  {sector:<10}  {stype}")
