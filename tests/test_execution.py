"""실행 엔진 테스트"""

from unittest.mock import MagicMock

import pytest

from config.settings import ArbitrageConfig
from src.arbitrage.detector import ArbitrageOpportunity, ArbitrageType
from src.execution.engine import ExecutionEngine, ExecutionResult
from src.risk.manager import RiskManager
from src.monitor.fx_rate import FXRateProvider


def _make_opp(net_profit=1.0, buy_vol=1000, sell_vol=1000):
    return ArbitrageOpportunity(
        arb_type=ArbitrageType.CROSS_EXCHANGE,
        symbol="BTC",
        buy_exchange="binance", sell_exchange="bybit",
        buy_price_usdt=100000, sell_price_usdt=101000,
        buy_price_original=100000, sell_price_original=101000,
        buy_quote="USDT", sell_quote="USDT",
        spread_pct=1.2, net_profit_pct=net_profit,
        buy_volume=buy_vol, sell_volume=sell_vol,
    )


@pytest.fixture
def config():
    return ArbitrageConfig(min_profit_pct=0.5, max_slippage_pct=0.3, max_trade_usdt=1000)


@pytest.fixture
def fx():
    provider = FXRateProvider()
    provider._cached_rate = 1350.0
    provider._cache_time = __import__("time").time()
    return provider


class TestExecutionEngine:
    def test_dry_run_simulation(self, config, fx):
        """시뮬레이션 모드 테스트"""
        exchanges = {
            "binance": MagicMock(name="binance", is_korean=False, fee_rate=0.001),
            "bybit": MagicMock(name="bybit", is_korean=False, fee_rate=0.001),
        }
        risk = RiskManager(config)
        engine = ExecutionEngine(exchanges, risk, config, fx)
        engine.dry_run = True

        opp = _make_opp(net_profit=1.0)
        result = engine.execute(opp)

        assert isinstance(result, ExecutionResult)
        assert result.success is True
        assert result.actual_profit_usdt > 0

    def test_rejects_invalid_opportunity(self, config, fx):
        """수익률 낮은 기회는 거부"""
        exchanges = {"binance": MagicMock(), "bybit": MagicMock()}
        risk = RiskManager(config)
        engine = ExecutionEngine(exchanges, risk, config, fx)

        opp = _make_opp(net_profit=0.1)
        result = engine.execute(opp)
        assert result.success is False

    def test_missing_exchange(self, config, fx):
        """거래소가 없으면 실패"""
        exchanges = {"binance": MagicMock()}
        risk = RiskManager(config)
        engine = ExecutionEngine(exchanges, risk, config, fx)

        opp = _make_opp()
        result = engine.execute(opp)
        assert result.success is False
        assert "거래소 없음" in result.error

    def test_dry_run_default(self, config, fx):
        """기본 모드는 시뮬레이션"""
        exchanges = {}
        risk = RiskManager(config)
        engine = ExecutionEngine(exchanges, risk, config, fx)
        assert engine.dry_run is True
