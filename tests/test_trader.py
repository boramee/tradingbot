"""자동매매 엔진 테스트"""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import pandas as pd
import numpy as np
import pytest

from src.trader.engine import TraderEngine, Position


@pytest.fixture
def engine():
    return TraderEngine(ticker="KRW-BTC", strategy_name="combined")


def _fake_ohlcv(*args, **kwargs):
    np.random.seed(42)
    n = 100
    close = 50000 + np.cumsum(np.random.randn(n) * 500)
    return pd.DataFrame({
        "open": close * 0.999, "high": close * 1.002,
        "low": close * 0.998, "close": close,
        "volume": np.full(n, 1000.0),
        "value": np.full(n, 5e7),
    }, index=pd.date_range("2025-01-01", periods=n, freq="h"))


class TestPosition:
    def test_empty(self):
        p = Position(ticker="KRW-BTC")
        assert p.is_holding is False

    def test_holding(self):
        p = Position(ticker="KRW-BTC", avg_price=50000, volume=0.01)
        assert p.is_holding is True


class TestStopLoss:
    def test_triggers(self, engine):
        engine.position.avg_price = 100000
        assert engine._check_stop_loss(96000) is True

    def test_no_trigger(self, engine):
        engine.position.avg_price = 100000
        assert engine._check_stop_loss(98000) is False

    def test_no_position(self, engine):
        assert engine._check_stop_loss(50000) is False


class TestTakeProfit:
    def test_triggers(self, engine):
        engine.position.avg_price = 100000
        assert engine._check_take_profit(106000) is True

    def test_no_trigger(self, engine):
        engine.position.avg_price = 100000
        assert engine._check_take_profit(103000) is False


class TestRunOnce:
    @patch("src.trader.engine.pyupbit")
    def test_runs_without_error(self, mock_pyupbit, engine):
        mock_pyupbit.get_ohlcv.return_value = _fake_ohlcv()
        mock_pyupbit.get_current_price.return_value = 50000
        engine.run_once()

    @patch("src.trader.engine.pyupbit")
    def test_no_data_handled(self, mock_pyupbit, engine):
        mock_pyupbit.get_ohlcv.return_value = None
        engine.run_once()
