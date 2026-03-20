"""기술적 지표 테스트"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import TechnicalIndicators


@pytest.fixture
def sample_df():
    """삼성전자 유사 일봉 테스트 데이터 (50일치)"""
    np.random.seed(42)
    n = 50
    base = 70000
    prices = [base]
    for _ in range(n - 1):
        change = np.random.normal(0, 500)
        prices.append(max(prices[-1] + change, 50000))

    df = pd.DataFrame({
        "open": [p + np.random.randint(-300, 300) for p in prices],
        "high": [p + abs(np.random.normal(0, 400)) for p in prices],
        "low": [p - abs(np.random.normal(0, 400)) for p in prices],
        "close": prices,
        "volume": [np.random.randint(5_000_000, 30_000_000) for _ in range(n)],
    })
    return df


@pytest.fixture
def indicators():
    return TechnicalIndicators()


class TestRSI:
    def test_rsi_range(self, indicators, sample_df):
        df = indicators.add_rsi(sample_df)
        rsi_values = df["rsi"].dropna()
        assert all(0 <= v <= 100 for v in rsi_values)

    def test_rsi_column_exists(self, indicators, sample_df):
        df = indicators.add_rsi(sample_df)
        assert "rsi" in df.columns


class TestMACD:
    def test_macd_columns(self, indicators, sample_df):
        df = indicators.add_macd(sample_df)
        assert "macd" in df.columns
        assert "macd_signal" in df.columns
        assert "macd_hist" in df.columns

    def test_macd_hist_equals_diff(self, indicators, sample_df):
        df = indicators.add_macd(sample_df)
        diff = df["macd"] - df["macd_signal"]
        pd.testing.assert_series_equal(df["macd_hist"], diff, check_names=False)


class TestBollinger:
    def test_bollinger_columns(self, indicators, sample_df):
        df = indicators.add_bollinger(sample_df)
        assert "bb_mid" in df.columns
        assert "bb_upper" in df.columns
        assert "bb_lower" in df.columns
        assert "bb_pctb" in df.columns

    def test_upper_above_lower(self, indicators, sample_df):
        df = indicators.add_bollinger(sample_df)
        valid = df.dropna(subset=["bb_upper", "bb_lower"])
        assert all(valid["bb_upper"] >= valid["bb_lower"])


class TestMA:
    def test_ma_columns(self, indicators, sample_df):
        df = indicators.add_ma(sample_df)
        assert "ma_short" in df.columns
        assert "ma_mid" in df.columns
        assert "ma_long" in df.columns


class TestATR:
    def test_atr_positive(self, indicators, sample_df):
        df = indicators.add_atr(sample_df)
        atr_values = df["atr"].dropna()
        assert all(v >= 0 for v in atr_values)


class TestVolume:
    def test_volume_ratio(self, indicators, sample_df):
        df = indicators.add_volume_ma(sample_df)
        assert "vol_ma" in df.columns
        assert "vol_ratio" in df.columns


class TestAddAll:
    def test_all_columns(self, indicators, sample_df):
        df = indicators.add_all(sample_df)
        expected = [
            "rsi", "macd", "macd_signal", "macd_hist",
            "bb_mid", "bb_upper", "bb_lower", "bb_pctb",
            "ma_short", "ma_mid", "ma_long",
            "atr", "vol_ma", "vol_ratio",
        ]
        for col in expected:
            assert col in df.columns, f"Missing column: {col}"
