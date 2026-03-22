"""자동매매 엔진 테스트"""

from __future__ import annotations

from unittest.mock import patch
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

    def test_update_highest(self):
        p = Position(ticker="KRW-BTC", avg_price=50000, volume=0.01, highest_price=52000)
        p.update_highest(53000)
        assert p.highest_price == 53000
        p.update_highest(51000)
        assert p.highest_price == 53000  # 최고가 유지


class TestATRStopLoss:
    def test_atr_stop_triggers(self, engine):
        """ATR 기반 동적 손절: avg - (ATR × 2) 이하이면 손절"""
        engine.position.avg_price = 100000
        engine.position.entry_atr = 2000  # ATR=2000, 배수=2.0 → 손절가=96000
        assert engine._check_stop_loss(95000) is True

    def test_atr_stop_no_trigger(self, engine):
        engine.position.avg_price = 100000
        engine.position.entry_atr = 2000
        assert engine._check_stop_loss(97000) is False

    def test_fallback_fixed_stop(self, engine):
        """ATR 없으면 고정 3% 손절"""
        engine.position.avg_price = 100000
        engine.position.entry_atr = 0
        assert engine._check_stop_loss(96000) is True
        assert engine._check_stop_loss(98000) is False

    def test_atr_stop_includes_fee_buffer(self, engine):
        """ATR 손절도 수수료만큼 더 이르게 발동한다."""
        engine.position.avg_price = 100000
        engine.position.entry_atr = 2000
        assert engine._check_stop_loss(96050) is True
        assert engine._check_stop_loss(96150) is False


class TestTrailingStop:
    def test_not_active_below_threshold(self, engine):
        """익절 기준(5%) 미달이면 트레일링 비활성"""
        engine.position.avg_price = 100000
        engine.position.highest_price = 103000
        assert engine._check_trailing_stop(101000) is False

    def test_trailing_triggers(self, engine):
        """5% 이상 수익 후 최고점 대비 2% 하락 시 익절"""
        engine.position.avg_price = 100000
        engine.position.highest_price = 110000  # +10% 최고
        # 110000 × 0.98 = 107800 이하이면 트레일링 발동
        assert engine._check_trailing_stop(107000) is True

    def test_trailing_not_yet(self, engine):
        """최고점 대비 아직 충분히 안 빠짐"""
        engine.position.avg_price = 100000
        engine.position.highest_price = 110000
        assert engine._check_trailing_stop(109000) is False

    def test_trailing_detail(self, engine):
        engine.position.avg_price = 100000
        engine.position.highest_price = 110000
        detail = engine._get_trailing_detail(107000)
        assert "트레일링" in detail
        assert "최고점" in detail


class TestPnlCalculation:
    def test_calc_pnl_includes_round_trip_fee(self, engine):
        engine.position.avg_price = 100000
        assert engine._calc_pnl(100100) == pytest.approx(0.0)
        assert engine._calc_pnl(99600) == pytest.approx(-0.5)


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
