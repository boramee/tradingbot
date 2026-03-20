"""가격 모니터링 테스트"""

from unittest.mock import MagicMock

import pytest

from src.exchanges.base_exchange import Ticker
from src.monitor.fx_rate import FXRateProvider
from src.monitor.price_monitor import PriceMonitor, NormalizedPrice


def _mock_exchange(name, quote, is_korean, tickers_data):
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
                result[s] = Ticker(name, s, d.get("quote", quote),
                                   d["bid"], d["ask"], d["last"], d.get("vol", 100))
        return result

    def _fetch_ticker(symbol):
        if symbol in tickers_data:
            d = tickers_data[symbol]
            return Ticker(name, symbol, d.get("quote", quote),
                          d["bid"], d["ask"], d["last"], d.get("vol", 100))
        return None

    ex.fetch_tickers = _fetch_tickers
    ex.fetch_ticker = _fetch_ticker
    return ex


def _make_fx():
    fx = FXRateProvider()
    fx._cache = {"USD": 1350.0, "EUR": 1470.0, "CNH": 186.0, "XAU": 4050000.0}
    fx._cache_time = __import__("time").time()
    return fx


class TestPriceMonitor:
    def test_usdt_korean_vs_foreign(self):
        """USDT: 한국(KRW) vs 해외(USDC) 가격 비교"""
        upbit = _mock_exchange("upbit", "KRW", True, {
            "USDT": {"bid": 1380, "ask": 1385, "last": 1382, "quote": "KRW"},
        })
        binance = _mock_exchange("binance", "USDT", False, {
            "USDT": {"bid": 0.9998, "ask": 1.0001, "last": 0.9999, "quote": "USDC"},
        })

        monitor = PriceMonitor({"upbit": upbit, "binance": binance}, _make_fx(), ["USDT"])
        snapshots = monitor.fetch_all_prices()

        assert "USDT" in snapshots
        snap = snapshots["USDT"]
        assert snap.peg_currency == "USD"
        assert "upbit" in snap.prices
        assert "binance" in snap.prices

        upbit_p = snap.prices["upbit"]
        assert upbit_p.price_in_krw > 0
        assert upbit_p.price_in_peg > 1.0  # 프리미엄 있음

        binance_p = snap.prices["binance"]
        assert abs(binance_p.price_in_peg - 0.9999) < 0.01

    def test_eurt_pricing(self):
        """EURT: EUR 페그 기준 가격"""
        bitfinex = _mock_exchange("bitfinex", "USD", False, {
            "EURT": {"bid": 1.08, "ask": 1.09, "last": 1.085, "quote": "USD"},
        })

        monitor = PriceMonitor({"bitfinex": bitfinex}, _make_fx(), ["EURT"])
        snapshots = monitor.fetch_all_prices()

        assert "EURT" in snapshots
        snap = snapshots["EURT"]
        assert snap.peg_currency == "EUR"
        assert "bitfinex" in snap.prices

        bf_p = snap.prices["bitfinex"]
        assert bf_p.price_in_krw > 0
        assert bf_p.price_in_peg > 0

    def test_xaut_pricing(self):
        """XAUT: 금(XAU) 페그 기준 가격"""
        bitfinex = _mock_exchange("bitfinex", "USD", False, {
            "XAUT": {"bid": 3000, "ask": 3010, "last": 3005, "quote": "USD"},
        })

        monitor = PriceMonitor({"bitfinex": bitfinex}, _make_fx(), ["XAUT"])
        snapshots = monitor.fetch_all_prices()

        assert "XAUT" in snapshots
        snap = snapshots["XAUT"]
        assert snap.peg_currency == "XAU"
        assert "bitfinex" in snap.prices

    def test_multiple_tether_tokens(self):
        """여러 테더 토큰 동시 모니터링"""
        upbit = _mock_exchange("upbit", "KRW", True, {
            "USDT": {"bid": 1380, "ask": 1385, "last": 1382, "quote": "KRW"},
        })
        bitfinex = _mock_exchange("bitfinex", "USD", False, {
            "USDT": {"bid": 1.0001, "ask": 1.0002, "last": 1.0001, "quote": "USD"},
            "EURT": {"bid": 1.08, "ask": 1.09, "last": 1.085, "quote": "USD"},
            "XAUT": {"bid": 3000, "ask": 3010, "last": 3005, "quote": "USD"},
        })

        monitor = PriceMonitor(
            {"upbit": upbit, "bitfinex": bitfinex},
            _make_fx(), ["USDT", "EURT", "XAUT"],
        )
        snapshots = monitor.fetch_all_prices()

        assert "USDT" in snapshots
        assert "EURT" in snapshots
        assert "XAUT" in snapshots

    def test_missing_exchange_skipped(self):
        """데이터 없는 거래소는 스킵"""
        empty = _mock_exchange("binance", "USDT", False, {})
        monitor = PriceMonitor({"binance": empty}, _make_fx(), ["USDT"])
        snapshots = monitor.fetch_all_prices()
        assert snapshots["USDT"].exchange_count == 0


class TestNormalizedPrice:
    def test_mid_original(self):
        p = NormalizedPrice("binance", "USDT", "USDC", 0.9998, 1350, 0.9998, 1.0001, 0.9999, 500)
        assert abs(p.mid_original - 0.9999) < 0.001
