"""매매 전략 테스트"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import TechnicalIndicators
from src.strategies import Signal, TradeSignal, RSIStrategy, MACDStrategy, BollingerStrategy, CombinedStrategy


@pytest.fixture
def ti():
    return TechnicalIndicators()


def _df(prices):
    n = len(prices)
    close = np.array(prices, dtype=float)
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": np.full(n, 1000.0),
    }, index=pd.date_range("2025-01-01", periods=n, freq="h"))


class TestRSI:
    def test_downtrend_buy(self, ti):
        df = ti.add_all(_df(list(np.linspace(60000, 40000, 80))))
        sig = RSIStrategy().analyze(df)
        assert sig.signal in (Signal.BUY, Signal.HOLD)

    def test_uptrend_sell(self, ti):
        df = ti.add_all(_df(list(np.linspace(40000, 80000, 80))))
        sig = RSIStrategy().analyze(df)
        assert sig.signal in (Signal.SELL, Signal.HOLD)


class TestMACD:
    def test_valid_signal(self, ti):
        np.random.seed(42)
        df = ti.add_all(_df(50000 + np.cumsum(np.random.randn(100) * 500)))
        sig = MACDStrategy().analyze(df)
        assert isinstance(sig, TradeSignal)

    def test_no_data(self):
        sig = MACDStrategy().analyze(pd.DataFrame({"close": [50000]}))
        assert sig.signal == Signal.HOLD


class TestBollinger:
    def test_valid_signal(self, ti):
        np.random.seed(42)
        df = ti.add_all(_df(50000 + np.cumsum(np.random.randn(100) * 500)))
        sig = BollingerStrategy().analyze(df)
        assert isinstance(sig, TradeSignal)


class TestCombined:
    def test_bounded_confidence(self, ti):
        np.random.seed(42)
        df = ti.add_all(_df(50000 + np.cumsum(np.random.randn(100) * 500)))
        sig = CombinedStrategy().analyze(df)
        assert 0.0 <= sig.confidence <= 1.0


class TestTradeSignal:
    def test_actionable(self):
        assert TradeSignal(Signal.BUY, 0.7, "t").is_actionable is True
        assert TradeSignal(Signal.BUY, 0.3, "t").is_actionable is False
        assert TradeSignal(Signal.HOLD, 1.0, "t").is_actionable is False
