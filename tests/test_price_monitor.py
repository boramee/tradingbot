"""가격 모니터링 테스트"""

from unittest.mock import MagicMock

import pytest

from src.exchanges.base_exchange import Ticker
from src.monitor.fx_rate import FXRateProvider
from src.monitor.price_monitor import PriceMonitor, NormalizedPrice


def _mock_exchange(name, quote, is_korean, tickers_data):
    """테스트용 모의 거래소"""
    ex = MagicMock()
    ex.name = name
    ex.quote_currency = quote
    ex.is_korean = is_korean
    ex.fee_rate = 0.001

    def _fetch_tickers(symbols):
        result = {}
        for s in symbols:
            if s in tickers_data:
                d = tickers_data[s]
                result[s] = Ticker(name, s, quote, d["bid"], d["ask"], d["last"], d.get("vol", 100))
        return result

    ex.fetch_tickers = _fetch_tickers
    return ex


class TestPriceMonitor:
    def test_fetch_and_normalize(self):
        """가격 조회 및 USDT 정규화"""
        binance = _mock_exchange("binance", "USDT", False, {
            "BTC": {"bid": 100000, "ask": 100100, "last": 100050},
        })
        upbit = _mock_exchange("upbit", "KRW", True, {
            "BTC": {"bid": 136350000, "ask": 136500000, "last": 136400000},
        })

        fx = FXRateProvider()
        fx._cached_rate = 1350.0
        fx._cache_time = __import__("time").time()

        monitor = PriceMonitor(
            {"binance": binance, "upbit": upbit},
            fx, ["BTC"],
        )

        snapshots = monitor.fetch_all_prices()
        assert "BTC" in snapshots

        snap = snapshots["BTC"]
        assert "binance" in snap.prices
        assert "upbit" in snap.prices

        binance_price = snap.prices["binance"]
        assert abs(binance_price.bid_usdt - 100000) < 1

        upbit_price = snap.prices["upbit"]
        expected_usdt = 136350000 / 1350
        assert abs(upbit_price.bid_usdt - expected_usdt) < 100

    def test_missing_exchange_data(self):
        """데이터가 없는 거래소는 스킵"""
        binance = _mock_exchange("binance", "USDT", False, {
            "BTC": {"bid": 100000, "ask": 100100, "last": 100050},
        })
        empty = _mock_exchange("bybit", "USDT", False, {})

        fx = FXRateProvider()
        fx._cached_rate = 1350.0
        fx._cache_time = __import__("time").time()

        monitor = PriceMonitor({"binance": binance, "bybit": empty}, fx, ["BTC"])
        snapshots = monitor.fetch_all_prices()

        assert "BTC" in snapshots
        assert "binance" in snapshots["BTC"].prices
        assert "bybit" not in snapshots["BTC"].prices


class TestNormalizedPrice:
    def test_mid_usdt(self):
        p = NormalizedPrice("binance", "BTC", "USDT", 100000, 100100, 100050, 100000, 100100, 500)
        assert p.mid_usdt == 100050.0
