"""
main.py - 主入口（含风控）

运行方式:
    python main.py

流程：
    1. 拉取A股数据
    2. 生成双均线策略信号
    3. 执行回测（含三层风控）
    4. 打印绩效报告
    5. 画图（含风控触发标注）
"""

from data_fetcher import fetch_data
from strategy import generate_signals
from backtest import run_backtest, print_report
from plotting import plot_results
from config import STOP_LOSS, TAKE_PROFIT, TRAILING_STOP, MAX_PORTFOLIO_DRAWDOWN, COOLDOWN_DAYS, POSITION_RATIO


def main():
    print("=" * 55)
    print("  A股双均线量化策略 + 三层风控系统")
    print("=" * 55)

    # 打印风控配置
    print("\n[风控配置]")
    print(f"  个股级: 止损{STOP_LOSS*100:.0f}% / 止盈{TAKE_PROFIT*100:.0f}% / 移动止损{TRAILING_STOP*100:.0f}%")
    print(f"  组合级: 最大回撤{MAX_PORTFOLIO_DRAWDOWN*100:.0f}%熔断 / 冷静{COOLDOWN_DAYS}天")
    print(f"  执行级: 仓位上限{POSITION_RATIO*100:.0f}% / 涨跌停保护")

    # 第1步：拉数据
    print("\n>>> 第1步: 获取行情数据")
    df = fetch_data()

    # 第2步：生成策略信号
    print("\n>>> 第2步: 生成交易信号")
    df = generate_signals(df)

    # 第3步：执行回测（含风控）
    print("\n>>> 第3步: 执行回测（含风控）")
    df, stats = run_backtest(df)

    # 第4步：打印绩效报告
    print_report(stats)

    # 第5步：画图
    print(">>> 第4步: 生成图表")
    plot_results(df, stats)

    print("\n回测完成！打开 backtest_result.png 查看结果。")
    print("\n下一步建议:")
    print("  1. 对比有无风控的差异：风控是否减少了最大回撤？")
    print("  2. 调风控参数：收紧止损(如5%)看效果，放松止盈(如30%)对比")
    print("  3. 思考：风控帮你避开了哪些大亏？代价是错过了哪些盈利？")


if __name__ == "__main__":
    main()
