"""
generate_daily.py - 每日AI选股系统

每天跑一次，自动：
    1. 全市场扫描：平台 CompScore TOP 候选池（剔除创业板）
    2. 并行拉取候选池全部 K 线数据
    3. 4因子引擎精打分（可调权重 + 自我进化）
    4. 生成今日交易计划（买什么、卖什么）
    5. 执行模拟交易，更新持仓（含手续费/税/滑点）
    6. 生成可视化仪表盘 + AI 自我复盘

用法：
    python generate_daily.py

在你的电脑上每天跑一次就行。
想自动化可以用Windows计划任务（Task Scheduler）每天定时执行。
"""

import os
import sys
import json
import shutil
import pandas as pd
from datetime import datetime, timedelta

# 设置UTF-8编码
os.environ["PYTHONIOENCODING"] = "utf-8"

from stock_universe import get_universe, get_stock_name
from data_fetcher import fetch_batch_kline
from signal_engine import score_universe
from portfolio import load_portfolio, generate_trading_plan, execute_plan, calc_total_value, save_portfolio
from config import INITIAL_CASH
from trading_calendar import is_trading_day, trading_day_countdown

PORTFOLIO_FILE = "portfolio.json"
BACKUP_DIR = "backups"


def backup_portfolio():
    """每次运行前备份 portfolio.json，永不丢失持仓数据"""
    if not os.path.exists(PORTFOLIO_FILE):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = os.path.join(BACKUP_DIR, f"portfolio_{ts}.json")
    shutil.copy2(PORTFOLIO_FILE, backup_path)
    print(f"[备份] portfolio.json → {backup_path}")


def check_same_day_protection():
    """同日防重跑：如果今天已经跑过（有今天的快照），拒绝执行"""
    if not os.path.exists(PORTFOLIO_FILE):
        return True  # 没有持仓文件，允许运行（首次）

    try:
        with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            p = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        snapshots = p.get("daily_snapshots", [])
        today_snapshots = [s for s in snapshots if s.get("date") == today]
        if today_snapshots:
            print(f"[警告] 今天({today})已经运行过，持仓快照已存在。")
            print(f"[警告] 重复运行会导致：")
            print(f"       1. 同一天重复交易（A股T+1不允许）")
            print(f"       2. AI复盘参数被连续调整多次")
            print(f"[警告] 如确需强制重跑，请加参数：--force")
            return False
    except Exception:
        pass
    return True


def validate_data_freshness(data_dict):
    """
    数据新鲜度全局校验：所有K线数据最后日期必须是今天/最近交易日。
    
    这是防止"价格错误"的最后一道防线：
    如果拉完数据后发现某只股票的数据不是今天的，报警并提示。
    """
    from datetime import datetime, timedelta
    today = datetime.now().date()
    
    stale_codes = []
    for code, df in data_dict.items():
        last_date = pd.Timestamp(df["date"].iloc[-1]).date()
        gap = (today - last_date).days
        
        # 工作日：最多容忍1天gap（数据延迟）
        # 周一：容忍3天gap（周末没数据）
        if today.weekday() == 0:
            max_gap = 3
        elif today.weekday() >= 5:  # 周末
            max_gap = 3
        else:
            max_gap = 1
        
        if gap > max_gap:
            stale_codes.append((code, str(last_date), gap))
    
    if stale_codes:
        print(f"\n[⚠️ 数据新鲜度警告] {len(stale_codes)} 只股票数据不是今天的：")
        for code, last_date, gap in stale_codes[:10]:
            print(f"  {code}: 最后数据日期={last_date} (滞后{gap}天)")
        if len(stale_codes) > 10:
            print(f"  ... 还有 {len(stale_codes) - 10} 只")
        print(f"[⚠️] 这意味着价格可能不准确！")
        print(f"[⚠️] 可能原因：今天是非交易日、数据源延迟、END_DATE设置错误")
        return False
    else:
        print(f"[✅] 数据新鲜度校验通过：所有 {len(data_dict)} 只股票数据均为最新")
        return True


def fetch_universe_data():
    """
    全市场动态扫描 + 并行拉取 K 线数据。

    不再逐只循环——get_universe() 从平台取 TOP 候选，
    fetch_batch_kline() 并行拉所有 K 线。
    """
    universe = get_universe()
    codes = [code for code, name, sector, stype in universe]

    print(f">>> 候选池: {len(codes)} 只标的, 并行拉取 K 线...\n")

    data_dict, prices, open_prices = fetch_batch_kline(codes, max_workers=10)

    print(f"  成功拉取: {len(data_dict)}/{len(codes)} 只\n")
    return data_dict, prices, open_prices


def run_daily():
    """Execute daily stock selection pipeline."""
    # 备份持仓
    backup_portfolio()

    td = trading_day_countdown()

    print("=" * 60)
    print("  AShare AI Stock Selector")
    print(f"  Date: {td['today']}")
    print(f"  Status: {td['message']}")
    print("=" * 60)

    if not td["is_trading"]:
        print(f"\n  Market closed today (next trading day: {td['next_date']}).")
        print("  Generating preview dashboard only — no trades executed.\n")

    # 1. Fetch data
    print("\n>>> Step 1: Fetching market data")
    data_dict, prices, open_prices = fetch_universe_data()
    
    # 1.1 数据新鲜度校验（防止价格错误）
    print("\n>>> Step 1.1: Data freshness validation")
    validate_data_freshness(data_dict)

    # 2. Multi-factor scoring
    print("\n>>> Step 2: AI Multi-Factor Scoring")
    scores = score_universe(data_dict)

    for s in scores:
        s["name"] = get_stock_name(s["code"])

    print(f"\n  {len(scores)} stocks scored:")
    header = f"  {'Rank':<5} {'Code':<8} {'Name':<12} {'Composite':>7} {'Signal':<8} {'Conf':>5} {'RSI':>6}"
    print(header)
    print("  " + "-" * 58)
    for i, s in enumerate(scores[:15]):
        print(f"  {i+1:<5} {s['code']:<8} {s['name']:<12} {s['composite']:>+7.1f} {s['signal']:<8} {s['confidence']:>5.0f}% {s['rsi']:>6.1f}")
    if len(scores) > 15:
        print(f"  ... {len(scores)} total")

    # 3. Generate trading plan
    print("\n>>> Step 3: Generating trading plan")
    portfolio = load_portfolio()
    plan = generate_trading_plan(portfolio, scores, prices, open_prices)

    total_value = calc_total_value(portfolio, prices)
    print(f"\n  Portfolio: {total_value:,.0f}")
    print(f"  Cash: {portfolio['cash']:,.0f}")
    print(f"  Holdings: {len(portfolio['holdings'])}")

    if plan["sell"]:
        print(f"\n  [Sell] {len(plan['sell'])} signals:")
        for s in plan["sell"]:
            print(f"    {s['code']} {s['name']}: score {s['score']:.0f}, "
                  f"PnL {s['pnl_pct']:+.1f}%, {s['shares']}sh @ {s['price']:.2f}")
    else:
        print("\n  [Sell] none")

    if plan["buy"]:
        print(f"\n  [Buy] {len(plan['buy'])} signals:")
        for b in plan["buy"]:
            print(f"    {b['code']} {b['name']}: score {b['score']:.0f}, "
                  f"{b['shares']}sh @ {b['price']:.2f} = {b['amount']:,.0f}")
    else:
        print("\n  [Buy] none")

    if plan["hold"]:
        print(f"\n  [Hold] {len(plan['hold'])} positions:")
        for h in plan["hold"]:
            print(f"    {h['code']} {h['name']}: score {h['score']:.0f}, "
                  f"PnL {h['pnl_pct']:+.1f}%")

    # 4. Execute trades (only on trading days)
    if td["is_trading"]:
        print("\n>>> Step 4: Executing trades (live market)")
        portfolio = execute_plan(portfolio, plan, close_prices=prices)
    else:
        print(f"\n>>> Step 4: Skipping execution (next trading day: {td['next_date']})")

    total_value = calc_total_value(portfolio, prices)
    print(f"  Portfolio value: {total_value:,.0f}")

    # 5. Generate dashboard
    print("\n>>> Step 5: Generating dashboard")
    from dashboard import generate_dashboard
    dashboard_path = generate_dashboard(portfolio, scores, plan, prices, data_dict, td)
    print(f"  Dashboard: {dashboard_path}")

    # 6. AI self-review
    print("\n>>> Step 6: AI Self-Review")
    from self_review import run_self_review
    review_result, review_report = run_self_review(
        portfolio, scores, plan, prices, data_dict
    )

    from dashboard import append_review_to_dashboard
    append_review_to_dashboard(dashboard_path, review_report, review_result)

    # Save daily result
    daily_result = {
        "date": td["today"],
        "is_trading_day": td["is_trading"],
        "next_trading_day": td["next_date"],
        "scores": [{k: v for k, v in s.items() if k != "factors"} for s in scores],
        "plan": {
            "sell": plan["sell"],
            "buy": plan["buy"],
            "hold": plan["hold"],
        },
        "portfolio_value": total_value,
        "review_summary": review_report[:500] if review_report else "",
    }
    with open("daily_result.json", "w", encoding="utf-8") as f:
        json.dump(daily_result, f, ensure_ascii=False, indent=2, default=str)

    print("\n" + "=" * 60)
    if td["is_trading"]:
        print("  Daily scan complete!")
    else:
        print(f"  Preview ready. Market opens {td['next_date']}.")
    print(f"  Open dashboard.html to view")
    print("=" * 60)

    return dashboard_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='AI Quant Daily Scanner')
    parser.add_argument('--serve', action='store_true', help='Start HTTP server after generating dashboard')
    parser.add_argument('--port', type=int, default=8765, help='HTTP server port (default: 8765)')
    parser.add_argument('--force', action='store_true', help='强制重跑（跳过同日防重跑保护）')
    args = parser.parse_args()

    # 同日防重跑保护
    if not args.force:
        if not check_same_day_protection():
            print("\n退出。如需强制重跑：python generate_daily.py --force")
            sys.exit(1)

    run_daily()

    if args.serve:
        import threading
        import http.server
        import socketserver

        project_dir = os.path.dirname(os.path.abspath(__file__))
        os.chdir(project_dir)

        # 启动价格 API 服务器（8766端口）
        from live_price_server import start_server as start_price_server
        price_thread = threading.Thread(target=start_price_server, args=(8766,), daemon=True)
        price_thread.start()

        # 启动静态文件服务器（默认8765端口）
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", args.port), handler) as httpd:
            print(f"\n  仪表盘: http://localhost:{args.port}/dashboard.html")
            print(f"  价格API: http://localhost:8766/api/prices")
            print(f"  Press Ctrl+C to stop.")
            httpd.serve_forever()
