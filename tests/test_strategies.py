"""매매 전략 테스트"""

import numpy as np
import pandas as pd
import pytest

from config.settings import IndicatorConfig
from src.indicators.technical import TechnicalIndicators
from src.strategies.base_strategy import Signal, TradeSignal
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.macd_strategy import MACDStrategy
from src.strategies.bollinger_strategy import BollingerStrategy
from src.strategies.combined_strategy import CombinedStrategy


@pytest.fixture
def config():
    return IndicatorConfig()


@pytest.fixture
def indicators(config):
    return TechnicalIndicators(config)


def _make_df(close_values, n=100):
    """지정된 종가 패턴으로 DataFrame 생성"""
    close = np.array(close_values[-n:] if len(close_values) >= n else close_values)
    n = len(close)
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.002,
        "low": close * 0.998,
        "close": close,
        "volume": np.full(n, 1000.0),
    }, index=pd.date_range("2025-01-01", periods=n, freq="h"))


class TestRSIStrategy:
    def test_oversold_generates_buy(self, config, indicators):
        prices = list(np.linspace(60000, 40000, 80))
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = RSIStrategy(config)
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)
        assert signal.signal in (Signal.BUY, Signal.HOLD)

    def test_overbought_generates_sell(self, config, indicators):
        prices = list(np.linspace(40000, 80000, 80))
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = RSIStrategy(config)
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)
        assert signal.signal in (Signal.SELL, Signal.HOLD)

    def test_returns_trade_signal(self, config, indicators):
        np.random.seed(42)
        prices = 50000 + np.cumsum(np.random.randn(100) * 500)
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = RSIStrategy(config)
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)
        assert signal.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)


class TestMACDStrategy:
    def test_returns_valid_signal(self, config, indicators):
        np.random.seed(42)
        prices = 50000 + np.cumsum(np.random.randn(100) * 500)
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = MACDStrategy(config)
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)

    def test_missing_data_returns_hold(self, config):
        df = pd.DataFrame({"close": [50000]})
        strategy = MACDStrategy(config)
        signal = strategy.analyze(df)
        assert signal.signal == Signal.HOLD


class TestBollingerStrategy:
    def test_returns_valid_signal(self, config, indicators):
        np.random.seed(42)
        prices = 50000 + np.cumsum(np.random.randn(100) * 500)
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = BollingerStrategy(config)
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)

    def test_missing_data_returns_hold(self, config):
        df = pd.DataFrame({"close": [50000]})
        strategy = BollingerStrategy(config)
        signal = strategy.analyze(df)
        assert signal.signal == Signal.HOLD


class TestCombinedStrategy:
    def test_returns_valid_signal(self, config, indicators):
        np.random.seed(42)
        prices = 50000 + np.cumsum(np.random.randn(100) * 500)
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = CombinedStrategy(config)
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)
        assert 0.0 <= signal.confidence <= 1.0

    def test_confidence_bounded(self, config, indicators):
        prices = list(np.linspace(60000, 40000, 80))
        df = _make_df(prices)
        df = indicators.add_all_indicators(df)

        strategy = CombinedStrategy(config)
        signal = strategy.analyze(df)
        assert 0.0 <= signal.confidence <= 1.0


class TestTradeSignal:
    def test_actionable_buy(self):
        sig = TradeSignal(Signal.BUY, 0.7, "test")
        assert sig.is_actionable is True

    def test_not_actionable_low_confidence(self):
        sig = TradeSignal(Signal.BUY, 0.3, "test")
        assert sig.is_actionable is False

    def test_hold_not_actionable(self):
        sig = TradeSignal(Signal.HOLD, 1.0, "test")
        assert sig.is_actionable is False
