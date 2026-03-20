"""기술적 분석 지표 계산 모듈"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

from config.settings import IndicatorConfig

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """다양한 기술적 분석 지표를 계산하는 클래스"""

    def __init__(self, config: IndicatorConfig):
        self.config = config

    def add_all_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """모든 기술적 지표를 DataFrame에 추가"""
        df = df.copy()
        df = self.add_rsi(df)
        df = self.add_macd(df)
        df = self.add_bollinger_bands(df)
        df = self.add_moving_averages(df)
        df = self.add_volume_indicators(df)
        df = self.add_atr(df)
        return df

    def add_rsi(self, df: pd.DataFrame, period: Optional[int] = None) -> pd.DataFrame:
        """RSI (Relative Strength Index) 계산"""
        period = period or self.config.rsi_period
        delta = df["close"].diff()

        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()

        rs = avg_gain / avg_loss.replace(0, np.finfo(float).eps)
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    def add_macd(
        self,
        df: pd.DataFrame,
        fast: Optional[int] = None,
        slow: Optional[int] = None,
        signal: Optional[int] = None,
    ) -> pd.DataFrame:
        """MACD (Moving Average Convergence Divergence) 계산"""
        fast = fast or self.config.macd_fast
        slow = slow or self.config.macd_slow
        signal = signal or self.config.macd_signal

        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=signal, adjust=False).mean()
        df["macd_histogram"] = df["macd"] - df["macd_signal"]
        return df

    def add_bollinger_bands(
        self,
        df: pd.DataFrame,
        period: Optional[int] = None,
        std_dev: Optional[float] = None,
    ) -> pd.DataFrame:
        """볼린저 밴드 계산"""
        period = period or self.config.bb_period
        std_dev = std_dev or self.config.bb_std

        df["bb_middle"] = df["close"].rolling(window=period).mean()
        rolling_std = df["close"].rolling(window=period).std()
        df["bb_upper"] = df["bb_middle"] + (rolling_std * std_dev)
        df["bb_lower"] = df["bb_middle"] - (rolling_std * std_dev)

        band_width = df["bb_upper"] - df["bb_lower"]
        df["bb_pctb"] = (df["close"] - df["bb_lower"]) / band_width.replace(0, np.nan)
        return df

    def add_moving_averages(self, df: pd.DataFrame) -> pd.DataFrame:
        """이동평균선 계산"""
        df["ma_short"] = df["close"].rolling(window=self.config.ma_short).mean()
        df["ma_long"] = df["close"].rolling(window=self.config.ma_long).mean()
        df["ma_cross"] = df["ma_short"] - df["ma_long"]
        return df

    def add_volume_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """거래량 관련 지표"""
        df["volume_ma"] = df["volume"].rolling(window=20).mean()
        df["volume_ratio"] = df["volume"] / df["volume_ma"].replace(0, np.nan)
        return df

    def add_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """ATR (Average True Range) - 변동성 지표"""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=period).mean()
        return df
