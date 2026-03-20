"""재정거래 탐지 엔진 테스트"""

import pytest

from config.settings import ArbitrageConfig
from src.monitor.price_monitor import NormalizedPrice, PriceSnapshot
from src.arbitrage.detector import ArbitrageDetector, ArbitrageType


@pytest.fixture
def config():
    return ArbitrageConfig(min_profit_pct=0.5, max_slippage_pct=0.3)


@pytest.fixture
def detector(config):
    fee_rates = {"upbit": 0.0005, "binance": 0.001, "bybit": 0.001, "bitfinex": 0.002, "bithumb": 0.0025}
    return ArbitrageDetector(config, fee_rates)


def _price(exchange, symbol, quote, bid, ask, price_in_peg, price_in_krw, vol=1000):
    return NormalizedPrice(
        exchange=exchange, symbol=symbol, original_quote=quote,
        price_in_peg=price_in_peg, price_in_krw=price_in_krw,
        bid_original=bid, ask_original=ask, last_original=(bid+ask)/2,
        volume_24h=vol, peg_currency="USD",
    )


class TestArbitrageDetector:
    def test_usdt_kimchi_premium(self, detector):
        """USDT 김치프리미엄 탐지"""
        snapshot = PriceSnapshot(
            symbol="USDT", peg_currency="USD", peg_rate_krw=1350,
            prices={
                "upbit": _price("upbit", "USDT", "KRW", 1380, 1385, 1.022, 1382),
                "binance": _price("binance", "USDT", "USDC", 0.9998, 1.0001, 0.9999, 1350),
            },
        )

        opps = detector.detect_all({"USDT": snapshot})
        assert len(opps) > 0
        kimchi = [o for o in opps if o.arb_type == ArbitrageType.KIMCHI_PREMIUM]
        assert len(kimchi) > 0

    def test_cross_exchange(self, detector):
        """해외 거래소 간 가격차"""
        snapshot = PriceSnapshot(
            symbol="USDT", peg_currency="USD", peg_rate_krw=1350,
            prices={
                "binance": _price("binance", "USDT", "USDC", 0.9998, 1.0001, 0.9999, 1350),
                "bitfinex": _price("bitfinex", "USDT", "USD", 1.0005, 1.0008, 1.0006, 1351),
            },
        )

        opps = detector.detect_all({"USDT": snapshot})
        cross = [o for o in opps if o.arb_type == ArbitrageType.CROSS_EXCHANGE]
        assert len(cross) > 0

    def test_no_opportunity_same_price(self, detector):
        snapshot = PriceSnapshot(
            symbol="USDT", peg_currency="USD", peg_rate_krw=1350,
            prices={
                "binance": _price("binance", "USDT", "USDC", 1.0, 1.0, 1.0, 1350),
                "bybit": _price("bybit", "USDT", "USDC", 1.0, 1.0, 1.0, 1350),
            },
        )
        profitable = detector.detect_profitable({"USDT": snapshot})
        assert len(profitable) == 0

    def test_single_exchange_no_detection(self, detector):
        snapshot = PriceSnapshot(
            symbol="USDT", peg_currency="USD", peg_rate_krw=1350,
            prices={
                "binance": _price("binance", "USDT", "USDC", 1.0, 1.0, 1.0, 1350),
            },
        )
        assert len(detector.detect_all({"USDT": snapshot})) == 0

    def test_multiple_tokens(self, detector):
        snapshots = {
            "USDT": PriceSnapshot(
                symbol="USDT", peg_currency="USD", peg_rate_krw=1350,
                prices={
                    "upbit": _price("upbit", "USDT", "KRW", 1380, 1385, 1.022, 1382),
                    "binance": _price("binance", "USDT", "USDC", 0.999, 1.000, 0.999, 1349),
                },
            ),
            "EURT": PriceSnapshot(
                symbol="EURT", peg_currency="EUR", peg_rate_krw=1470,
                prices={
                    "bitfinex": _price("bitfinex", "EURT", "USD", 1.08, 1.09, 1.0, 1480),
                    "binance": _price("binance", "EURT", "USDT", 1.07, 1.08, 0.99, 1460),
                },
            ),
        }
        opps = detector.detect_all(snapshots)
        symbols = {o.symbol for o in opps}
        assert "USDT" in symbols or "EURT" in symbols

    def test_calculate_premium(self, detector):
        snapshot = PriceSnapshot(
            symbol="USDT", peg_currency="USD", peg_rate_krw=1350,
            prices={
                "upbit": _price("upbit", "USDT", "KRW", 1380, 1385, 1.022, 1382),
                "binance": _price("binance", "USDT", "USDC", 0.999, 1.000, 0.999, 1349),
            },
        )
        premium = detector.calculate_premium(snapshot, "upbit", "binance")
        assert premium is not None
        assert premium > 0


class TestArbitrageOpportunity:
    def test_is_profitable(self):
        from src.arbitrage.detector import ArbitrageOpportunity
        opp = ArbitrageOpportunity(
            arb_type=ArbitrageType.KIMCHI_PREMIUM, symbol="USDT",
            buy_exchange="binance", sell_exchange="upbit",
            buy_price_usdt=1.0, sell_price_usdt=1.022,
            buy_price_original=1.0, sell_price_original=1380,
            buy_quote="USDC", sell_quote="KRW",
            spread_pct=2.2, net_profit_pct=2.05,
        )
        assert opp.is_profitable is True

    def test_summary(self):
        from src.arbitrage.detector import ArbitrageOpportunity
        opp = ArbitrageOpportunity(
            arb_type=ArbitrageType.KIMCHI_PREMIUM, symbol="USDT",
            buy_exchange="binance", sell_exchange="upbit",
            buy_price_usdt=1.0, sell_price_usdt=1.022,
            buy_price_original=1.0, sell_price_original=1380,
            buy_quote="USDC", sell_quote="KRW",
            spread_pct=2.2, net_profit_pct=2.05,
        )
        s = opp.summary()
        assert "USDT" in s
        assert "kimchi_premium" in s
