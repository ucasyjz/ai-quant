"""
risk_manager.py - 风控管理器

三层风控体系，按优先级从高到低：
    1. 组合级熔断（最高优先级）—— 整体亏太多，清仓停止交易
    2. 个股级止损止盈 —— 单笔交易到红线就砍
    3. 执行级保护 —— 涨跌停不交易、仓位不超限

为什么风控比策略重要？
    再好的策略也会遇到连续亏损。没有风控，一次极端行情就能让你爆仓。
    有了风控，你最差也是"亏一个可控的数字"而不是"归零"。
"""

from config import (
    STOP_LOSS, TAKE_PROFIT, TRAILING_STOP,
    MAX_PORTFOLIO_DRAWDOWN, COOLDOWN_DAYS,
    POSITION_RATIO, LIMIT_UP_THRESHOLD, LIMIT_DOWN_THRESHOLD,
    INITIAL_CASH,
)


class RiskManager:
    """
    风控管理器：在回测循环中逐日检查，决定是否允许交易。

    使用方式：
        rm = RiskManager()
        # 每天循环里：
        #   1. 先检查组合熔断
        #   2. 再检查个股止损止盈
        #   3. 最后检查执行级限制（涨跌停、仓位）
    """

    def __init__(self):
        # === 个股状态 ===
        self.entry_price = 0.0       # 买入价
        self.highest_since_entry = 0.0  # 买入后持仓最高价（用于移动止损）

        # === 组合状态 ===
        self.peak_portfolio = INITIAL_CASH  # 组合历史最高值
        self.cooldown_remaining = 0          # 冷静期剩余天数
        self.circuit_breaker_triggered = False  # 是否已触发熔断

        # === 统计 ===
        self.stop_loss_hits = 0       # 止损触发次数
        self.take_profit_hits = 0     # 止盈触发次数
        self.trailing_stop_hits = 0   # 移动止损触发次数
        self.circuit_breaker_hits = 0 # 熔断触发次数
        self.limit_up_blocks = 0      # 涨停拦截次数
        self.limit_down_blocks = 0    # 跌停拦截次数

    def update_portfolio_peak(self, portfolio_value):
        """更新组合历史最高值"""
        if portfolio_value > self.peak_portfolio:
            self.peak_portfolio = portfolio_value

    def get_portfolio_drawdown(self, portfolio_value):
        """计算当前组合相对历史最高点的回撤比例"""
        if self.peak_portfolio <= 0:
            return 0.0
        return (portfolio_value - self.peak_portfolio) / self.peak_portfolio

    def check_circuit_breaker(self, portfolio_value):
        """
        检查组合级熔断。
        返回 True 表示触发熔断（应清仓），False 表示正常。
        """
        # 更新历史最高值
        self.update_portfolio_peak(portfolio_value)
        drawdown = self.get_portfolio_drawdown(portfolio_value)

        if drawdown <= -MAX_PORTFOLIO_DRAWDOWN and not self.circuit_breaker_triggered:
            # 触发熔断
            self.circuit_breaker_triggered = True
            self.cooldown_remaining = COOLDOWN_DAYS
            self.circuit_breaker_hits += 1
            return True

        return False

    def is_in_cooldown(self):
        """是否处于冷静期（冷静期内禁止买入）"""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1
            return True
        # 冷静期结束，解除熔断状态
        if self.circuit_breaker_triggered and self.cooldown_remaining == 0:
            self.circuit_breaker_triggered = False
        return False

    def check_exit(self, close, prev_close):
        """
        检查个股级止损止盈。返回 True 表示应强制卖出。

        三个条件，任一触发就卖：
            - 止损：亏损达到 STOP_LOSS
            - 止盈：盈利达到 TAKE_PROFIT
            - 移动止损：从持仓最高价回撤达 TRAILING_STOP
        """
        if self.entry_price <= 0:
            return False

        # 更新持仓最高价
        if close > self.highest_since_entry:
            self.highest_since_entry = close

        # 计算盈亏比例
        pnl_ratio = (close - self.entry_price) / self.entry_price

        # 1. 止损
        if pnl_ratio <= -STOP_LOSS:
            self.stop_loss_hits += 1
            return True

        # 2. 止盈
        if pnl_ratio >= TAKE_PROFIT:
            self.take_profit_hits += 1
            return True

        # 3. 移动止损（从最高点回撤）
        if self.highest_since_entry > self.entry_price:
            # 只在盈利时启用移动止损，锁定利润
            retrace = (close - self.highest_since_entry) / self.highest_since_entry
            if retrace <= -TRAILING_STOP:
                self.trailing_stop_hits += 1
                return True

        return False

    def check_entry(self, close, prev_close, portfolio_value):
        """
        检查是否允许买入。返回 (allowed, reason)。

        检查项：
            1. 冷静期内禁止买入
            2. 涨停不买入（买不进）
        """
        # 1. 冷静期
        if self.is_in_cooldown():
            return False, "cooldown"

        # 2. 涨跌停保护
        if prev_close > 0:
            daily_return = (close - prev_close) / prev_close
            if daily_return >= LIMIT_UP_THRESHOLD:
                self.limit_up_blocks += 1
                return False, "limit_up"

        return True, "ok"

    def check_limit_down_sell(self, close, prev_close):
        """
        检查卖出时是否处于跌停（卖不出）。
        返回 True 表示可以卖出，False 表示跌停无法卖出。
        """
        if prev_close > 0:
            daily_return = (close - prev_close) / prev_close
            if daily_return <= LIMIT_DOWN_THRESHOLD:
                self.limit_down_blocks += 1
                return False
        return True

    def on_buy(self, buy_price):
        """买入成交后更新状态"""
        self.entry_price = buy_price
        self.highest_since_entry = buy_price

    def on_sell(self):
        """卖出成交后重置个股状态"""
        self.entry_price = 0.0
        self.highest_since_entry = 0.0

    def get_position_size(self, cash):
        """
        计算单次买入的可用资金。
        仓位控制：只用总资金的 POSITION_RATIO 比例，不满仓。
        """
        return cash * POSITION_RATIO

    def get_stats(self):
        """返回风控触发统计"""
        return {
            "stop_loss_hits": self.stop_loss_hits,
            "take_profit_hits": self.take_profit_hits,
            "trailing_stop_hits": self.trailing_stop_hits,
            "circuit_breaker_hits": self.circuit_breaker_hits,
            "limit_up_blocks": self.limit_up_blocks,
            "limit_down_blocks": self.limit_down_blocks,
        }
