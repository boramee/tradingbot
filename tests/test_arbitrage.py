"""재정거래 탐지 엔진 테스트"""

import time

import pytest

from config.settings import ArbitrageConfig
from src.monitor.price_monitor import NormalizedPrice, PriceSnapshot
from src.arbitrage.detector import ArbitrageDetector, ArbitrageType


@pytest.fixture
def config():
    return ArbitrageConfig(
        min_profit_pct=0.5,
        max_slippage_pct=0.3,
    )


@pytest.fixture
def detector(config):
    fee_rates = {"upbit": 0.0005, "binance": 0.001, "bybit": 0.001, "bithumb": 0.0025}
    return ArbitrageDetector(config, fee_rates)


def _make_normalized(exchange, symbol, quote, bid_usdt, ask_usdt, bid_orig=0, ask_orig=0, volume=1000):
    return NormalizedPrice(
        exchange=exchange,
        symbol=symbol,
        original_quote=quote,
        bid_usdt=bid_usdt,
        ask_usdt=ask_usdt,
        last_usdt=(bid_usdt + ask_usdt) / 2,
        bid_original=bid_orig or bid_usdt,
        ask_original=ask_orig or ask_usdt,
        volume_24h=volume,
    )


class TestArbitrageDetector:
    def test_detect_cross_exchange_opportunity(self, detector):
        """바이낸스-바이비트 간 가격차 탐지"""
        snapshot = PriceSnapshot(
            symbol="BTC",
            prices={
                "binance": _make_normalized("binance", "BTC", "USDT", 99800, 99900),
                "bybit": _make_normalized("bybit", "BTC", "USDT", 100500, 100600),
            },
            fx_rate=1350,
        )

        opps = detector.detect_all({"BTC": snapshot})
        assert len(opps) > 0

        profitable = [o for o in opps if o.net_profit_pct > 0]
        if profitable:
            best = profitable[0]
            assert best.buy_exchange == "binance"
            assert best.sell_exchange == "bybit"
            assert best.arb_type == ArbitrageType.CROSS_EXCHANGE

    def test_detect_kimchi_premium(self, detector):
        """업비트(KRW) vs 바이낸스(USDT) 김치프리미엄 탐지"""
        snapshot = PriceSnapshot(
            symbol="BTC",
            prices={
                "upbit": _make_normalized(
                    "upbit", "BTC", "KRW",
                    bid_usdt=101000, ask_usdt=101100,
                    bid_orig=136350000, ask_orig=136485000,
                ),
                "binance": _make_normalized("binance", "BTC", "USDT", 99800, 99900),
            },
            fx_rate=1350,
        )

        opps = detector.detect_all({"BTC": snapshot})
        kimchi = [o for o in opps if o.arb_type == ArbitrageType.KIMCHI_PREMIUM]
        assert len(kimchi) > 0

    def test_no_opportunity_same_price(self, detector):
        """가격이 같으면 수익 기회 없음"""
        snapshot = PriceSnapshot(
            symbol="ETH",
            prices={
                "binance": _make_normalized("binance", "ETH", "USDT", 3000, 3001),
                "bybit": _make_normalized("bybit", "ETH", "USDT", 3000, 3001),
            },
            fx_rate=1350,
        )

        profitable = detector.detect_profitable({"ETH": snapshot})
        assert len(profitable) == 0

    def test_single_exchange_no_detection(self, detector):
        """거래소 1개면 비교 불가"""
        snapshot = PriceSnapshot(
            symbol="BTC",
            prices={
                "binance": _make_normalized("binance", "BTC", "USDT", 100000, 100100),
            },
        )
        opps = detector.detect_all({"BTC": snapshot})
        assert len(opps) == 0

    def test_calculate_kimchi_premium(self, detector):
        """김치프리미엄 계산"""
        snapshot = PriceSnapshot(
            symbol="BTC",
            prices={
                "upbit": _make_normalized("upbit", "BTC", "KRW", bid_usdt=102000, ask_usdt=102100),
                "binance": _make_normalized("binance", "BTC", "USDT", 100000, 100100),
            },
        )

        premium = detector.calculate_kimchi_premium(snapshot)
        assert premium is not None
        assert premium > 0  # 업비트가 더 비쌈

    def test_negative_kimchi_premium(self, detector):
        """역 김치프리미엄 (해외가 더 높음)"""
        snapshot = PriceSnapshot(
            symbol="BTC",
            prices={
                "upbit": _make_normalized("upbit", "BTC", "KRW", bid_usdt=98000, ask_usdt=98100),
                "binance": _make_normalized("binance", "BTC", "USDT", 100000, 100100),
            },
        )

        premium = detector.calculate_kimchi_premium(snapshot)
        assert premium is not None
        assert premium < 0

    def test_multiple_symbols(self, detector):
        """여러 코인 동시 탐지"""
        snapshots = {
            "BTC": PriceSnapshot(
                symbol="BTC",
                prices={
                    "binance": _make_normalized("binance", "BTC", "USDT", 99000, 99100),
                    "bybit": _make_normalized("bybit", "BTC", "USDT", 100500, 100600),
                },
            ),
            "ETH": PriceSnapshot(
                symbol="ETH",
                prices={
                    "binance": _make_normalized("binance", "ETH", "USDT", 2990, 2995),
                    "bybit": _make_normalized("bybit", "ETH", "USDT", 3050, 3055),
                },
            ),
        }

        opps = detector.detect_all(snapshots)
        symbols = {o.symbol for o in opps}
        assert "BTC" in symbols
        assert "ETH" in symbols


class TestArbitrageOpportunity:
    def test_is_profitable(self):
        from src.arbitrage.detector import ArbitrageOpportunity
        opp = ArbitrageOpportunity(
            arb_type=ArbitrageType.CROSS_EXCHANGE,
            symbol="BTC",
            buy_exchange="binance", sell_exchange="bybit",
            buy_price_usdt=100000, sell_price_usdt=100800,
            buy_price_original=100000, sell_price_original=100800,
            buy_quote="USDT", sell_quote="USDT",
            spread_pct=0.8, net_profit_pct=0.6,
        )
        assert opp.is_profitable is True

    def test_not_profitable(self):
        from src.arbitrage.detector import ArbitrageOpportunity
        opp = ArbitrageOpportunity(
            arb_type=ArbitrageType.CROSS_EXCHANGE,
            symbol="ETH",
            buy_exchange="binance", sell_exchange="bybit",
            buy_price_usdt=3000, sell_price_usdt=3001,
            buy_price_original=3000, sell_price_original=3001,
            buy_quote="USDT", sell_quote="USDT",
            spread_pct=0.03, net_profit_pct=-0.17,
        )
        assert opp.is_profitable is False

    def test_summary(self):
        from src.arbitrage.detector import ArbitrageOpportunity
        opp = ArbitrageOpportunity(
            arb_type=ArbitrageType.KIMCHI_PREMIUM,
            symbol="BTC",
            buy_exchange="binance", sell_exchange="upbit",
            buy_price_usdt=100000, sell_price_usdt=102000,
            buy_price_original=100000, sell_price_original=137700000,
            buy_quote="USDT", sell_quote="KRW",
            spread_pct=2.0, net_profit_pct=1.85,
        )
        summary = opp.summary()
        assert "BTC" in summary
        assert "kimchi_premium" in summary
