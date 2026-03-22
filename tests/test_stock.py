"""주식 매매 엔진 테스트"""

from __future__ import annotations

import datetime
from unittest.mock import patch, MagicMock

import pandas as pd

from src.stock.kis_client import KISClient
from src.stock.scanner.stock_scanner import SECTOR_MAP
from src.stock.stock_engine import StockEngine, StockPosition
from src.strategies.base import Signal, TradeSignal


def _stock_df(rows: int = 60) -> pd.DataFrame:
    return pd.DataFrame({"close": [70000] * rows})


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


class TestAutoScan:
    def test_auto_scan_runs_before_current_symbol_signal(self):
        engine = StockEngine(stock_code="005930", auto_scan=True)
        engine._fetch_data = MagicMock()
        engine.strategy.analyze = MagicMock()
        engine._run_auto_scan = MagicMock()

        with patch.object(StockEngine, "get_trading_mode", return_value="normal"):
            engine.run_once()

        engine._run_auto_scan.assert_called_once()
        engine._fetch_data.assert_not_called()
        engine.strategy.analyze.assert_not_called()

    def test_auto_scan_restores_symbol_state_when_buy_fails(self):
        engine = StockEngine(stock_code="005930", auto_scan=True)
        engine._stock_name = "삼성전자"
        candidate = MagicMock(
            code="000660",
            name="SK하이닉스",
            score=55.0,
            reasons=["신고가돌파"],
            sector="반도체",
        )
        df = _stock_df()

        engine._pre_buy_checks = MagicMock(return_value=True)
        engine.scanner.get_best = MagicMock(return_value=candidate)
        engine.kis.get_ohlcv = MagicMock(return_value=df)
        engine.indicators.add_all = MagicMock(return_value=df)
        engine.strategy.analyze = MagicMock(
            return_value=TradeSignal(Signal.BUY, 0.9, "차트매수")
        )
        engine._buy = MagicMock(return_value=False)

        engine._run_auto_scan(0.0)

        assert engine.stock_code == "005930"
        assert engine._stock_name == "삼성전자"


class TestPositionSync:
    def test_sync_resets_stale_position_when_holding_missing(self):
        engine = StockEngine(stock_code="005930")
        engine._stock_name = "삼성전자"
        engine.position = StockPosition(
            code="005930", name="삼성전자", avg_price=70000, quantity=10, highest_price=72000
        )
        engine.kis.get_balance = MagicMock(return_value={"cash": 0, "holdings": []})

        engine._sync_position_from_balance()

        assert engine.position.code == "005930"
        assert engine.position.name == "삼성전자"
        assert engine.position.quantity == 0
        assert engine.position.avg_price == 0
        assert engine.position.highest_price == 0

    def test_sync_seeds_highest_price_from_balance(self):
        engine = StockEngine(stock_code="005930")
        engine.position.avg_price = 68000
        engine.position.entry_atr = 500
        engine.position.partial_sold = True
        engine.kis.get_balance = MagicMock(return_value={
            "cash": 0,
            "holdings": [{
                "code": "005930",
                "name": "삼성전자",
                "quantity": 10,
                "avg_price": 70000,
                "current_price": 71000,
                "pnl_pct": 1.4,
            }],
        })

        engine._sync_position_from_balance()

        assert engine.position.code == "005930"
        assert engine.position.name == "삼성전자"
        assert engine.position.quantity == 10
        assert engine.position.avg_price == 70000
        assert engine.position.highest_price == 71000
        assert engine.position.entry_atr == 0
        assert engine.position.partial_sold is False


class TestScannerConfig:
    def test_sector_map_has_no_duplicate_codes(self):
        codes = [code for members in SECTOR_MAP.values() for code in members]
        assert len(codes) == len(set(codes))


class TestKISClient:
    @patch("src.stock.kis_client.requests.get")
    def test_investor_trend_uses_positive_sign_for_net_buy(self, mock_get):
        mock_get.return_value.json.return_value = {
            "output": [
                {"invst_nm": "외국인", "seln_qty": "100", "shnu_qty": "350"},
                {"invst_nm": "기관", "seln_qty": "80", "shnu_qty": "110"},
            ]
        }
        client = KISClient("", "", "")
        client._token = "token"
        client._token_expires = 9999999999

        trend = client.get_investor_trend("005930")

        assert trend == {
            "foreign_net": 250,
            "institution_net": 30,
            "program_net": 0,
        }
