"""
self_review.py - AI自我复盘与精进系统

每天交易结束后，AI从8个维度审视自己的表现：
    1. 信号准确度 - 昨天的买入/卖出信号，今天验证对了几个
    2. 因子归因   - 4个因子谁在帮忙谁在添乱，据此调整权重
    3. 仓位配置   - 鸡蛋放一个篮子了吗，现金留够了吗
    4. 风险控制   - 最大单只亏损、整体回撤、止损有没有及时
    5. 漏买漏卖   - 该买没买的涨了没？该卖没卖的跌了没？
    6. 板块表现   - 哪个板块今天给力，我们的仓位踩对了吗
    7. 交易效率   - 换手太频繁了吗，手续费吃了多少
    8. 参数自调整 - 基于以上分析，微调因子权重和买卖阈值

核心思想：不是写死一套参数跑到死，而是每天复盘→微调→进化。
就像一个交易员每天收盘后写交易日记，不断修正自己的判断框架。

复盘报告存在 review_log.json（追加），参数调整写入 strategy_params.json。
"""

import json
import os
import numpy as np
from datetime import datetime

from stock_universe import get_universe, get_stock_name
from portfolio import load_portfolio, calc_total_value, load_strategy_params

SIGNAL_HISTORY_FILE = "signal_history.json"
REVIEW_LOG_FILE = "review_log.json"
PARAMS_FILE = "strategy_params.json"


# ============================================================
#  数据存取
# ============================================================

def save_signal_history(scores, prices, date_str):
    """
    保存今天的评分快照，明天复盘时用来验证信号准不准。

    存的内容：每只股票今天的评分、4个因子分、今天价格。
    明天复盘时，拿今天的评分对比明天的实际涨跌。
    """
    history = []
    if os.path.exists(SIGNAL_HISTORY_FILE):
        try:
            with open(SIGNAL_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []

    snapshot = {
        "date": date_str,
        "scores": [
            {
                "code": s["code"],
                "name": s.get("name", s["code"]),
                "composite": s["composite"],
                "trend": s["trend"],
                "momentum": s["momentum"],
                "volume": s["volume"],
                "mean_reversion": s["mean_reversion"],
                "signal": s["signal"],
                "price": prices.get(s["code"], s.get("close", 0)),
                "change_pct": s.get("change_pct", 0),
            }
            for s in scores
        ],
    }
    history.append(snapshot)

    # 只保留最近30天
    if len(history) > 30:
        history = history[-30:]

    with open(SIGNAL_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2, default=str)


def load_signal_history():
    """加载历史评分快照"""
    if os.path.exists(SIGNAL_HISTORY_FILE):
        try:
            with open(SIGNAL_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def load_review_log():
    """加载历史复盘记录"""
    if os.path.exists(REVIEW_LOG_FILE):
        try:
            with open(REVIEW_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_review_log(log):
    """保存复盘记录（追加）"""
    with open(REVIEW_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2, default=str)


def load_params():
    """加载当前策略参数"""
    if os.path.exists(PARAMS_FILE):
        with open(PARAMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"factor_weights": {"trend": 0.30, "momentum": 0.30,
                               "volume": 0.20, "mean_reversion": 0.20}}


def save_params(params):
    """保存调整后的策略参数"""
    params["updated_at"] = datetime.now().strftime("%Y-%m-%d")
    params["update_count"] = params.get("update_count", 0) + 1
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2, default=str)


# ============================================================
#  维度1: 信号准确度
# ============================================================

def review_signal_accuracy(yesterday_scores, today_prices):
    """
    拿昨天的评分，对比今天的实际涨跌，看信号准不准。

    买入信号(评分>20)的股票今天涨了 → 命中
    卖出信号(评分<-20)的股票今天跌了 → 命中
    """
    result = {
        "buy_signals": 0, "buy_hits": 0,
        "sell_signals": 0, "sell_hits": 0,
        "neutral": 0,
        "details": [],
    }

    for s in yesterday_scores:
        code = s["code"]
        y_price = s["price"]
        t_price = today_prices.get(code, y_price)
        change = (t_price / y_price - 1) * 100 if y_price > 0 else 0
        composite = s["composite"]

        if composite >= 20:
            result["buy_signals"] += 1
            hit = change > 0
            if hit:
                result["buy_hits"] += 1
            result["details"].append({
                "code": code, "name": s["name"],
                "signal": "买入", "score": composite,
                "change": round(change, 2),
                "hit": hit,
            })
        elif composite <= -20:
            result["sell_signals"] += 1
            hit = change < 0
            if hit:
                result["sell_hits"] += 1
            result["details"].append({
                "code": code, "name": s["name"],
                "signal": "卖出", "score": composite,
                "change": round(change, 2),
                "hit": hit,
            })
        else:
            result["neutral"] += 1

    result["buy_accuracy"] = (
        round(result["buy_hits"] / result["buy_signals"] * 100, 1)
        if result["buy_signals"] > 0 else None
    )
    result["sell_accuracy"] = (
        round(result["sell_hits"] / result["sell_signals"] * 100, 1)
        if result["sell_signals"] > 0 else None
    )
    return result


# ============================================================
#  维度2: 因子归因
# ============================================================

def review_factor_attribution(yesterday_scores, today_prices):
    """
    分析4个因子各自的预测能力。

    逻辑：
        如果某因子分数高 + 股票实际涨了 → 这个因子有用 → 应该加权
        如果某因子分数高 + 股票实际跌了 → 这个因子不准 → 应该降权

    返回每个因子的"准确度得分"，用于后续权重调整。
    """
    factor_names = ["trend", "momentum", "volume", "mean_reversion"]
    factor_scores = {f: [] for f in factor_names}

    for s in yesterday_scores:
        code = s["code"]
        y_price = s["price"]
        t_price = today_prices.get(code, y_price)
        change = (t_price / y_price - 1) * 100 if y_price > 0 else 0

        for f in factor_names:
            f_score = s.get(f, 0)
            # 因子贡献 = 因子方向 × 实际涨跌方向
            # 因子分>0且涨=正贡献, 因子分<0且跌=正贡献
            contribution = (1 if (f_score > 0) == (change > 0) else -1) * abs(change)
            factor_scores[f].append({
                "code": code,
                "factor_score": f_score,
                "change": change,
                "contribution": round(contribution, 2),
            })

    # 计算每个因子的平均贡献
    attribution = {}
    for f in factor_names:
        if factor_scores[f]:
            contributions = [x["contribution"] for x in factor_scores[f]]
            attribution[f] = {
                "avg_contribution": round(np.mean(contributions), 2),
                "hit_rate": round(
                    sum(1 for c in contributions if c > 0) / len(contributions) * 100, 1
                ),
                "samples": len(contributions),
            }
        else:
            attribution[f] = {"avg_contribution": 0, "hit_rate": 0, "samples": 0}

    return attribution


# ============================================================
#  维度3: 仓位配置
# ============================================================

def review_position(portfolio, prices):
    """检查仓位集中度、现金比例、板块分散度"""
    total_value = calc_total_value(portfolio, prices)
    holdings = portfolio["holdings"]

    if not holdings:
        return {"status": "空仓", "cash_ratio": 100, "concentration": 0}

    # 各持仓占比
    weights = []
    sector_map = {}
    universe = {c: (n, s) for c, n, s, _ in get_universe()}

    for code, h in holdings.items():
        mv = h["shares"] * prices.get(code, h["buy_price"])
        w = mv / total_value * 100
        weights.append(w)
        sector = universe.get(code, ("", "未知"))[1]
        sector_map[sector] = sector_map.get(sector, 0) + w

    cash_ratio = portfolio["cash"] / total_value * 100
    max_single = max(weights) if weights else 0
    # HHI集中度指数（0=完全分散, 10000=完全集中）
    hhi = sum(w ** 2 for w in weights)

    return {
        "cash_ratio": round(cash_ratio, 1),
        "max_single_pct": round(max_single, 1),
        "n_positions": len(holdings),
        "concentration_hhi": round(hhi, 0),
        "sector_distribution": {k: round(v, 1) for k, v in
                                sorted(sector_map.items(), key=lambda x: -x[1])},
        "total_value": round(total_value, 0),
    }


# ============================================================
#  维度4: 风险控制
# ============================================================

def review_risk(portfolio, prices):
    """检查当前风险状况"""
    holdings = portfolio["holdings"]
    position_pnls = []
    max_loss = 0
    max_loss_code = ""

    for code, h in holdings.items():
        pnl = (prices.get(code, h["buy_price"]) / h["buy_price"] - 1) * 100
        position_pnls.append({"code": code, "name": h["name"], "pnl": round(pnl, 1)})
        if pnl < max_loss:
            max_loss = pnl
            max_loss_code = code

    # 计算组合回撤
    snapshots = portfolio.get("daily_snapshots", [])
    max_drawdown = 0
    if len(snapshots) > 1:
        peak = snapshots[0]["total_value"]
        for snap in snapshots:
            v = snap["total_value"]
            if v > peak:
                peak = v
            dd = (v / peak - 1) * 100
            if dd < max_drawdown:
                max_drawdown = dd

    return {
        "max_single_loss": round(max_loss, 1),
        "max_loss_stock": max_loss_code,
        "max_drawdown": round(max_drawdown, 1),
        "position_pnls": position_pnls,
        "need_stop_loss": max_loss < -10,
    }


# ============================================================
#  维度5: 漏买漏卖检讨
# ============================================================

def review_missed_signals(yesterday_scores, today_prices, portfolio):
    """
    检讨：该买没买的涨了没？该卖没卖的跌了没？
    """
    missed_buys = []
    missed_sells = []

    held_codes = set(portfolio["holdings"].keys())

    for s in yesterday_scores:
        code = s["code"]
        y_price = s["price"]
        t_price = today_prices.get(code, y_price)
        change = (t_price / y_price - 1) * 100 if y_price > 0 else 0
        composite = s["composite"]

        # 该买没买：评分>20但不在持仓中
        if composite >= 20 and code not in held_codes:
            missed_buys.append({
                "code": code, "name": s["name"],
                "score": composite, "change": round(change, 2),
                "regret": change > 2,  # 涨超2%才算真遗憾
            })

    # 该卖没卖：持仓中亏损>5%但评分还在卖出线以上
    for code, h in portfolio["holdings"].items():
        pnl = (today_prices.get(code, h["buy_price"]) / h["buy_price"] - 1) * 100
        s = next((x for x in yesterday_scores if x["code"] == code), None)
        if s and pnl < -5 and s["composite"] > -20:
            missed_sells.append({
                "code": code, "name": h["name"],
                "pnl": round(pnl, 1), "score": s["composite"],
            })

    return {
        "missed_buys": missed_buys,
        "missed_sells": missed_sells,
        "real_regrets": sum(1 for m in missed_buys if m["regret"]),
    }


# ============================================================
#  维度6: 板块表现
# ============================================================

def review_sector_performance(scores, prices, signal_history):
    """分析各板块今天的平均涨跌，看我们的持仓板块踩对没"""
    universe = get_universe()
    sector_changes = {}

    for code, name, sector, _ in universe:
        # 今天的涨跌：如果有昨天价格，算出来
        change = None
        if signal_history:
            yesterday = signal_history[-1]
            y_score = next((s for s in yesterday["scores"] if s["code"] == code), None)
            if y_score:
                y_price = y_score["price"]
                t_price = prices.get(code, y_price)
                change = (t_price / y_price - 1) * 100 if y_price > 0 else 0

        if change is not None:
            sector_changes.setdefault(sector, []).append(change)

    sector_avg = {}
    for sector, changes in sector_changes.items():
        sector_avg[sector] = {
            "avg_change": round(np.mean(changes), 2),
            "n_stocks": len(changes),
        }

    # 排序
    sector_avg = dict(sorted(sector_avg.items(), key=lambda x: -x[1]["avg_change"]))
    return sector_avg


# ============================================================
#  维度7: 交易效率
# ============================================================

def review_trading_efficiency(portfolio):
    """换手率、交易频率、手续费估算"""
    trades = portfolio.get("trades", [])
    snapshots = portfolio.get("daily_snapshots", [])

    # 最近10笔交易
    recent_trades = trades[-10:] if len(trades) > 10 else trades
    n_recent = len(recent_trades)

    # 估算手续费（A股约万分之2.5+印花税千分之1卖出收）
    total_cost = 0
    for t in recent_trades:
        if t["action"] == "买入":
            total_cost += t["amount"] * 0.00025
        else:
            total_cost += t["amount"] * (0.00025 + 0.001)

    # 平均持仓天数
    holding_days = []
    completed = [t for t in trades if t["action"] == "卖出"]
    for i, sell in enumerate(completed):
        buy = next((t for t in trades if t["code"] == sell["code"]
                    and t["action"] == "买入" and t["date"] <= sell["date"]), None)
        if buy:
            try:
                d1 = datetime.strptime(buy["date"], "%Y-%m-%d")
                d2 = datetime.strptime(sell["date"], "%Y-%m-%d")
                holding_days.append((d2 - d1).days)
            except Exception:
                pass

    return {
        "recent_trades": n_recent,
        "estimated_cost": round(total_cost, 0),
        "avg_holding_days": round(np.mean(holding_days), 1) if holding_days else None,
        "total_trades": len(trades),
        "trading_days": len(snapshots),
    }


# ============================================================
#  维度8: 参数自调整（核心进化逻辑）
# ============================================================

def adjust_parameters(params, factor_attribution, review_result):
    """
    基于因子归因，微调因子权重。

    规则：
        - 表现好的因子（正贡献、命中率高）→ 权重+2%~5%
        - 表现差的因子（负贡献、命中率低）→ 权重-2%~5%
        - 每次调整幅度不超过5%，避免剧烈波动
        - 权重始终在10%~45%之间
        - 四个因子权重之和必须=1

    同时根据风险状况调整买卖阈值：
        - 连续亏损 → 收紧买入阈值（更挑剔）
        - 连续盈利 → 适当放宽（更积极）
    """
    fw = params.get("factor_weights", {
        "trend": 0.30, "momentum": 0.30, "volume": 0.20, "mean_reversion": 0.20
    }).copy()

    rules = params.get("adjustment_rules", {
        "max_weight_change_per_review": 0.05,
        "min_weight": 0.10, "max_weight": 0.45,
    })
    max_change = rules.get("max_weight_change_per_review", 0.05)
    min_w = rules.get("min_weight", 0.10)
    max_w = rules.get("max_weight", 0.45)

    adjustments = []
    new_weights = {}

    for factor in ["trend", "momentum", "volume", "mean_reversion"]:
        old_w = fw.get(factor, 0.25)
        attr = factor_attribution.get(factor, {"avg_contribution": 0, "hit_rate": 50})

        # 调整方向：正贡献→加权，负贡献→降权
        contribution = attr["avg_contribution"]
        hit_rate = attr["hit_rate"]

        # 调整幅度：贡献越大调越多，但不超过max_change
        if contribution > 0.5:
            change = min(max_change, abs(contribution) * 0.01)
        elif contribution < -0.5:
            change = -min(max_change, abs(contribution) * 0.01)
        else:
            change = 0  # 贡献太小，不动

        new_w = old_w + change
        new_w = max(min_w, min(max_w, new_w))
        new_weights[factor] = round(new_w, 4)

        if abs(change) > 0.001:
            adjustments.append({
                "factor": factor,
                "old_weight": round(old_w, 4),
                "new_weight": round(new_w, 4),
                "change": round(change, 4),
                "reason": f"贡献度{contribution:+.2f}, 命中率{hit_rate:.0f}%",
            })

    # 归一化：确保权重之和=1
    total = sum(new_weights.values())
    if total > 0:
        for k in new_weights:
            new_weights[k] = round(new_weights[k] / total, 4)

    params["factor_weights"] = new_weights

    # 根据信号准确度调整买卖阈值
    sig = review_result.get("signal_accuracy", {})
    buy_acc = sig.get("buy_accuracy")
    if buy_acc is not None:
        buy_threshold = params.get("buy_threshold", 20)
        if buy_acc < 40:
            # 买入信号不准，提高门槛（更挑剔）
            new_bt = min(35, buy_threshold + 2)
            if new_bt != buy_threshold:
                adjustments.append({
                    "factor": "buy_threshold",
                    "old_weight": buy_threshold,
                    "new_weight": new_bt,
                    "change": new_bt - buy_threshold,
                    "reason": f"买入命中率仅{buy_acc:.0f}%，提高门槛",
                })
                params["buy_threshold"] = new_bt
        elif buy_acc > 70:
            # 买入信号很准，适当放宽
            new_bt = max(15, buy_threshold - 2)
            if new_bt != buy_threshold:
                adjustments.append({
                    "factor": "buy_threshold",
                    "old_weight": buy_threshold,
                    "new_weight": new_bt,
                    "change": new_bt - buy_threshold,
                    "reason": f"买入命中率达{buy_acc:.0f}%，适当放宽",
                })
                params["buy_threshold"] = new_bt

    # 根据风控状况调整止损
    risk = review_result.get("risk", {})
    if risk.get("need_stop_loss"):
        current_stop = params.get("hard_stop_loss", -0.10)
        if current_stop > -0.12:
            params["hard_stop_loss"] = -0.08  # 收紧止损
            adjustments.append({
                "factor": "hard_stop_loss",
                "old_weight": current_stop,
                "new_weight": -0.08,
                "change": -0.08 - current_stop,
                "reason": f"单只亏损{risk['max_single_loss']:.1f}%过大，收紧止损到-8%",
            })

    # 记录因子表现历史
    perf_history = params.get("factor_performance_history", [])
    perf_history.append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "attribution": factor_attribution,
    })
    if len(perf_history) > 30:
        perf_history = perf_history[-30:]
    params["factor_performance_history"] = perf_history

    return adjustments


# ============================================================
#  生成文字复盘报告
# ============================================================

def generate_review_report(date_str, review_result, adjustments):
    """
    把8个维度的分析结果，翻译成一段人话复盘报告。
    不是冰冷的数字，是AI在"说人话"反思自己。
    """
    lines = []
    lines.append(f"=== AI复盘报告 {date_str} ===\n")

    # 维度1: 信号准确度
    sig = review_result["signal_accuracy"]
    if sig["buy_signals"] > 0 or sig["sell_signals"] > 0:
        lines.append("【信号准确度】")
        if sig["buy_signals"] > 0:
            lines.append(f"  昨天发出{sig['buy_signals']}个买入信号，"
                        f"{sig['buy_hits']}个命中(今天涨了)，"
                        f"命中率{sig['buy_accuracy']}%")
        if sig["sell_signals"] > 0:
            lines.append(f"  昨天发出{sig['sell_signals']}个卖出信号，"
                        f"{sig['sell_hits']}个命中(今天跌了)，"
                        f"命中率{sig['sell_accuracy']}%")

        # 列出打脸的
        misses = [d for d in sig["details"] if not d["hit"]]
        if misses:
            lines.append(f"  打脸的信号:")
            for m in misses[:3]:
                lines.append(f"    {m['name']}: 预测{m['signal']}但实际"
                            f"{'涨' if m['change']>0 else '跌'}{abs(m['change']):.1f}%")
    else:
        lines.append("【信号准确度】暂无历史数据可比，明天开始有完整复盘")

    # 维度2: 因子归因
    attr = review_result["factor_attribution"]
    lines.append("\n【因子归因】")
    factor_cn = {"trend": "趋势", "momentum": "动量", "volume": "量能",
                 "mean_reversion": "均值回归"}
    best_factor = max(attr.items(), key=lambda x: x[1]["avg_contribution"])
    worst_factor = min(attr.items(), key=lambda x: x[1]["avg_contribution"])
    for f, a in attr.items():
        emoji = "+" if a["avg_contribution"] > 0 else "-"
        lines.append(f"  {factor_cn[f]}: 贡献度{a['avg_contribution']:+.2f} "
                    f"命中率{a['hit_rate']:.0f}% ({emoji})")
    lines.append(f"  表现最好的: {factor_cn[best_factor[0]]}")
    lines.append(f"  表现最差的: {factor_cn[worst_factor[0]]}")

    # 维度3: 仓位
    pos = review_result["position"]
    lines.append("\n【仓位配置】")
    lines.append(f"  总资产: ¥{pos['total_value']:,.0f}")
    lines.append(f"  持仓{pos['n_positions']}只, 现金{pos['cash_ratio']}%, "
                f"最大单只{pos['max_single_pct']}%")
    if "sector_distribution" in pos:
        lines.append(f"  板块分布: {', '.join(f'{k}{v}%' for k,v in list(pos['sector_distribution'].items())[:3])}")
    if pos["max_single_pct"] > 30:
        lines.append(f"  ⚠ 单只持仓{pos['max_single_pct']}%过高，下次减仓")

    # 维度4: 风险
    risk = review_result["risk"]
    lines.append("\n【风险控制】")
    if risk["max_single_loss"] < 0:
        lines.append(f"  最大单只亏损: {risk['max_single_loss']:.1f}% ({risk['max_loss_stock']})")
    lines.append(f"  组合最大回撤: {risk['max_drawdown']:.1f}%")
    if risk["need_stop_loss"]:
        lines.append(f"  ⚠ {risk['max_loss_stock']}亏损{risk['max_single_loss']:.1f}%，"
                    f"建议下次开盘止损")

    # 维度5: 漏买漏卖
    missed = review_result["missed"]
    if missed["real_regrets"] > 0 or missed["missed_sells"]:
        lines.append("\n【漏买漏卖检讨】")
        if missed["real_regrets"] > 0:
            regrets = [m for m in missed["missed_buys"] if m["regret"]]
            lines.append(f"  错过了{len(regrets)}只该买没买的:")
            for r in regrets[:3]:
                lines.append(f"    {r['name']}: 评分{r['score']:.0f}, 涨了{r['change']:.1f}%")
        if missed["missed_sells"]:
            lines.append(f"  {len(missed['missed_sells'])}只该止损没止损的:")
            for m in missed["missed_sells"][:3]:
                lines.append(f"    {m['name']}: 亏{m['pnl']:.1f}%, 评分还在{m['score']:.0f}")

    # 维度6: 板块
    sectors = review_result["sectors"]
    if sectors:
        lines.append("\n【板块表现】")
        top = list(sectors.items())[:2]
        bottom = list(sectors.items())[-2:]
        top_strs = [f"{k}+{v['avg_change']:.1f}%" for k, v in top]
        bottom_strs = [f"{k}{v['avg_change']:.1f}%" for k, v in bottom]
        lines.append(f"  最强: {', '.join(top_strs)}")
        lines.append(f"  最弱: {', '.join(bottom_strs)}")

    # 维度7: 交易效率
    eff = review_result["efficiency"]
    lines.append("\n【交易效率】")
    lines.append(f"  最近{eff['recent_trades']}笔交易, 估算手续费¥{eff['estimated_cost']}")
    if eff["avg_holding_days"]:
        lines.append(f"  平均持仓{eff['avg_holding_days']}天")

    # 维度8: 参数调整
    if adjustments:
        lines.append("\n【AI自我调整】")
        for adj in adjustments:
            if adj["factor"] in factor_cn:
                name = factor_cn[adj["factor"]]
            else:
                name = adj["factor"]
            lines.append(f"  {name}: {adj['old_weight']}→{adj['new_weight']} "
                        f"({adj['reason']})")
    else:
        lines.append("\n【AI自我调整】本轮不调整，参数维持不变")

    # 总结
    lines.append("\n【明日策略】")
    if adjustments:
        lines.append("  基于今日复盘微调了参数，明天用新参数扫描")
    else:
        lines.append("  参数无需调整，维持当前策略继续执行")

    return "\n".join(lines)


# ============================================================
#  主函数
# ============================================================

def run_self_review(portfolio, scores, plan, prices, data_dict):
    """
    执行完整8维度复盘。

    在 generate_daily.py 的最后调用。
    第一天没有历史数据时，只记录基线，不做归因分析。
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    history = load_signal_history()

    # 如果有昨天的数据，做完整复盘
    if history:
        yesterday = history[-1]
        yesterday_scores = yesterday["scores"]

        # 维度1: 信号准确度
        sig_acc = review_signal_accuracy(yesterday_scores, prices)

        # 维度2: 因子归因
        factor_attr = review_factor_attribution(yesterday_scores, prices)

        # 维度3: 仓位配置
        position = review_position(portfolio, prices)

        # 维度4: 风险控制
        risk = review_risk(portfolio, prices)

        # 维度5: 漏买漏卖
        missed = review_missed_signals(yesterday_scores, prices, portfolio)

        # 维度6: 板块表现
        sectors = review_sector_performance(scores, prices, history)

        # 维度7: 交易效率
        efficiency = review_trading_efficiency(portfolio)

        # 汇总
        review_result = {
            "date": date_str,
            "signal_accuracy": sig_acc,
            "factor_attribution": factor_attr,
            "position": position,
            "risk": risk,
            "missed": missed,
            "sectors": sectors,
            "efficiency": efficiency,
        }

        # 维度8: 参数自调整
        params = load_params()
        adjustments = adjust_parameters(params, factor_attr, review_result)
        if adjustments:
            save_params(params)

        review_result["adjustments"] = adjustments

    else:
        # 第一天：只记录基线
        review_result = {
            "date": date_str,
            "signal_accuracy": {"buy_signals": 0, "buy_hits": 0,
                               "sell_signals": 0, "sell_hits": 0,
                               "buy_accuracy": None, "sell_accuracy": None,
                               "details": [], "neutral": len(scores)},
            "factor_attribution": {f: {"avg_contribution": 0, "hit_rate": 0, "samples": 0}
                                   for f in ["trend", "momentum", "volume", "mean_reversion"]},
            "position": review_position(portfolio, prices),
            "risk": review_risk(portfolio, prices),
            "missed": {"missed_buys": [], "missed_sells": [], "real_regrets": 0},
            "sectors": review_sector_performance(scores, prices, []),
            "efficiency": review_trading_efficiency(portfolio),
            "adjustments": [],
        }
        adjustments = []

    # 生成文字报告
    report = generate_review_report(date_str, review_result, adjustments)
    print("\n" + report)

    # 保存评分历史（供明天复盘用）
    save_signal_history(scores, prices, date_str)

    # 保存复盘记录
    log = load_review_log()
    log.append({
        "date": date_str,
        "report": report,
        "result": review_result,
    })
    if len(log) > 90:
        log = log[-90:]
    save_review_log(log)

    return review_result, report


if __name__ == "__main__":
    # 独立测试：加载当前状态做一次复盘
    from portfolio import load_portfolio, calc_total_value
    from signal_engine import score_universe
    from stock_universe import get_universe, get_stock_name
    from generate_daily import fetch_universe_data

    print(">>> 加载数据...")
    data_dict, prices, open_prices = fetch_universe_data()
    scores = score_universe(data_dict)
    for s in scores:
        s["name"] = get_stock_name(s["code"])

    portfolio = load_portfolio()
    print(f">>> 当前资产: ¥{calc_total_value(portfolio, prices):,.0f}")
    print(f">>> 持仓: {len(portfolio['holdings'])}只\n")

    run_self_review(portfolio, scores, None, prices, data_dict)
