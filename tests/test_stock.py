"""주식 매매 엔진 테스트"""

from __future__ import annotations

import datetime
from unittest.mock import patch, MagicMock

import pytest

from src.stock.stock_engine import StockEngine, StockPosition, MARKET_OPEN, MARKET_CLOSE


class TestStockPosition:
    def test_empty(self):
        p = StockPosition(code="005930")
        assert p.is_holding is False

    def test_holding(self):
        p = StockPosition(code="005930", avg_price=70000, quantity=10)
        assert p.is_holding is True

    def test_update_highest(self):
        p = StockPosition(code="005930", highest_price=70000)
        p.update_highest(72000)
        assert p.highest_price == 72000
        p.update_highest(71000)
        assert p.highest_price == 72000


class TestMarketHours:
    @patch("src.stock.stock_engine.datetime")
    def test_weekday_open(self, mock_dt):
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 20, 10, 0)  # 금요일 10시
        mock_dt.time = datetime.time
        assert StockEngine.is_market_open() is True

    @patch("src.stock.stock_engine.datetime")
    def test_weekend_closed(self, mock_dt):
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 21, 10, 0)  # 토요일
        mock_dt.time = datetime.time
        assert StockEngine.is_market_open() is False

    @patch("src.stock.stock_engine.datetime")
    def test_before_open(self, mock_dt):
        mock_dt.datetime.now.return_value = datetime.datetime(2026, 3, 20, 8, 30)
        mock_dt.time = datetime.time
        assert StockEngine.is_market_open() is False


class TestStopLoss:
    def test_fixed_stop(self):
        engine = StockEngine(stock_code="005930")
        engine.position.avg_price = 70000
        assert engine._check_stop_loss(68000) is True  # -2.8%
        assert engine._check_stop_loss(69000) is False  # -1.4%

    def test_atr_stop(self):
        engine = StockEngine(stock_code="005930")
        engine.position.avg_price = 70000
        engine.position.entry_atr = 500  # ATR=500, x2 = 손절가 69000
        assert engine._check_stop_loss(68900) is True
        assert engine._check_stop_loss(69100) is False


class TestTrailing:
    def test_not_active_below_threshold(self):
        engine = StockEngine(stock_code="005930")
        engine.position.avg_price = 70000
        engine.position.highest_price = 71000  # +1.4% < 3%
        assert engine._check_trailing(70500) is False

    def test_triggers(self):
        engine = StockEngine(stock_code="005930")
        engine.position.avg_price = 70000
        engine.position.highest_price = 75000  # +7.1%
        # gain = (71500-70000)/70000 = 2.1% → 여전히 < 3%... 더 높은 가격 필요
        # gain >= 3% 이면서 최고점 대비 1.5% 이상 하락
        # 73000 → gain=4.3% ≥ 3%, 75000에서 1.5% = 73875 이하
        assert engine._check_trailing(73500) is True

    def test_not_yet(self):
        engine = StockEngine(stock_code="005930")
        engine.position.avg_price = 70000
        engine.position.highest_price = 73000
        assert engine._check_trailing(72800) is False
