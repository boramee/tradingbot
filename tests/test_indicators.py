"""기술적 지표 테스트"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.technical import TechnicalIndicators


@pytest.fixture
def ti():
    return TechnicalIndicators()


@pytest.fixture
def sample():
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


class TestIndicators:
    def test_rsi_range(self, ti, sample):
        df = ti.add_rsi(sample)
        valid = df["rsi"].dropna()
        assert all(0 <= v <= 100 for v in valid)

    def test_macd_columns(self, ti, sample):
        df = ti.add_macd(sample)
        for col in ["macd", "macd_signal", "macd_hist"]:
            assert col in df.columns

    def test_bollinger_order(self, ti, sample):
        df = ti.add_bollinger(sample)
        valid = df.dropna(subset=["bb_upper", "bb_lower"])
        assert all(valid["bb_upper"] >= valid["bb_lower"])

    def test_all_columns(self, ti, sample):
        df = ti.add_all(sample)
        expected = ["rsi", "macd", "macd_signal", "macd_hist",
                    "bb_upper", "bb_mid", "bb_lower", "bb_pctb",
                    "ma_short", "ma_long", "atr", "vol_ma", "vol_ratio"]
        for col in expected:
            assert col in df.columns

    def test_immutable(self, ti, sample):
        orig_cols = set(sample.columns)
        ti.add_all(sample)
        assert set(sample.columns) == orig_cols
