"""리스크 관리 테스트"""

from datetime import datetime, timedelta

import pytest

from src.risk.manager import RiskManager, RiskConfig, RiskState


@pytest.fixture
def risk_manager():
    config = RiskConfig(
        max_buy_amount=1_000_000,
        max_hold_qty=100,
        stop_loss_pct=3.0,
        take_profit_pct=5.0,
        max_daily_loss=500_000,
        cooldown_minutes=5,
    )
    return RiskManager(config)


class TestCanBuy:
    def test_can_buy_normal(self, risk_manager):
        ok, msg = risk_manager.can_buy(price=70000, current_qty=0, cash_balance=5_000_000)
        assert ok

    def test_cannot_buy_max_qty(self, risk_manager):
        ok, msg = risk_manager.can_buy(price=70000, current_qty=100, cash_balance=5_000_000)
        assert not ok
        assert "보유수량" in msg

    def test_cannot_buy_no_cash(self, risk_manager):
        ok, msg = risk_manager.can_buy(price=70000, current_qty=0, cash_balance=10000)
        assert not ok
        assert "예수금" in msg

    def test_cannot_buy_max_amount(self, risk_manager):
        ok, msg = risk_manager.can_buy(price=2_000_000, current_qty=0, cash_balance=5_000_000)
        assert not ok
        assert "매수금액" in msg

    def test_cannot_buy_daily_loss(self, risk_manager):
        risk_manager.state.daily_pnl = -600_000
        risk_manager.state.daily_reset_date = datetime.now().strftime("%Y-%m-%d")
        ok, msg = risk_manager.can_buy(price=70000, current_qty=0, cash_balance=5_000_000)
        assert not ok
        assert "손실" in msg

    def test_cannot_buy_cooldown(self, risk_manager):
        risk_manager.state.last_trade_time = datetime.now()
        ok, msg = risk_manager.can_buy(price=70000, current_qty=0, cash_balance=5_000_000)
        assert not ok
        assert "쿨다운" in msg

    def test_can_buy_after_cooldown(self, risk_manager):
        risk_manager.state.last_trade_time = datetime.now() - timedelta(minutes=10)
        ok, msg = risk_manager.can_buy(price=70000, current_qty=0, cash_balance=5_000_000)
        assert ok


class TestCanSell:
    def test_can_sell_with_holdings(self, risk_manager):
        ok, msg = risk_manager.can_sell(current_qty=10)
        assert ok

    def test_cannot_sell_no_holdings(self, risk_manager):
        ok, msg = risk_manager.can_sell(current_qty=0)
        assert not ok
        assert "수량" in msg


class TestStopLoss:
    def test_stop_loss_triggered(self, risk_manager):
        assert risk_manager.check_stop_loss(avg_price=70000, current_price=67500)

    def test_stop_loss_not_triggered(self, risk_manager):
        assert not risk_manager.check_stop_loss(avg_price=70000, current_price=69000)

    def test_stop_loss_zero_avg(self, risk_manager):
        assert not risk_manager.check_stop_loss(avg_price=0, current_price=70000)


class TestTakeProfit:
    def test_take_profit_triggered(self, risk_manager):
        assert risk_manager.check_take_profit(avg_price=70000, current_price=74000)

    def test_take_profit_not_triggered(self, risk_manager):
        assert not risk_manager.check_take_profit(avg_price=70000, current_price=72000)


class TestBuyQty:
    def test_calculate_qty(self, risk_manager):
        qty = risk_manager.calculate_buy_qty(price=70000, cash_balance=5_000_000)
        assert qty == 14  # 1,000,000 / 70,000 = 14

    def test_calculate_qty_low_cash(self, risk_manager):
        qty = risk_manager.calculate_buy_qty(price=70000, cash_balance=100_000)
        assert qty == 1

    def test_calculate_qty_too_expensive(self, risk_manager):
        qty = risk_manager.calculate_buy_qty(price=1_500_000, cash_balance=1_000_000)
        assert qty == 0


class TestPnl:
    def test_positive_pnl(self, risk_manager):
        pnl = risk_manager.get_pnl_pct(avg_price=70000, current_price=73500)
        assert pnl == pytest.approx(5.0, rel=0.01)

    def test_negative_pnl(self, risk_manager):
        pnl = risk_manager.get_pnl_pct(avg_price=70000, current_price=67900)
        assert pnl == pytest.approx(-3.0, rel=0.01)

    def test_zero_avg(self, risk_manager):
        pnl = risk_manager.get_pnl_pct(avg_price=0, current_price=70000)
        assert pnl == 0.0


class TestRecordTrade:
    def test_record_updates_state(self, risk_manager):
        risk_manager.record_trade(pnl=5000)
        assert risk_manager.state.daily_pnl == 5000
        assert risk_manager.state.trade_count == 1
        assert risk_manager.state.last_trade_time is not None

    def test_record_multiple(self, risk_manager):
        risk_manager.record_trade(pnl=5000)
        risk_manager.state.last_trade_time = datetime.now() - timedelta(minutes=10)
        risk_manager.record_trade(pnl=-3000)
        assert risk_manager.state.daily_pnl == 2000
        assert risk_manager.state.trade_count == 2


class TestDailyReset:
    def test_reset_new_day(self, risk_manager):
        risk_manager.state.daily_pnl = -100000
        risk_manager.state.trade_count = 5
        risk_manager.state.daily_reset_date = "2020-01-01"
        risk_manager.state.reset_if_new_day()
        assert risk_manager.state.daily_pnl == 0.0
        assert risk_manager.state.trade_count == 0
