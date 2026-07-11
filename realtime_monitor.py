"""
realtime_monitor.py - A股盘中自动交易系统（模拟盘）

功能：
  1. 每60秒拉取持仓+候选股的实时行情
  2. 自动卖出：亏损>5%硬止损（遵守T+1）、评分跌破卖出线
  3. 自动买入：候选股盘中突破且评分达标
  4. 严格遵守A股规则：T+1、100股手数、涨跌停过滤、交易时间限制

A股规则（必须遵守）：
  - T+1：今天买的明天才能卖
  - 主板100股1手，科创板200股1手
  - 涨跌停不交易（涨停买不进，跌停卖不出）
  - 只在交易时间内操作（9:30-11:30, 13:00-15:00）
  - 印花税卖出单向千一，佣金双向万三

用法：
  python realtime_monitor.py              # 盘中持续监控+自动交易（本地）
  python realtime_monitor.py --cloud      # 云端模式（GitHub Actions用，akshare数据源+git提交）
  python realtime_monitor.py --interval 30  # 30秒刷新
  python realtime_monitor.py --once        # 只跑一次（测试用）
  python realtime_monitor.py --force       # 非交易时间也运行
"""

import os
import sys
import json
import time
import shutil
import subprocess
from datetime import datetime, timezone, timedelta

os.environ["PYTHONIOENCODING"] = "utf-8"

# ============ 北京时区 ============
BEIJING_TZ = timezone(timedelta(hours=8))

def now_beijing():
    """获取北京时间（无论运行在哪里都正确）"""
    return datetime.now(BEIJING_TZ)

def today_str_beijing():
    return now_beijing().strftime("%Y-%m-%d")

# ============ A股交易规则配置 ============
STOP_LOSS_PCT = -5.0       # 硬止损 -5%（盘中亏损达5%立刻卖出）
TAKE_PROFIT_PCT = 10.0     # 止盈 +10%
SURGE_PCT = 1.0            # 候选股盘中涨幅>1% → 触发买入评估（原3%太严，两天0交易）
BUY_MIN_SCORE = 25         # 买入最低评分（原30太严，与收盘扫描门槛对齐）
MAX_POSITIONS = 5          # 最多同时持有5只
MAX_SINGLE_RATIO = 0.25    # 单只最多25%仓位
COMMISSION_RATE = 0.0003   # 佣金 万三
STAMP_TAX_RATE = 0.001     # 印花税 千一（卖出单向）
SLIPPAGE = 0.001           # 滑点 0.1%

DEFAULT_INTERVAL = 60      # 默认刷新间隔(秒)
CLOUD_INTERVAL = 120       # 云端模式刷新间隔(秒)，减少API压力

SINA_API = "http://hq.sinajs.cn/list={codes}"
HEADERS = {"Referer": "https://finance.sina.com.cn"}

# ============ A股规则函数 ============

def get_lot_size(code):
    """A股最小交易单位：科创板200股，其余100股"""
    if code.startswith("688"):
        return 200
    return 100


def get_limit_pct(code):
    """涨跌停幅度：科创板/创业板 ±20%，主板 ±10%"""
    if code.startswith("688") or code.startswith("300"):
        return 20.0
    return 10.0


def is_t1_blocked(holding, today_str):
    """T+1规则：今天买的不能今天卖"""
    buy_date = holding.get("buy_date", "")
    if buy_date == today_str:
        return True
    return False


def is_at_limit_up(code, quote):
    """涨停检测：当前价接近涨停价就不买入（买不进还白交手续费）"""
    limit_pct = get_limit_pct(code)
    prev_close = quote.get("prev_close", 0)
    current = quote.get("current", 0)
    if prev_close <= 0:
        return False
    pct = (current / prev_close - 1) * 100
    return pct >= (limit_pct - 0.5)


def is_at_limit_down(code, quote):
    """跌停检测：当前价接近跌停价就不卖出（卖不出）"""
    limit_pct = get_limit_pct(code)
    prev_close = quote.get("prev_close", 0)
    current = quote.get("current", 0)
    if prev_close <= 0:
        return False
    pct = (current / prev_close - 1) * 100
    return pct <= -(limit_pct - 0.5)


def is_trading_hours():
    """检查是否在A股交易时间内（使用北京时间）"""
    now = now_beijing()
    if now.weekday() >= 5:
        return False, "周末非交易日"
    hour_min = now.hour * 100 + now.minute
    if hour_min < 930:
        return False, f"尚未开盘(9:30)，当前 {now.strftime('%H:%M')}"
    if hour_min > 1505:
        return False, f"已收盘(15:00)，当前 {now.strftime('%H:%M')}"
    if 1130 < hour_min < 1300:
        return False, f"午间休市(11:30-13:00)，当前 {now.strftime('%H:%M')}"
    return True, now.strftime("%H:%M:%S")


def should_stop_cloud():
    """云端模式：15:10后自动停止"""
    now = now_beijing()
    hour_min = now.hour * 100 + now.minute
    return hour_min >= 1510


# ============ 行情拉取 ============

def sina_code(code):
    """转换新浪格式: 688266 -> sh688266, 001309 -> sz001309"""
    if code.startswith(("6", "5")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def fetch_realtime_sina(codes):
    """从新浪财经拉取实时行情"""
    sina_codes = ",".join([sina_code(c) for c in codes])
    url = SINA_API.format(codes=sina_codes)
    try:
        import requests
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "gbk"
    except Exception as e:
        print(f"  [新浪行情错误] {e}")
        return {}

    result = {}
    for line in resp.text.strip().split("\n"):
        if '=""' in line or len(line) < 20:
            continue
        try:
            code_part = line.split("=")[0].split("_")[-1]
            code = code_part[2:]
            data = line.split('"')[1].split(",")
            name = data[0]
            open_p = float(data[1])
            prev_close = float(data[2])
            current = float(data[3])
            high = float(data[4])
            low = float(data[5])
            volume = int(float(data[8]))
            amount = float(data[9])
            pct = (current / prev_close - 1) * 100 if prev_close > 0 else 0

            result[code] = {
                "name": name,
                "open": open_p,
                "prev_close": prev_close,
                "current": current,
                "high": high,
                "low": low,
                "volume": volume,
                "amount": amount,
                "pct": round(pct, 2),
            }
        except (IndexError, ValueError, ZeroDivisionError):
            continue

    return result


def fetch_realtime_akshare(codes):
    """从akshare(东方财富)拉取实时行情 — 云端模式首选，国际可达"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
    except Exception as e:
        print(f"  [akshare行情错误] {e}")
        return {}

    result = {}
    for code in codes:
        row = df[df["代码"] == code]
        if len(row) == 0:
            continue
        r = row.iloc[0]
        current = float(r.get("最新价", 0))
        if current <= 0:
            continue
        prev_close = float(r.get("昨收", 0))
        if prev_close <= 0:
            # 从涨跌幅反推
            pct_val = float(r.get("涨跌幅", 0))
            prev_close = current / (1 + pct_val / 100) if pct_val != -100 else current

        pct = (current / prev_close - 1) * 100 if prev_close > 0 else 0
        result[code] = {
            "name": str(r.get("名称", code)),
            "open": float(r.get("今开", 0)),
            "prev_close": prev_close,
            "current": current,
            "high": float(r.get("最高", 0)),
            "low": float(r.get("最低", 0)),
            "volume": int(float(r.get("成交量", 0))),
            "amount": float(r.get("成交额", 0)),
            "pct": round(pct, 2),
        }

    return result


def fetch_realtime(codes, use_cloud=False):
    """拉取实时行情，云端模式用akshare，本地用新浪"""
    if not codes:
        return {}
    if use_cloud:
        return fetch_realtime_akshare(codes)
    else:
        quotes = fetch_realtime_sina(codes)
        # 本地模式如果新浪失败，尝试akshare兜底
        if not quotes:
            print("  [新浪行情失败，切换akshare]")
            return fetch_realtime_akshare(codes)
        return quotes


# ============ 持仓管理 ============

def load_portfolio():
    """加载持仓"""
    if not os.path.exists("portfolio.json"):
        return {"holdings": {}, "cash": 0, "trades": [], "daily_snapshots": []}
    with open("portfolio.json", "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(portfolio):
    """保存持仓（先备份）"""
    if os.path.exists("portfolio.json"):
        backup_dir = "backups"
        os.makedirs(backup_dir, exist_ok=True)
        ts = now_beijing().strftime("%Y%m%d_%H%M%S")
        shutil.copy2("portfolio.json", f"{backup_dir}/portfolio_monitor_{ts}.json")

    portfolio["last_update"] = now_beijing().strftime("%Y-%m-%d %H:%M")
    with open("portfolio.json", "w", encoding="utf-8") as f:
        json.dump(portfolio, f, ensure_ascii=False, indent=2)


def load_watchlist(n=10):
    """从 data.json 加载候选股列表（评分最高的N只）"""
    if not os.path.exists("data.json"):
        return []
    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    scores = data.get("scores", {}).get("all", [])
    sorted_scores = sorted(scores, key=lambda x: x.get("composite", 0), reverse=True)
    return sorted_scores[:n]


# ============ 云端git提交 ============

def git_commit_cloud(msg):
    """云端模式：把交易结果提交回仓库"""
    try:
        subprocess.run(["git", "config", "user.name", "AI Quant Bot"], check=True)
        subprocess.run(["git", "config", "user.email", "bot@quant.ai"], check=True)
        subprocess.run(["git", "add", "portfolio.json"], check=True)
        subprocess.run(["git", "add", "backups/"], check=True, capture_output=True)
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            subprocess.run(["git", "push"], check=True, capture_output=True)
            print(f"  [云端] 已提交: {msg}")
            return True
    except Exception as e:
        print(f"  [云端提交失败] {e}")
    return False


# ============ 自动交易执行 ============

def auto_sell(portfolio, code, name, shares, price, reason, today_str):
    """自动卖出一只股票（遵守A股规则）"""
    h = portfolio["holdings"].get(code)
    if not h:
        return False, f"[卖出失败] {name}({code}) 不在持仓中"

    if is_t1_blocked(h, today_str):
        return False, f"[T+1拦截] {name}({code}) 今天买入的，A股规定明天才能卖出"

    gross = shares * price
    sell_commission = gross * COMMISSION_RATE
    stamp_tax = gross * STAMP_TAX_RATE
    slippage_cost = gross * SLIPPAGE
    net_proceeds = gross - sell_commission - stamp_tax - slippage_cost

    real_pnl = net_proceeds - h["cost"]
    real_pnl_pct = (real_pnl / h["cost"] * 100) if h["cost"] > 0 else 0

    portfolio["cash"] += net_proceeds

    portfolio["trades"].append({
        "date": today_str,
        "time": now_beijing().strftime("%H:%M:%S"),
        "code": code,
        "name": name,
        "action": "卖出",
        "shares": shares,
        "price": price,
        "amount": round(gross, 2),
        "net_amount": round(net_proceeds, 2),
        "cost": h["cost"],
        "fees": round(sell_commission + stamp_tax + slippage_cost, 2),
        "pnl": round(real_pnl, 2),
        "pnl_pct": round(real_pnl_pct, 2),
        "reason": reason,
        "source": "盘中自动",
    })

    del portfolio["holdings"][code]
    return True, f"[自动卖出] {name}({code}) {shares}股 x {price:.2f} = ¥{gross:,.0f} 盈亏{real_pnl_pct:+.1f}% 原因:{reason}"


def auto_buy(portfolio, code, name, current_price, score, today_str):
    """自动买入一只股票（遵守A股规则）"""
    if code in portfolio["holdings"]:
        return False, f"[买入失败] {name}({code}) 已持有"

    lot = get_lot_size(code)

    total_value = portfolio["cash"]
    for c, h in portfolio["holdings"].items():
        total_value += h["shares"] * h["buy_price"]

    fee_factor = 1 + COMMISSION_RATE + SLIPPAGE
    target_amount = total_value * MAX_SINGLE_RATIO
    available = (portfolio["cash"] * 0.80) / fee_factor
    buy_amount = min(target_amount, available)

    if buy_amount < current_price * lot:
        return False, f"[买入失败] {name}({code}) 现金不足1手({lot}股x{current_price:.2f}={current_price*lot:.0f})"

    shares = int(buy_amount / current_price / lot) * lot
    if shares <= 0:
        return False, f"[买入失败] {name}({code}) 资金不够买1手"

    gross = shares * current_price
    buy_commission = gross * COMMISSION_RATE
    slippage_cost = gross * SLIPPAGE
    total_cost = gross + buy_commission + slippage_cost

    if portfolio["cash"] < total_cost:
        return False, f"[买入失败] {name}({code}) 现金¥{portfolio['cash']:,.0f}不够(需¥{total_cost:,.0f})"

    portfolio["cash"] -= total_cost
    portfolio["holdings"][code] = {
        "name": name,
        "shares": shares,
        "buy_price": current_price,
        "cost": round(total_cost, 2),
        "buy_date": today_str,
    }

    portfolio["trades"].append({
        "date": today_str,
        "time": now_beijing().strftime("%H:%M:%S"),
        "code": code,
        "name": name,
        "action": "买入",
        "shares": shares,
        "price": current_price,
        "amount": round(gross, 2),
        "net_amount": round(total_cost, 2),
        "cost": round(total_cost, 2),
        "fees": round(buy_commission + slippage_cost, 2),
        "pnl": 0,
        "pnl_pct": 0,
        "reason": f"盘中突破买入，评分{score:.0f}",
        "source": "盘中自动",
    })

    return True, f"[自动买入] {name}({code}) {shares}股 x {current_price:.2f} = ¥{gross:,.0f} 评分{score:.0f}"


def check_and_execute(portfolio, quotes, watchlist, today_str):
    """检查交易条件并执行自动交易"""
    trade_logs = []
    holdings = portfolio.get("holdings", {})

    # ===== 卖出检查 =====
    for code, h in list(holdings.items()):
        q = quotes.get(code)
        if not q or q["current"] <= 0:
            continue

        buy_price = h.get("buy_price", 0)
        if buy_price <= 0:
            continue

        pnl_pct = (q["current"] / buy_price - 1) * 100

        if is_t1_blocked(h, today_str):
            if pnl_pct <= STOP_LOSS_PCT:
                trade_logs.append(f"[T+1憋着] {h['name']}({code}) 亏损{pnl_pct:+.1f}% 但今天刚买入，A股规定明天才能卖!")
            continue

        if is_at_limit_down(code, q):
            if pnl_pct <= STOP_LOSS_PCT:
                trade_logs.append(f"[跌停憋着] {h['name']}({code}) 亏损{pnl_pct:+.1f}% 但已跌停卖不出，等明天!")
            continue

        if pnl_pct <= STOP_LOSS_PCT:
            success, msg = auto_sell(
                portfolio, code, h["name"], h["shares"],
                q["current"], f"盘中硬止损 亏损{pnl_pct:+.1f}%",
                today_str
            )
            trade_logs.append(msg)

        elif pnl_pct >= TAKE_PROFIT_PCT:
            success, msg = auto_sell(
                portfolio, code, h["name"], h["shares"],
                q["current"], f"盘中止盈 盈利{pnl_pct:+.1f}%",
                today_str
            )
            trade_logs.append(msg)

    # ===== 买入检查 =====
    n_current = len(portfolio["holdings"])
    if n_current >= MAX_POSITIONS:
        return portfolio, trade_logs

    n_slots = MAX_POSITIONS - n_current

    for item in watchlist[:n_slots]:
        code = item["code"]
        name = item.get("name", code)
        score = item.get("composite", 0)

        if code in portfolio["holdings"]:
            continue

        q = quotes.get(code)
        if not q or q["current"] <= 0:
            continue

        if is_at_limit_up(code, q):
            trade_logs.append(f"[涨停跳过] {name}({code}) 涨幅{q['pct']:+.1f}% 涨停买不进")
            continue

        if q["pct"] >= SURGE_PCT and score >= BUY_MIN_SCORE:
            success, msg = auto_buy(
                portfolio, code, name, q["current"], score, today_str
            )
            trade_logs.append(msg)

    return portfolio, trade_logs


# ============ 终端显示 ============

def color_text(text, color):
    """终端彩色输出（中国习惯：涨红跌绿）"""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "cyan": "\033[96m",
        "magenta": "\033[95m",
        "bold": "\033[1m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def print_dashboard(portfolio, quotes, watchlist, trade_logs, n_ticks, is_cloud=False):
    """打印实时仪表盘"""
    os.system("cls" if os.name == "nt" else "clear")

    now_str = now_beijing().strftime("%Y-%m-%d %H:%M:%S")
    today_str = today_str_beijing()
    mode_str = "云端" if is_cloud else "本地"

    print("=" * 70)
    print(f"  A股AI量化 - 盘中自动交易[{mode_str}模式]  |  {now_str}  |  第{n_ticks}轮")
    print("=" * 70)

    print(f"  {color_text('[A股规则]', 'yellow')} T+1 | 100股1手(科创200) | 涨跌停不交易 | 止损{STOP_LOSS_PCT}% 止盈+{TAKE_PROFIT_PCT}%")
    print()

    holdings = portfolio.get("holdings", {})
    cash = portfolio.get("cash", 0)
    print(f"{color_text('【持仓监控】', 'bold')}")

    if not holdings:
        print("  无持仓，等待买入信号...")
    else:
        print(f"  {'股票':<12} {'买入价':>8} {'现价':>8} {'盈亏%':>8} {'日涨跌%':>8} {'T+1':>6} {'市值':>10}")
        print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6} {'-'*10}")

        total_value = cash
        for code, h in holdings.items():
            q = quotes.get(code)
            name = h.get("name", code)
            buy_price = h.get("buy_price", 0)
            shares = h.get("shares", 0)

            if q and q["current"] > 0:
                current = q["current"]
                day_pct = q["pct"]
            else:
                current = buy_price
                day_pct = 0.0

            pnl_pct = (current / buy_price - 1) * 100 if buy_price > 0 else 0
            market_value = shares * current
            total_value += market_value

            t1_tag = "锁定" if is_t1_blocked(h, today_str) else "可卖"

            limit_tag = ""
            if q and q["current"] > 0:
                if is_at_limit_up(code, q):
                    limit_tag = " 涨停"
                elif is_at_limit_down(code, q):
                    limit_tag = " 跌停"

            pnl_color = "red" if pnl_pct > 0 else ("green" if pnl_pct < 0 else "")
            day_color = "red" if day_pct > 0 else ("green" if day_pct < 0 else "")

            pnl_str = color_text(f"{pnl_pct:+.1f}%{limit_tag}", pnl_color)
            day_str = color_text(f"{day_pct:+.1f}", day_color)
            t1_str = color_text(t1_tag, "yellow" if "锁" in t1_tag else "cyan")

            print(f"  {name:<12} {buy_price:>8.2f} {current:>8.2f}  {pnl_str:>14}  {day_str:>10}  {t1_str:>6}  {market_value:>8.0f}")

        total_pnl = total_value - 1000000
        total_pct = (total_value / 1000000 - 1) * 100
        pnl_color = "red" if total_pnl > 0 else ("green" if total_pnl < 0 else "")
        print(f"\n  总资产: {total_value:>,.0f}  |  总盈亏: {color_text(f'{total_pnl:+,.0f} ({total_pct:+.2f}%)', pnl_color)}  |  现金: {cash:,.0f}")

    if watchlist:
        print(f"\n{color_text('【候选股监控】', 'bold')}  (评分TOP{min(len(watchlist),5)}只)")
        print(f"  {'股票':<12} {'现价':>8} {'日涨跌%':>8} {'评分':>6} {'状态':>8}")
        print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*6} {'-'*8}")
        for item in watchlist[:5]:
            code = item["code"]
            name = item.get("name", code)
            score = item.get("composite", 0)
            q = quotes.get(code)
            if q and q["current"] > 0:
                day_color = "red" if q["pct"] > 0 else ("green" if q["pct"] < 0 else "")
                day_str = color_text(f"{q['pct']:+.1f}", day_color)

                if is_at_limit_up(code, q):
                    status = color_text("涨停", "red")
                elif q["pct"] >= SURGE_PCT and score >= BUY_MIN_SCORE and code not in holdings:
                    status = color_text("可买!", "magenta")
                elif q["pct"] >= SURGE_PCT:
                    status = color_text("观望", "yellow")
                else:
                    status = color_text("平淡", "")

                print(f"  {name:<12} {q['current']:>8.2f}  {day_str:>10}  {score:>6.0f}  {status}")
            else:
                print(f"  {name:<12} {'--':>8} {'--':>8} {score:>6.0f} {'--':>8}")

    if trade_logs:
        print(f"\n{color_text('【本轮交易执行】', 'bold')}  {len(trade_logs)} 条")
        for log in trade_logs:
            if "自动卖出" in log:
                print(f"  {color_text(log, 'green')}")
            elif "自动买入" in log:
                print(f"  {color_text(log, 'magenta')}")
            elif "T+1" in log or "跌停" in log or "涨停" in log:
                print(f"  {color_text(log, 'yellow')}")
            else:
                print(f"  {log}")

    today_trades = [t for t in portfolio.get("trades", []) if t.get("date") == today_str and t.get("source") == "盘中自动"]
    if today_trades:
        print(f"\n{color_text('【今日累计交易】', 'bold')}  {len(today_trades)} 笔")
        for t in today_trades:
            action_str = t["action"]
            action_color = "green" if action_str == "卖出" else "magenta"
            line = f"{action_str} {t['name']}({t['code']}) {t['shares']}股 x {t['price']}"
            print(f"  {color_text(line, action_color)}")

    interval = CLOUD_INTERVAL if is_cloud else DEFAULT_INTERVAL
    print(f"\n{'=' * 70}")
    print(f"  刷新: {interval}秒 | 止损{STOP_LOSS_PCT}% 止盈+{TAKE_PROFIT_PCT}% 突破+{SURGE_PCT}% 买入评分>={BUY_MIN_SCORE}")
    if is_cloud:
        print(f"  云端模式: akshare数据源 | 交易自动提交GitHub | 15:10自动停止")
    print(f"  Ctrl+C 退出")
    print(f"{'=' * 70}")


# ============ 主循环 ============

def run_once(is_cloud=False):
    """跑一次监控+交易"""
    portfolio = load_portfolio()
    watchlist = load_watchlist(10)

    holding_codes = list(portfolio.get("holdings", {}).keys())
    watch_codes = [item["code"] for item in watchlist]
    all_codes = list(set(holding_codes + watch_codes))

    if not all_codes:
        print("[错误] 没有持仓也没有候选股，请先运行 generate_daily.py")
        return [], 0

    quotes = fetch_realtime(all_codes, use_cloud=is_cloud)

    today_str = today_str_beijing()

    portfolio, trade_logs = check_and_execute(portfolio, quotes, watchlist, today_str)

    has_real_trade = any("自动" in log for log in trade_logs)
    if trade_logs and has_real_trade:
        save_portfolio(portfolio)
        # 云端模式：提交到git
        if is_cloud:
            trade_summary = " | ".join([log for log in trade_logs if "自动" in log][:3])
            git_commit_cloud(f"盘中交易: {trade_summary}")

    return trade_logs, len(quotes)


def run_loop(interval=DEFAULT_INTERVAL, is_cloud=False):
    """盘中持续监控+自动交易循环"""
    if is_cloud:
        interval = CLOUD_INTERVAL

    mode_str = "云端" if is_cloud else "本地"
    print(f"\n{color_text('A股盘中自动交易系统启动[' + mode_str + '模式]', 'yellow')}")
    print(f"  模拟盘 | 每{interval}秒刷新 | 遵守A股T+1/涨跌停/手数规则")
    print(f"  止损{STOP_LOSS_PCT}% 止盈+{TAKE_PROFIT_PCT}% 突破买入评分>={BUY_MIN_SCORE}")
    if is_cloud:
        print(f"  数据源: akshare(东方财富) | 交易自动提交GitHub | 15:10自动停止")
    print(f"  正在加载持仓和候选股...\n")

    n_ticks = 0

    while True:
        # 云端模式：15:10自动停止
        if is_cloud and should_stop_cloud():
            print(f"\n{color_text('[15:10] 收盘，云端任务自动停止', 'cyan')}")
            # 最后保存一次
            try:
                git_commit_cloud("收盘自动保存")
            except:
                pass
            break

        trading, msg = is_trading_hours()
        if not trading:
            if is_cloud:
                # 云端模式：非交易时间等待5分钟后重试
                print(f"  [{now_beijing().strftime('%H:%M')}] 非交易时间: {msg}，等待5分钟...")
                time.sleep(300)
                continue
            else:
                os.system("cls" if os.name == "nt" else "clear")
                print(f"\n  [非交易时间] {msg}")
                print(f"  系统等待交易时间(9:30-11:30, 13:00-15:00)...")
                print(f"  每5分钟检查一次。Ctrl+C退出。\n")
                time.sleep(300)
                continue

        try:
            n_ticks += 1
            trade_logs, n_quotes = run_once(is_cloud=is_cloud)

            portfolio = load_portfolio()
            watchlist = load_watchlist(10)
            holding_codes = list(portfolio.get("holdings", {}).keys())
            watch_codes = [item["code"] for item in watchlist]
            all_codes = list(set(holding_codes + watch_codes))
            quotes = fetch_realtime(all_codes, use_cloud=is_cloud) if all_codes else {}

            print_dashboard(portfolio, quotes, watchlist, trade_logs, n_ticks, is_cloud=is_cloud)

        except KeyboardInterrupt:
            print(f"\n\n{color_text('交易系统已停止。', 'yellow')}")
            sys.exit(0)
        except Exception as e:
            print(f"\n[错误] {e}")
            import traceback
            traceback.print_exc()
            print("10秒后重试...")

        time.sleep(interval)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="A股盘中自动交易系统（模拟盘）")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help=f"刷新间隔秒数 (默认{DEFAULT_INTERVAL})")
    parser.add_argument("--once", action="store_true", help="只跑一次（测试用）")
    parser.add_argument("--force", action="store_true", help="非交易时间也强制运行")
    parser.add_argument("--cloud", action="store_true", help="云端模式（GitHub Actions用）")
    args = parser.parse_args()

    if args.once or args.force:
        is_cloud = args.cloud
        trade_logs, _ = run_once(is_cloud=is_cloud)
        portfolio = load_portfolio()
        watchlist = load_watchlist(10)
        holding_codes = list(portfolio.get("holdings", {}).keys())
        watch_codes = [item["code"] for item in watchlist]
        all_codes = list(set(holding_codes + watch_codes))
        quotes = fetch_realtime(all_codes, use_cloud=is_cloud) if all_codes else {}
        print_dashboard(portfolio, quotes, watchlist, trade_logs, 1, is_cloud=is_cloud)
    else:
        run_loop(args.interval, is_cloud=args.cloud)
