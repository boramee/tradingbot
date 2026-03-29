"""공포/탐욕 지수 테스트"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.kr_stock.fear_greed_index import (
    FearGreedCalculator,
    FearGreedResult,
    MarketFearGreedIndex,
    Sentiment,
    _classify_sentiment,
    _clamp,
)
from src.kr_stock.watchlist import (
    DEFAULT_WATCHLIST,
    ETF_WATCHLIST,
    Stock,
    WatchlistConfig,
)


def _make_ohlcv(n: int = 300, trend: str = "neutral") -> pd.DataFrame:
    """테스트용 OHLCV 데이터 생성"""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base = 50000.0
    prices = [base]
    for i in range(1, n):
        if trend == "bull":
            drift = 0.002
        elif trend == "bear":
            drift = -0.002
        else:
            drift = 0.0
        change = np.random.normal(drift, 0.015)
        prices.append(prices[-1] * (1 + change))

    close = np.array(prices)
    high = close * (1 + np.abs(np.random.normal(0, 0.01, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.01, n)))
    open_ = close * (1 + np.random.normal(0, 0.005, n))
    volume = np.random.randint(100000, 1000000, n).astype(float)

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }, index=dates)


class TestClamp:
    def test_within_range(self):
        assert _clamp(50.0) == 50.0

    def test_below_min(self):
        assert _clamp(-10.0) == 0.0

    def test_above_max(self):
        assert _clamp(120.0) == 100.0


class TestClassifySentiment:
    def test_extreme_fear(self):
        assert _classify_sentiment(10) == Sentiment.EXTREME_FEAR

    def test_fear(self):
        assert _classify_sentiment(30) == Sentiment.FEAR

    def test_neutral(self):
        assert _classify_sentiment(50) == Sentiment.NEUTRAL

    def test_greed(self):
        assert _classify_sentiment(70) == Sentiment.GREED

    def test_extreme_greed(self):
        assert _classify_sentiment(90) == Sentiment.EXTREME_GREED

    def test_boundary_20(self):
        assert _classify_sentiment(20) == Sentiment.EXTREME_FEAR

    def test_boundary_40(self):
        assert _classify_sentiment(40) == Sentiment.FEAR

    def test_boundary_60(self):
        assert _classify_sentiment(60) == Sentiment.NEUTRAL

    def test_boundary_80(self):
        assert _classify_sentiment(80) == Sentiment.GREED


class TestFearGreedCalculator:
    def setup_method(self):
        self.calc = FearGreedCalculator()

    def test_calculate_neutral(self):
        df = _make_ohlcv(300, trend="neutral")
        result = self.calc.calculate(df)
        assert result is not None
        assert 0 <= result.score <= 100
        assert isinstance(result.sentiment, Sentiment)

    def test_calculate_bull(self):
        df = _make_ohlcv(300, trend="bull")
        result = self.calc.calculate(df)
        assert result is not None
        assert result.score > 40

    def test_calculate_bear(self):
        df = _make_ohlcv(300, trend="bear")
        result = self.calc.calculate(df)
        assert result is not None
        assert result.score < 60

    def test_insufficient_data(self):
        df = _make_ohlcv(50)
        result = self.calc.calculate(df)
        assert result is None

    def test_none_input(self):
        result = self.calc.calculate(None)
        assert result is None

    def test_all_scores_in_range(self):
        df = _make_ohlcv(300)
        result = self.calc.calculate(df)
        assert result is not None
        for attr in ["rsi_score", "ma_deviation_score", "volatility_score",
                      "volume_trend_score", "high_low_score", "bollinger_score"]:
            score = getattr(result, attr)
            assert 0 <= score <= 100, f"{attr} out of range: {score}"

    def test_price_change_pct(self):
        df = _make_ohlcv(300)
        result = self.calc.calculate(df)
        assert result is not None
        assert isinstance(result.price_change_pct, float)

    def test_rsi_score_range(self):
        df = _make_ohlcv(300)
        score = self.calc._rsi_score(df)
        assert 0 <= score <= 100

    def test_ma_deviation_score_range(self):
        df = _make_ohlcv(300)
        score = self.calc._ma_deviation_score(df)
        assert 0 <= score <= 100

    def test_volatility_score_range(self):
        df = _make_ohlcv(300)
        score = self.calc._volatility_score(df)
        assert 0 <= score <= 100

    def test_volume_trend_score_range(self):
        df = _make_ohlcv(300)
        score = self.calc._volume_trend_score(df)
        assert 0 <= score <= 100

    def test_high_low_score_range(self):
        df = _make_ohlcv(300)
        score = self.calc._high_low_score(df)
        assert 0 <= score <= 100

    def test_bollinger_score_range(self):
        df = _make_ohlcv(300)
        score = self.calc._bollinger_score(df)
        assert 0 <= score <= 100


class TestFearGreedResult:
    def test_action_signal_extreme_fear(self):
        r = FearGreedResult(
            score=15, sentiment=Sentiment.EXTREME_FEAR,
            rsi_score=20, ma_deviation_score=15, volatility_score=10,
            volume_trend_score=20, high_low_score=10, bollinger_score=15,
            current_price=50000, price_change_pct=-3.5,
        )
        assert "강력 매수" in r.action_signal
        assert r.emoji == "🟢🟢"

    def test_action_signal_extreme_greed(self):
        r = FearGreedResult(
            score=85, sentiment=Sentiment.EXTREME_GREED,
            rsi_score=80, ma_deviation_score=85, volatility_score=90,
            volume_trend_score=80, high_low_score=85, bollinger_score=90,
            current_price=80000, price_change_pct=2.1,
        )
        assert "강력 매도" in r.action_signal
        assert r.emoji == "🔴🔴"

    def test_action_signal_neutral(self):
        r = FearGreedResult(
            score=50, sentiment=Sentiment.NEUTRAL,
            rsi_score=50, ma_deviation_score=50, volatility_score=50,
            volume_trend_score=50, high_low_score=50, bollinger_score=50,
            current_price=60000, price_change_pct=0.1,
        )
        assert "관망" in r.action_signal
        assert r.emoji == "⚪"


class TestMarketFearGreedIndex:
    def test_composite_calculation(self):
        idx = MarketFearGreedIndex()
        results = {
            "005930": FearGreedResult(
                score=30, sentiment=Sentiment.FEAR,
                rsi_score=25, ma_deviation_score=30, volatility_score=35,
                volume_trend_score=25, high_low_score=30, bollinger_score=35,
                current_price=70000, price_change_pct=-1.0,
            ),
            "000660": FearGreedResult(
                score=70, sentiment=Sentiment.GREED,
                rsi_score=75, ma_deviation_score=70, volatility_score=65,
                volume_trend_score=75, high_low_score=70, bollinger_score=65,
                current_price=150000, price_change_pct=1.5,
            ),
        }
        composite = idx.calculate_composite(results)
        assert composite is not None
        assert composite.score == 50.0
        assert composite.sentiment == Sentiment.NEUTRAL

    def test_empty_results(self):
        idx = MarketFearGreedIndex()
        assert idx.calculate_composite({}) is None


class TestWatchlist:
    def test_default_watchlist(self):
        assert len(DEFAULT_WATCHLIST) > 0
        for s in DEFAULT_WATCHLIST:
            assert s.code
            assert s.name

    def test_etf_watchlist(self):
        assert len(ETF_WATCHLIST) > 0
        for s in ETF_WATCHLIST:
            assert s.category == "ETF"

    def test_watchlist_config(self):
        wl = WatchlistConfig()
        assert len(wl.all_items) == len(DEFAULT_WATCHLIST) + len(ETF_WATCHLIST)

    def test_add_stock(self):
        wl = WatchlistConfig()
        original = len(wl.stocks)
        wl.add_stock("999999", "테스트주", "테스트")
        assert len(wl.stocks) == original + 1

    def test_add_etf(self):
        wl = WatchlistConfig()
        original = len(wl.etfs)
        wl.add_etf("999998", "테스트ETF")
        assert len(wl.etfs) == original + 1

    def test_stock_frozen(self):
        s = Stock("005930", "삼성전자", "반도체")
        with pytest.raises(AttributeError):
            s.code = "000000"


class TestAlertBot:
    def test_import(self):
        from src.kr_stock.alert_bot import KRStockAlertBot, AlertThresholds
        assert AlertThresholds.EXTREME_FEAR == 20
        assert AlertThresholds.EXTREME_GREED == 80

    def test_bot_creation(self):
        from src.kr_stock.alert_bot import KRStockAlertBot
        wl = WatchlistConfig()
        bot = KRStockAlertBot(watchlist=wl)
        assert bot.poll_interval_min == 60
        assert bot.telegram is None

    def test_score_bar(self):
        from src.kr_stock.alert_bot import KRStockAlertBot
        bar = KRStockAlertBot._score_bar(50.0)
        assert "█" in bar
        assert "░" in bar
