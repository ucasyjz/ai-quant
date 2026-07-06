"""
dashboard.py - Data bridge for the AI Quant Dashboard.

Architecture:
  - This module generates data.json with all portfolio/scores/plans.
  - dashboard.html is a standalone frontend that reads data.json via fetch().
  - Clean separation: Python handles data, HTML handles presentation.

v5.0: No more embedding HTML in Python strings. Pure JSON output.
"""

import json
import os
from datetime import datetime
from config import INITIAL_CASH
from trading_calendar import trading_day_countdown


SECTOR_COLORS = {
    "宽基指数": "#5b8def", "金融": "#60a5fa", "科技": "#818cf8",
    "消费": "#f59e0b", "医药": "#10b981", "新能源": "#f97316",
    "白酒": "#fbbf24", "银行": "#34d399", "周期": "#94a3b8",
    "通信": "#c084fc", "农业": "#14b8a6", "其他": "#64748b",
    "半导体": "#818cf8", "AI": "#a78bfa", "ETF": "#60a5fa",
    "资源": "#f59e0b", "制造": "#38bdf8", "电力": "#facc15",
}


def _build_portfolio_data(portfolio, prices):
    """Build clean portfolio JSON."""
    holdings = []
    total_market_value = 0
    for code, h in portfolio.get("holdings", {}).items():
        price = prices.get(code, h["buy_price"])
        value = h["shares"] * price
        total_market_value += value
        pnl = value - h["cost"]
        holdings.append({
            "code": code,
            "name": h["name"],
            "shares": h["shares"],
            "cost": round(h["cost"], 2),
            "buy_price": round(h["buy_price"], 2),
            "price": round(price, 2),
            "value": round(value, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round((price / h["buy_price"] - 1) * 100, 2),
        })
    holdings.sort(key=lambda x: -x["value"])

    total_value = portfolio.get("cash", 0) + total_market_value
    total_return = (total_value / INITIAL_CASH - 1) * 100

    return {
        "cash": round(portfolio.get("cash", 0), 2),
        "market_value": round(total_market_value, 2),
        "total_value": round(total_value, 2),
        "total_return_pct": round(total_return, 2),
        "init_cash": INITIAL_CASH,
        "holdings": holdings,
        "holding_count": len(holdings),
        "trades": portfolio.get("trades", [])[-30:],
        "trade_count": len(portfolio.get("trades", [])),
        "snapshots": portfolio.get("daily_snapshots", []),
    }


def _build_plan_data(plan):
    """Build clean plan JSON."""
    def clean_signal(items):
        return [{
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "score": round(s.get("score", 0), 1),
            "shares": s.get("shares", 0),
            "price": round(s.get("price", 0), 2),
            "amount": round(s.get("amount", 0), 2),
            "reason": s.get("reason", ""),
            "pnl_pct": round(s.get("pnl_pct", 0), 2),
        } for s in items]

    return {
        "buy": clean_signal(plan.get("buy", [])),
        "sell": clean_signal(plan.get("sell", [])),
        "hold": clean_signal(plan.get("hold", [])),
        "buy_count": len(plan.get("buy", [])),
        "sell_count": len(plan.get("sell", [])),
        "hold_count": len(plan.get("hold", [])),
    }


def _build_scores_data(scores, universe):
    """Build scores + market overview."""
    sector_map = {}
    for code, name, sector, _ in universe:
        sector_map[code] = {"name": name, "sector": sector}

    cleaned = []
    for s in scores:
        code = s["code"]
        info = sector_map.get(code, {"name": s.get("name", code), "sector": "其他"})
        cleaned.append({
            "code": code,
            "name": info["name"],
            "sector": info["sector"],
            "composite": round(float(s["composite"]), 1),
            "trend": round(float(s["trend"]), 1),
            "momentum": round(float(s["momentum"]), 1),
            "volume": round(float(s["volume"]), 1),
            "mean_reversion": round(float(s["mean_reversion"]), 1),
            "signal": str(s.get("signal", "中性")),
            "confidence": round(float(s.get("confidence", 50)), 1),
            "rsi": round(float(s.get("rsi", 50)), 1),
        })

    # Sort by composite descending
    cleaned.sort(key=lambda x: -x["composite"])

    # Sector averages
    sector_scores = {}
    for s in cleaned:
        sec = s["sector"]
        sector_scores.setdefault(sec, []).append(s["composite"])
    sector_list = [{"sector": k, "score": round(sum(v)/len(v), 1),
                    "color": SECTOR_COLORS.get(k, "#64748b"),
                    "count": len(v)}
                   for k, v in sorted(sector_scores.items(), key=lambda x: -sum(x[1])/len(x[1]))]

    # Market overview
    composites = [s["composite"] for s in cleaned]
    avg = sum(composites) / len(composites) if composites else 0
    buy_pct = sum(1 for s in cleaned if s["composite"] >= 20) / len(cleaned) * 100 if cleaned else 0
    sell_pct = sum(1 for s in cleaned if s["composite"] <= -20) / len(cleaned) * 100 if cleaned else 0

    if avg > 25: sentiment = "偏多"
    elif avg > 5: sentiment = "中性偏多"
    elif avg > -5: sentiment = "中性"
    elif avg > -25: sentiment = "中性偏空"
    else: sentiment = "偏空"

    return {
        "all": cleaned,
        "top20": cleaned[:20],
        "sector_avg": sector_list,
        "market_overview": {
            "avg_score": round(avg, 1),
            "max_score": round(composites[0], 1) if composites else 0,
            "min_score": round(composites[-1], 1) if composites else 0,
            "sentiment": sentiment,
            "buy_signal_pct": round(buy_pct, 1),
            "sell_signal_pct": round(sell_pct, 1),
            "thermo_pct": max(0, min(100, (avg + 100) / 200 * 100)),
            "total_scored": len(cleaned),
        },
    }


def _build_3d_data(scores_data):
    """Build 3D universe map data from scores."""
    stock_list = []
    for s in scores_data:
        composite = s["composite"]
        # Color: red (buy) -> gray (neutral) -> green (sell)
        if composite >= 60:
            color, emissive = "#f6465d", 0.55
        elif composite >= 20:
            t = (composite - 20) / 40
            color, emissive = _lerp_hex("#e8796e", "#f6465d", t), 0.35
        elif composite > -20:
            color, emissive = "#7b8ca8", 0.10
        elif composite > -60:
            t = (composite + 60) / 40
            color, emissive = _lerp_hex("#0ecb81", "#5ab89a", t), 0.35
        else:
            color, emissive = "#0ecb81", 0.55

        stock_list.append({
            "code": s["code"],
            "name": s["name"],
            "sector": s["sector"],
            "composite": s["composite"],
            "trend": s["trend"],
            "momentum": s["momentum"],
            "volume": s["volume"],
            "mean_reversion": s["mean_reversion"],
            "signal": s["signal"],
            "confidence": s["confidence"],
            "x": round(s["trend"] * 0.04, 3),
            "y": round(s["composite"] * 0.03, 3),
            "z": round(s["volume"] * 0.04, 3),
            "radius": round(0.09 + abs(composite) / 100 * 0.2, 3),
            "color": color,
            "emissive": emissive,
            "sectorColor": SECTOR_COLORS.get(s["sector"], "#64748b"),
        })

    # Sector rings for grouping
    sector_data = {}
    for st in stock_list:
        sec = st["sector"]
        sector_data.setdefault(sec, {"x": [], "y": [], "z": [],
                                      "color": SECTOR_COLORS.get(sec, "#64748b")})
        sector_data[sec]["x"].append(st["x"])
        sector_data[sec]["y"].append(st["y"])
        sector_data[sec]["z"].append(st["z"])

    rings = []
    for sec, data in sector_data.items():
        cx = sum(data["x"]) / len(data["x"])
        cy = sum(data["y"]) / len(data["y"])
        cz = sum(data["z"]) / len(data["z"])
        max_dist = 0
        for i in range(len(data["x"])):
            dist = ((data["x"][i]-cx)**2 + (data["y"][i]-cy)**2 + (data["z"][i]-cz)**2)**0.5
            max_dist = max(max_dist, dist)
        rings.append({
            "name": sec, "color": data["color"],
            "cx": round(cx, 3), "cy": round(cy, 3), "cz": round(cz, 3),
            "radius": round(max_dist * 1.2, 3),
        })

    return stock_list, rings


def _lerp_hex(a, b, t):
    a, b = a.lstrip("#"), b.lstrip("#")
    ra, ga, ba = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    rb, gb, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = int(ra + (rb - ra) * t)
    g = int(ga + (gb - ga) * t)
    bv = int(ba + (bb - ba) * t)
    return f"#{r:02x}{g:02x}{bv:02x}"


def generate_dashboard(portfolio, scores, plan, prices, data_dict, td=None):
    """
    Generate data.json for the dashboard frontend.
    
    No longer generates HTML. The standalone dashboard.html reads this JSON.
    """
    if td is None:
        td = trading_day_countdown()

    from stock_universe import get_universe
    universe = get_universe()

    # Build all data sections
    portfolio_data = _build_portfolio_data(portfolio, prices)
    plan_data = _build_plan_data(plan)
    scores_data = _build_scores_data(scores, universe)
    stock_3d, sector_rings = _build_3d_data(scores_data["all"])

    # Build radar data for top buy candidates
    radar = []
    for b in plan_data["buy"][:3]:
        s = next((x for x in scores_data["all"] if x["code"] == b["code"]), None)
        if s:
            radar.append({
                "name": s["name"],
                "data": [s["trend"], s["momentum"], s["volume"], s["mean_reversion"]],
                "color": ["#5b8def", "#818cf8", "#f59e0b"][len(radar) % 3],
            })

    data = {
        "generated_at": datetime.now().isoformat(),
        "version": "5.0",
        "market": {
            "is_trading": td["is_trading"],
            "today": td["today"],
            "prev_date": td["prev_date"],
            "next_date": td["next_date"],
            "next_weekday": td["next_weekday"],
            "status": td["message"],
            "days_until": td["days_until"],
        },
        "portfolio": portfolio_data,
        "plan": plan_data,
        "scores": scores_data,
        "radar": radar,
        "universe": {
            "stocks": stock_3d,
            "rings": sector_rings,
            "total": len(stock_3d),
        },
        "review": None,  # Populated by append_review_to_dashboard
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return os.path.abspath("dashboard.html")


def append_review_to_dashboard(dashboard_path, review_report, review_result):
    """Append review data to data.json so the dashboard can show it."""
    if not review_report or not os.path.exists("data.json"):
        return

    with open("data.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Read current strategy params for factor weights display
    try:
        with open("strategy_params.json", "r", encoding="utf-8") as f:
            params = json.load(f)
        factor_weights = params.get("factor_weights", {})
        update_count = params.get("update_count", 0)
    except Exception:
        factor_weights = {"trend": 0.30, "momentum": 0.30, "volume": 0.20, "mean_reversion": 0.20}
        update_count = 0

    data["review"] = {
        "report": review_report,
        "date": review_result.get("date", ""),
        "adjustments": review_result.get("adjustments", []),
        "factor_weights": factor_weights,
        "update_count": update_count,
    }

    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
