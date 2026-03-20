"""리스크 관리 테스트"""

import pytest

from config.settings import RiskConfig
from src.risk.manager import RiskManager
from src.strategies.base_strategy import Signal, TradeSignal


@pytest.fixture
def config():
    return RiskConfig(stop_loss_pct=3.0, take_profit_pct=5.0, max_daily_trades=10)


@pytest.fixture
def manager(config):
    return RiskManager(config)


class TestStopLoss:
    def test_triggers_on_loss(self, manager):
        assert manager.check_stop_loss(100000, 96000) is True

    def test_no_trigger_small_loss(self, manager):
        assert manager.check_stop_loss(100000, 98000) is False

    def test_no_trigger_zero_avg(self, manager):
        assert manager.check_stop_loss(0, 50000) is False


class TestTakeProfit:
    def test_triggers_on_profit(self, manager):
        assert manager.check_take_profit(100000, 106000) is True

    def test_no_trigger_small_profit(self, manager):
        assert manager.check_take_profit(100000, 103000) is False


class TestCanTrade:
    def test_can_trade_normally(self, manager):
        assert manager.can_trade() is True

    def test_blocked_after_max_trades(self, config):
        mgr = RiskManager(config)
        for _ in range(10):
            mgr.record_trade(True)
        assert mgr.can_trade() is False

    def test_blocked_after_consecutive_losses(self, manager):
        for _ in range(3):
            manager.record_trade(False)
        assert manager.can_trade() is False

    def test_reset_after_win(self, manager):
        manager.record_trade(False)
        manager.record_trade(False)
        manager.record_trade(True)
        assert manager.can_trade() is True


class TestValidateSignal:
    def test_stop_loss_overrides(self, manager):
        buy_signal = TradeSignal(Signal.BUY, 0.8, "test buy")
        result = manager.validate_signal(
            buy_signal,
            avg_buy_price=100000,
            current_price=96000,
            krw_balance=500000,
            holding_value=96000,
        )
        assert result.signal == Signal.SELL
        assert "[리스크]" in result.reason

    def test_take_profit_overrides(self, manager):
        hold_signal = TradeSignal(Signal.HOLD, 0.0, "test hold")
        result = manager.validate_signal(
            hold_signal,
            avg_buy_price=100000,
            current_price=106000,
            krw_balance=500000,
            holding_value=106000,
        )
        assert result.signal == Signal.SELL

    def test_position_limit(self, manager):
        buy_signal = TradeSignal(Signal.BUY, 0.8, "test buy")
        result = manager.validate_signal(
            buy_signal,
            avg_buy_price=50000,
            current_price=50000,
            krw_balance=100000,
            holding_value=300000,
        )
        assert result.signal == Signal.HOLD
        assert "포지션 비율 초과" in result.reason
