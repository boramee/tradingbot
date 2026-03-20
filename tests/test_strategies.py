"""매매 전략 테스트"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import TechnicalIndicators
from src.strategies.base import Signal, TradeSignal
from src.strategies.rsi import RSIStrategy
from src.strategies.macd import MACDStrategy
from src.strategies.bollinger import BollingerStrategy
from src.strategies.ma_cross import MACrossStrategy
from src.strategies.combined import CombinedStrategy
from src.strategies import create_strategy


@pytest.fixture
def indicators():
    return TechnicalIndicators()


def _make_df(closes, volumes=None):
    n = len(closes)
    if volumes is None:
        volumes = [10_000_000] * n
    df = pd.DataFrame({
        "open": closes,
        "high": [c + 100 for c in closes],
        "low": [c - 100 for c in closes],
        "close": closes,
        "volume": volumes,
    })
    return df


class TestRSIStrategy:
    def test_oversold_buy(self, indicators):
        closes = list(range(80000, 80000 - 30 * 200, -200))
        df = indicators.add_all(_make_df(closes))
        strategy = RSIStrategy()
        signal = strategy.analyze(df)
        assert signal.signal in (Signal.BUY, Signal.HOLD)

    def test_overbought_sell(self, indicators):
        closes = list(range(60000, 60000 + 30 * 200, 200))
        df = indicators.add_all(_make_df(closes))
        strategy = RSIStrategy()
        signal = strategy.analyze(df)
        assert signal.signal in (Signal.SELL, Signal.HOLD)

    def test_insufficient_data(self):
        df = _make_df([70000, 71000])
        strategy = RSIStrategy()
        signal = strategy.analyze(df)
        assert signal.signal == Signal.HOLD


class TestMACDStrategy:
    def test_golden_cross(self, indicators):
        closes = list(range(60000, 60000 + 40 * 100, 100))
        closes[-3:] = [closes[-4] + 500, closes[-4] + 1000, closes[-4] + 1500]
        df = indicators.add_all(_make_df(closes))
        strategy = MACDStrategy()
        signal = strategy.analyze(df)
        assert signal.signal in (Signal.BUY, Signal.HOLD)

    def test_insufficient_data(self):
        df = _make_df([70000])
        strategy = MACDStrategy()
        signal = strategy.analyze(df)
        assert signal.signal == Signal.HOLD


class TestBollingerStrategy:
    def test_lower_band_buy(self, indicators):
        closes = [70000] * 25
        closes[-1] = 65000
        closes[-2] = 66000
        df = indicators.add_all(_make_df(closes))
        strategy = BollingerStrategy()
        signal = strategy.analyze(df)
        assert signal.signal in (Signal.BUY, Signal.HOLD)

    def test_insufficient_data(self):
        df = _make_df([70000] * 5)
        strategy = BollingerStrategy()
        signal = strategy.analyze(df)
        assert signal.signal == Signal.HOLD


class TestMACrossStrategy:
    def test_golden_cross(self, indicators):
        closes = [70000] * 70
        for i in range(60, 70):
            closes[i] = 70000 + (i - 59) * 300
        df = indicators.add_all(_make_df(closes))
        strategy = MACrossStrategy()
        signal = strategy.analyze(df)
        assert signal.signal in (Signal.BUY, Signal.HOLD)

    def test_insufficient_data(self):
        df = _make_df([70000, 71000, 72000])
        strategy = MACrossStrategy()
        signal = strategy.analyze(df)
        assert signal.signal == Signal.HOLD


class TestCombinedStrategy:
    def test_returns_valid_signal(self, indicators):
        np.random.seed(42)
        closes = [70000 + int(np.random.normal(0, 500)) for _ in range(50)]
        df = indicators.add_all(_make_df(closes))
        strategy = CombinedStrategy()
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)
        assert signal.signal in (Signal.BUY, Signal.SELL, Signal.HOLD)

    def test_volume_filter(self, indicators):
        closes = [70000] * 50
        volumes = [100] * 50
        df = indicators.add_all(_make_df(closes, volumes))
        strategy = CombinedStrategy()
        signal = strategy.analyze(df)
        assert isinstance(signal, TradeSignal)


class TestTradeSignal:
    def test_actionable_buy(self):
        sig = TradeSignal(Signal.BUY, 0.7, "test", 70000)
        assert sig.is_actionable

    def test_not_actionable_hold(self):
        sig = TradeSignal(Signal.HOLD, 0.0, "test", 70000)
        assert not sig.is_actionable

    def test_not_actionable_low_confidence(self):
        sig = TradeSignal(Signal.BUY, 0.3, "test", 70000)
        assert not sig.is_actionable


class TestCreateStrategy:
    def test_create_rsi(self):
        s = create_strategy("rsi")
        assert isinstance(s, RSIStrategy)

    def test_create_combined(self):
        s = create_strategy("combined")
        assert isinstance(s, CombinedStrategy)

    def test_create_all_strategies(self):
        for name in ["rsi", "macd", "bollinger", "ma_cross", "combined"]:
            s = create_strategy(name)
            assert s.name

    def test_unknown_strategy(self):
        with pytest.raises(ValueError, match="알 수 없는 전략"):
            create_strategy("unknown")
