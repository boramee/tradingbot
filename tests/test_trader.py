"""매매 엔진 테스트"""

from unittest.mock import MagicMock, patch
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from config.settings import AppConfig, KISConfig, TradingConfig, TelegramConfig
from src.api.kis_client import Position, StockPrice, OrderResult
from src.strategies.base import Signal, TradeSignal
from src.trader.engine import TraderEngine


def _make_config() -> AppConfig:
    config = AppConfig()
    config.kis = KISConfig(
        app_key="test_key",
        app_secret="test_secret",
        account_no="12345678-01",
        is_paper=True,
    )
    config.trading = TradingConfig(
        stock_code="005930",
        stock_name="삼성전자",
        strategy="combined",
        max_buy_amount=1_000_000,
        max_hold_qty=100,
        stop_loss_pct=3.0,
        take_profit_pct=5.0,
        poll_interval_sec=60,
    )
    config.telegram = TelegramConfig(token="", chat_id="")
    return config


def _make_ohlcv(n=50, base=70000):
    np.random.seed(42)
    closes = [base + int(np.random.normal(0, 500)) for _ in range(n)]
    df = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="D"),
        "open": [c + np.random.randint(-300, 300) for c in closes],
        "high": [c + abs(np.random.normal(0, 400)) for c in closes],
        "low": [c - abs(np.random.normal(0, 400)) for c in closes],
        "close": closes,
        "volume": [np.random.randint(5_000_000, 30_000_000) for _ in range(n)],
    })
    return df


@pytest.fixture
def engine():
    config = _make_config()
    e = TraderEngine(config, dry_run=True)
    return e


class TestRunOnce:
    def test_run_once_returns_signal(self, engine):
        engine._fetch_ohlcv = MagicMock(return_value=_make_ohlcv())
        engine._get_current_price = MagicMock(return_value=70000)
        engine._get_position = MagicMock(return_value=None)
        engine._get_cash_balance = MagicMock(return_value=5_000_000)

        result = engine.run_once()
        assert result is not None
        assert isinstance(result, TradeSignal)

    def test_run_once_insufficient_data(self, engine):
        engine._fetch_ohlcv = MagicMock(return_value=pd.DataFrame())
        result = engine.run_once()
        assert result is None

    def test_run_once_with_position(self, engine):
        engine._fetch_ohlcv = MagicMock(return_value=_make_ohlcv())
        engine._get_current_price = MagicMock(return_value=70000)
        engine._get_position = MagicMock(return_value=Position(
            code="005930", name="삼성전자", qty=10,
            avg_price=70000, current_price=70000, pnl=0, pnl_pct=0,
        ))
        engine._get_cash_balance = MagicMock(return_value=5_000_000)

        result = engine.run_once()
        assert result is not None


class TestStopLoss:
    def test_stop_loss_triggers_sell(self, engine):
        engine._fetch_ohlcv = MagicMock(return_value=_make_ohlcv())
        engine._get_current_price = MagicMock(return_value=67000)
        engine._get_position = MagicMock(return_value=Position(
            code="005930", name="삼성전자", qty=10,
            avg_price=70000, current_price=67000, pnl=-30000, pnl_pct=-4.3,
        ))
        engine._get_cash_balance = MagicMock(return_value=5_000_000)

        result = engine.run_once()
        assert result is not None
        assert result.signal == Signal.SELL

    def test_take_profit_triggers_sell(self, engine):
        engine._fetch_ohlcv = MagicMock(return_value=_make_ohlcv())
        engine._get_current_price = MagicMock(return_value=74000)
        engine._get_position = MagicMock(return_value=Position(
            code="005930", name="삼성전자", qty=10,
            avg_price=70000, current_price=74000, pnl=40000, pnl_pct=5.7,
        ))
        engine._get_cash_balance = MagicMock(return_value=5_000_000)

        result = engine.run_once()
        assert result is not None
        assert result.signal == Signal.SELL


class TestTradingTime:
    def test_weekday_trading_time(self, engine):
        with patch("src.trader.engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 10, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = engine._is_trading_time()
            assert result is True

    def test_weekend_not_trading(self, engine):
        with patch("src.trader.engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 21, 10, 30)  # Saturday
            result = engine._is_trading_time()
            assert result is False

    def test_after_hours_not_trading(self, engine):
        with patch("src.trader.engine.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 20, 20, 0)
            result = engine._is_trading_time()
            assert result is False


class TestDryRun:
    def test_dry_run_buy_no_real_order(self, engine):
        engine._fetch_ohlcv = MagicMock(return_value=_make_ohlcv())
        engine._get_current_price = MagicMock(return_value=70000)
        engine._get_position = MagicMock(return_value=None)
        engine._get_cash_balance = MagicMock(return_value=5_000_000)
        engine.strategy = MagicMock()
        engine.strategy.analyze.return_value = TradeSignal(Signal.BUY, 0.8, "테스트매수", 70000)
        engine.strategy.name = "Test"

        engine.kis.buy_market = MagicMock()
        result = engine.run_once()

        engine.kis.buy_market.assert_not_called()

    def test_dry_run_sell_no_real_order(self, engine):
        position = Position(
            code="005930", name="삼성전자", qty=10,
            avg_price=70000, current_price=74000, pnl=40000, pnl_pct=5.7,
        )
        engine._fetch_ohlcv = MagicMock(return_value=_make_ohlcv())
        engine._get_current_price = MagicMock(return_value=74000)
        engine._get_position = MagicMock(return_value=position)
        engine._get_cash_balance = MagicMock(return_value=5_000_000)

        engine.kis.sell_market = MagicMock()
        result = engine.run_once()

        engine.kis.sell_market.assert_not_called()
