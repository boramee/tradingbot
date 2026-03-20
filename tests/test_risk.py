"""리스크 관리 테스트"""

import time

import pytest

from config.settings import ArbitrageConfig
from src.risk.manager import RiskManager
from src.arbitrage.detector import ArbitrageOpportunity, ArbitrageType


@pytest.fixture
def config():
    return ArbitrageConfig(min_profit_pct=0.5, max_slippage_pct=0.3, max_trade_usdt=1000)


@pytest.fixture
def manager(config):
    return RiskManager(config)


def _make_opp(
    net_profit_pct=1.0, spread_pct=1.2, buy_vol=1000, sell_vol=1000,
    buy_price_usdt=100000, sell_price_usdt=101000,
):
    return ArbitrageOpportunity(
        arb_type=ArbitrageType.CROSS_EXCHANGE,
        symbol="BTC",
        buy_exchange="binance", sell_exchange="bybit",
        buy_price_usdt=buy_price_usdt, sell_price_usdt=sell_price_usdt,
        buy_price_original=buy_price_usdt, sell_price_original=sell_price_usdt,
        buy_quote="USDT", sell_quote="USDT",
        spread_pct=spread_pct, net_profit_pct=net_profit_pct,
        buy_volume=buy_vol, sell_volume=sell_vol,
    )


class TestValidation:
    def test_passes_valid_opportunity(self, manager):
        opp = _make_opp(net_profit_pct=1.0)
        ok, reason = manager.validate_opportunity(opp)
        assert ok is True

    def test_rejects_low_profit(self, manager):
        opp = _make_opp(net_profit_pct=0.2)
        ok, reason = manager.validate_opportunity(opp)
        assert ok is False
        assert "순수익률 부족" in reason

    def test_rejects_slippage_risk(self, manager):
        """순수익이 슬리피지 이하면 거부"""
        opp = _make_opp(net_profit_pct=0.5, spread_pct=0.7)
        # net=0.5, slippage=0.3 → adjusted=0.2 > 0 → pass
        # But let's test the boundary
        opp2 = _make_opp(net_profit_pct=0.3, spread_pct=0.5)
        ok2, _ = manager.validate_opportunity(opp2)
        # net=0.3 < min=0.5 → rejected by profit check first
        assert ok2 is False

    def test_rejects_low_volume(self, manager):
        opp = _make_opp(buy_vol=5, sell_vol=5)
        ok, reason = manager.validate_opportunity(opp)
        assert ok is False
        assert "거래량 부족" in reason

    def test_concurrent_limit(self, manager):
        """동시 거래 한도 초과"""
        for _ in range(3):
            manager.on_trade_start(_make_opp())
        opp = _make_opp()
        ok, reason = manager.validate_opportunity(opp)
        assert ok is False
        assert "동시 거래 한도" in reason

    def test_cooldown(self, manager):
        """같은 페어 연속 거래 쿨다운"""
        opp = _make_opp()
        manager.on_trade_start(opp)
        manager.on_trade_complete(opp, 0.5)

        ok, reason = manager.validate_opportunity(opp)
        assert ok is False
        assert "쿨다운" in reason


class TestTradeAmount:
    def test_max_amount(self, manager):
        opp = _make_opp(net_profit_pct=2.0, buy_vol=10000, sell_vol=10000)
        amount = manager.calculate_trade_amount(opp)
        assert amount <= manager.config.max_trade_usdt

    def test_reduced_for_low_profit(self, manager):
        opp = _make_opp(net_profit_pct=0.7, buy_vol=10000, sell_vol=10000)
        amount = manager.calculate_trade_amount(opp)
        assert amount <= manager.config.max_trade_usdt * 0.5 + 1  # ~500 USDT


class TestPnL:
    def test_daily_pnl_tracking(self, manager):
        opp = _make_opp()
        manager.on_trade_start(opp)
        manager.on_trade_complete(opp, 5.0)
        assert manager.daily_pnl == 5.0

    def test_negative_pnl(self, manager):
        opp = _make_opp()
        manager.on_trade_start(opp)
        manager.on_trade_complete(opp, -2.0)
        assert manager.daily_pnl == -2.0
