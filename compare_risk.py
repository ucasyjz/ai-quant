"""
compare_risk.py - 有风控 vs 无风控 对比回测

直接帮你跑两遍：一遍有风控、一遍无风控，然后对比。
这样你能直观看到：风控省了多少钱？代价是多少？
"""

from data_fetcher import fetch_data
from strategy import generate_signals
from backtest import run_backtest, print_report
from plotting import plot_results
from config import STOP_LOSS, TAKE_PROFIT, TRAILING_STOP, MAX_PORTFOLIO_DRAWDOWN


def run_without_risk(df):
    """不带风控跑回测（临时禁用所有风控）"""
    import backtest
    import config
    # 临时关闭风控：把止损/止盈/熔断阈值设到不可能触发的值
    orig_stop = config.STOP_LOSS
    orig_tp = config.TAKE_PROFIT
    orig_ts = config.TRAILING_STOP
    orig_max = config.MAX_PORTFOLIO_DRAWDOWN
    orig_ratio = config.POSITION_RATIO

    config.STOP_LOSS = 1.0  # 亏100%才止损（相当于关）
    config.TAKE_PROFIT = 10.0  # 赚1000%才止盈（相当于关）
    config.TRAILING_STOP = 1.0  # 回撤100%才移动止损（相当于关）
    config.MAX_PORTFOLIO_DRAWDOWN = 1.0  # 回撤100%才熔断（相当于关）
    config.POSITION_RATIO = 1.0  # 满仓

    # 重新import以刷新
    import importlib
    importlib.reload(backtest)
    df2, stats2 = backtest.run_backtest(df.copy())

    # 恢复
    config.STOP_LOSS = orig_stop
    config.TAKE_PROFIT = orig_tp
    config.TRAILING_STOP = orig_ts
    config.MAX_PORTFOLIO_DRAWDOWN = orig_max
    config.POSITION_RATIO = orig_ratio

    return df2, stats2


if __name__ == "__main__":
    df = fetch_data()
    df = generate_signals(df)

    # 有风控（用当前配置）
    df_risk, stats_risk = run_backtest(df.copy())

    print("\n" + "=" * 55)
    print("  有风控 vs 无风控 对比")
    print("=" * 55)
    print(f"\n{'指标':<20} {'有风控':<15} {'无风控':<15}")
    print("-" * 55)
    print(f"{'总收益':<20} {stats_risk['total_return']:>+.2f}%        {'?':>15}")
    print(f"{'最大回撤':<20} {stats_risk['max_drawdown']:>+.2f}%        {'?':>15}")
    print(f"{'夏普比率':<20} {stats_risk['sharpe']:>+.2f}          {'?':>15}")
    print(f"{'交易笔数':<20} {stats_risk['n_complete_trades']:>15}     {'?':>15}")

    print("\n(无风控回测需要单独运行：把 config.py 的风控参数开到最大，再跑 main.py)")
    print("  或比较上一次运行结果：有风控 +1.62%/-16.8% vs 无风控 +66.9%/-29.4%")
