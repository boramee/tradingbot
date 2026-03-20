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

    def _fetch_ticker(symbol):
        if symbol in tickers_data:
            d = tickers_data[symbol]
            return Ticker(name, symbol, quote, d["bid"], d["ask"], d["last"], d.get("vol", 100))
        return None

    ex.fetch_tickers = _fetch_tickers
    ex.fetch_ticker = _fetch_ticker
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

    def test_usdt_monitoring(self):
        """USDT 프리미엄 모니터링 - 한국은 KRW-USDT, 해외는 USDT/USDC 실가"""
        upbit = _mock_exchange("upbit", "KRW", True, {
            "USDT": {"bid": 1380, "ask": 1385, "last": 1382},
        })
        # 해외 거래소: USDT/USDC 실제 가격 (약 $0.9998)
        binance = _mock_exchange("binance", "USDT", False, {
            "USDT": {"bid": 0.9998, "ask": 1.0001, "last": 0.9999},
        })

        fx = FXRateProvider()
        fx._cached_rate = 1350.0
        fx._cache_time = __import__("time").time()

        monitor = PriceMonitor(
            {"upbit": upbit, "binance": binance},
            fx, ["USDT"],
        )

        snapshots = monitor.fetch_all_prices()
        assert "USDT" in snapshots

        usdt_snap = snapshots["USDT"]

        # 업비트: KRW-USDT 실제 가격
        assert "upbit" in usdt_snap.prices
        upbit_usdt = usdt_snap.prices["upbit"]
        assert upbit_usdt.bid_original == 1380
        assert upbit_usdt.bid_usdt > 1.0

        # 바이낸스: USDT/USDC 실제 시장가
        assert "binance" in usdt_snap.prices
        binance_usdt = usdt_snap.prices["binance"]
        assert abs(binance_usdt.bid_usdt - 0.9998) < 0.001
        assert abs(binance_usdt.ask_usdt - 1.0001) < 0.001

    def test_usdt_and_coins_together(self):
        """USDT와 코인을 동시에 모니터링"""
        upbit = _mock_exchange("upbit", "KRW", True, {
            "USDT": {"bid": 1380, "ask": 1385, "last": 1382},
            "BTC": {"bid": 136000000, "ask": 136100000, "last": 136050000},
        })
        binance = _mock_exchange("binance", "USDT", False, {
            "BTC": {"bid": 100000, "ask": 100100, "last": 100050},
        })

        fx = FXRateProvider()
        fx._cached_rate = 1350.0
        fx._cache_time = __import__("time").time()

        monitor = PriceMonitor(
            {"upbit": upbit, "binance": binance},
            fx, ["USDT", "BTC"],
        )

        snapshots = monitor.fetch_all_prices()
        assert "USDT" in snapshots
        assert "BTC" in snapshots
        assert len(snapshots) == 2


class TestNormalizedPrice:
    def test_mid_usdt(self):
        p = NormalizedPrice("binance", "BTC", "USDT", 100000, 100100, 100050, 100000, 100100, 500)
        assert p.mid_usdt == 100050.0
