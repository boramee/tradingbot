"""데이터 모델 테스트"""

import pytest

from src.exchanges.base_exchange import Ticker, OrderBook, OrderResult


class TestTicker:
    def test_mid_price(self):
        t = Ticker("binance", "BTC", "USDT", bid=100000, ask=100100, last=100050, volume_24h=500)
        assert t.mid == 100050.0

    def test_spread_pct(self):
        t = Ticker("binance", "BTC", "USDT", bid=100000, ask=100100, last=100050, volume_24h=500)
        assert abs(t.spread_pct - 0.1) < 0.01

    def test_pair(self):
        t = Ticker("upbit", "ETH", "KRW", bid=5000000, ask=5001000, last=5000500, volume_24h=100)
        assert t.pair == "ETH/KRW"

    def test_zero_bid_spread(self):
        t = Ticker("test", "BTC", "USDT", bid=0, ask=100, last=50, volume_24h=0)
        assert t.spread_pct == 0.0


class TestOrderBook:
    def test_best_bid_ask(self):
        ob = OrderBook(
            exchange="binance",
            symbol="BTC",
            bids=[[99900, 1.5], [99800, 2.0]],
            asks=[[100100, 0.5], [100200, 1.0]],
        )
        assert ob.best_bid == 99900
        assert ob.best_ask == 100100
        assert ob.best_bid_volume == 1.5
        assert ob.best_ask_volume == 0.5

    def test_empty_orderbook(self):
        ob = OrderBook(exchange="test", symbol="BTC")
        assert ob.best_bid == 0.0
        assert ob.best_ask == 0.0


class TestOrderResult:
    def test_success(self):
        r = OrderResult("binance", "BTC", "buy", 100000, 0.01, fee=0.001, order_id="abc123")
        assert r.success is True

    def test_failure(self):
        r = OrderResult("binance", "BTC", "buy", 0, 0, success=False, error="잔고 부족")
        assert r.success is False
        assert r.error == "잔고 부족"
