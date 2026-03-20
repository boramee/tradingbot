"""기술적 분석 지표 계산

삼성전자 등 국내 주식 일봉 데이터에 기술적 지표를 추가.
입력 DataFrame 컬럼: open, high, low, close, volume
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class TechnicalIndicators:
    """DataFrame에 기술적 지표 컬럼을 추가"""

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        bb_period: int = 20,
        bb_std: float = 2.0,
        ma_short: int = 5,
        ma_mid: int = 20,
        ma_long: int = 60,
        atr_period: int = 14,
    ):
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.ma_short = ma_short
        self.ma_mid = ma_mid
        self.ma_long = ma_long
        self.atr_period = atr_period

    def add_all(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df = self.add_rsi(df)
        df = self.add_macd(df)
        df = self.add_bollinger(df)
        df = self.add_ma(df)
        df = self.add_atr(df)
        df = self.add_volume_ma(df)
        return df

    def add_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.finfo(float).eps)
        df["rsi"] = 100 - (100 / (1 + rs))
        return df

    def add_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        ema_f = df["close"].ewm(span=self.macd_fast, adjust=False).mean()
        ema_s = df["close"].ewm(span=self.macd_slow, adjust=False).mean()
        df["macd"] = ema_f - ema_s
        df["macd_signal"] = df["macd"].ewm(span=self.macd_signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]
        return df

    def add_bollinger(self, df: pd.DataFrame) -> pd.DataFrame:
        df["bb_mid"] = df["close"].rolling(self.bb_period).mean()
        std = df["close"].rolling(self.bb_period).std()
        df["bb_upper"] = df["bb_mid"] + std * self.bb_std
        df["bb_lower"] = df["bb_mid"] - std * self.bb_std
        bw = df["bb_upper"] - df["bb_lower"]
        df["bb_pctb"] = (df["close"] - df["bb_lower"]) / bw.replace(0, np.nan)
        return df

    def add_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        df["ma_short"] = df["close"].rolling(self.ma_short).mean()
        df["ma_mid"] = df["close"].rolling(self.ma_mid).mean()
        df["ma_long"] = df["close"].rolling(self.ma_long).mean()
        return df

    def add_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        df["atr"] = tr.rolling(self.atr_period).mean()
        return df

    def add_volume_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        df["vol_ma"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, np.nan)
        return df
