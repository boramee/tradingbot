"""기술적 지표 테스트"""

import numpy as np
import pandas as pd
import pytest

from config.settings import IndicatorConfig
from src.indicators.technical import TechnicalIndicators


@pytest.fixture
def config():
    return IndicatorConfig()


@pytest.fixture
def indicators(config):
    return TechnicalIndicators(config)


@pytest.fixture
def sample_df():
    """테스트용 OHLCV 데이터 생성"""
    np.random.seed(42)
    n = 100
    close = 50000 + np.cumsum(np.random.randn(n) * 500)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 100,
        "high": close + abs(np.random.randn(n) * 300),
        "low": close - abs(np.random.randn(n) * 300),
        "close": close,
        "volume": np.random.randint(100, 10000, n).astype(float),
    }, index=pd.date_range("2025-01-01", periods=n, freq="h"))


class TestRSI:
    def test_rsi_range(self, indicators, sample_df):
        df = indicators.add_rsi(sample_df)
        valid_rsi = df["rsi"].dropna()
        assert all(0 <= v <= 100 for v in valid_rsi)

    def test_rsi_column_exists(self, indicators, sample_df):
        df = indicators.add_rsi(sample_df)
        assert "rsi" in df.columns


class TestMACD:
    def test_macd_columns(self, indicators, sample_df):
        df = indicators.add_macd(sample_df)
        assert "macd" in df.columns
        assert "macd_signal" in df.columns
        assert "macd_histogram" in df.columns

    def test_histogram_equals_diff(self, indicators, sample_df):
        df = indicators.add_macd(sample_df)
        valid = df.dropna(subset=["macd", "macd_signal", "macd_histogram"])
        diff = valid["macd"] - valid["macd_signal"]
        np.testing.assert_array_almost_equal(valid["macd_histogram"].values, diff.values)


class TestBollingerBands:
    def test_bb_columns(self, indicators, sample_df):
        df = indicators.add_bollinger_bands(sample_df)
        for col in ["bb_upper", "bb_middle", "bb_lower", "bb_pctb"]:
            assert col in df.columns

    def test_upper_above_lower(self, indicators, sample_df):
        df = indicators.add_bollinger_bands(sample_df)
        valid = df.dropna(subset=["bb_upper", "bb_lower"])
        assert all(valid["bb_upper"] >= valid["bb_lower"])


class TestMovingAverages:
    def test_ma_columns(self, indicators, sample_df):
        df = indicators.add_moving_averages(sample_df)
        assert "ma_short" in df.columns
        assert "ma_long" in df.columns
        assert "ma_cross" in df.columns


class TestAllIndicators:
    def test_all_indicators_added(self, indicators, sample_df):
        df = indicators.add_all_indicators(sample_df)
        expected = [
            "rsi", "macd", "macd_signal", "macd_histogram",
            "bb_upper", "bb_middle", "bb_lower", "bb_pctb",
            "ma_short", "ma_long", "ma_cross",
            "volume_ma", "volume_ratio", "atr",
        ]
        for col in expected:
            assert col in df.columns, f"Missing column: {col}"

    def test_original_not_modified(self, indicators, sample_df):
        original_cols = set(sample_df.columns)
        indicators.add_all_indicators(sample_df)
        assert set(sample_df.columns) == original_cols
