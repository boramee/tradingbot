"""백테스트 엔진 테스트"""

import numpy as np
import pandas as pd
import pytest

from config.settings import AppConfig
from src.backtest.engine import BacktestEngine, BacktestResult
from src.strategies.rsi_strategy import RSIStrategy
from src.strategies.combined_strategy import CombinedStrategy


@pytest.fixture
def config():
    return AppConfig()


@pytest.fixture
def engine(config):
    return BacktestEngine(config)


@pytest.fixture
def sample_df():
    np.random.seed(42)
    n = 200
    close = 50000 + np.cumsum(np.random.randn(n) * 500)
    return pd.DataFrame({
        "open": close * 0.999,
        "high": close * 1.003,
        "low": close * 0.997,
        "close": close,
        "volume": np.random.randint(100, 10000, n).astype(float),
        "value": np.random.randint(1000000, 100000000, n).astype(float),
    }, index=pd.date_range("2025-01-01", periods=n, freq="h"))


class TestBacktestEngine:
    def test_returns_result(self, engine, sample_df, config):
        strategy = RSIStrategy(config.indicator)
        result = engine.run(sample_df, strategy)
        assert isinstance(result, BacktestResult)

    def test_result_has_summary(self, engine, sample_df, config):
        strategy = CombinedStrategy(config.indicator)
        result = engine.run(sample_df, strategy)
        summary = result.summary()
        assert "백테스트 결과" in summary

    def test_win_rate_bounded(self, engine, sample_df, config):
        strategy = RSIStrategy(config.indicator)
        result = engine.run(sample_df, strategy)
        assert 0.0 <= result.win_rate <= 100.0

    def test_insufficient_data(self, engine, config):
        df = pd.DataFrame({
            "open": [50000],
            "high": [51000],
            "low": [49000],
            "close": [50500],
            "volume": [100.0],
            "value": [5000000.0],
        }, index=pd.date_range("2025-01-01", periods=1, freq="h"))
        strategy = RSIStrategy(config.indicator)
        result = engine.run(df, strategy)
        assert result.total_trades == 0
