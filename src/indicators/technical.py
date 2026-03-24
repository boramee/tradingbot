"""기술적 분석 지표 계산"""

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
        ma_long: int = 20,
        atr_period: int = 14,
    ):
        self.rsi_period = rsi_period
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.atr_period = atr_period

    def add_all(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # TR은 ATR/ADX 모두 사용 → 한 번만 계산
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        self._add_rsi(df)
        self._add_macd(df)
        self._add_bollinger(df)
        self._add_ma(df)
        self._add_atr(df, tr)
        self._add_adx(df, tr)
        self._add_volume_ma(df)
        return df

    # ── 개별 지표 (inplace 변경, 반환값 없음) ──

    def _add_rsi(self, df: pd.DataFrame) -> None:
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.finfo(float).eps)
        df["rsi"] = 100 - (100 / (1 + rs))

    def _add_macd(self, df: pd.DataFrame) -> None:
        ema_f = df["close"].ewm(span=self.macd_fast, adjust=False).mean()
        ema_s = df["close"].ewm(span=self.macd_slow, adjust=False).mean()
        df["macd"] = ema_f - ema_s
        df["macd_signal"] = df["macd"].ewm(span=self.macd_signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

    def _add_bollinger(self, df: pd.DataFrame) -> None:
        df["bb_mid"] = df["close"].rolling(self.bb_period).mean()
        std = df["close"].rolling(self.bb_period).std()
        df["bb_upper"] = df["bb_mid"] + std * self.bb_std
        df["bb_lower"] = df["bb_mid"] - std * self.bb_std
        bw = df["bb_upper"] - df["bb_lower"]
        df["bb_pctb"] = (df["close"] - df["bb_lower"]) / bw.replace(0, np.nan)

    def _add_ma(self, df: pd.DataFrame) -> None:
        df["ma_short"] = df["close"].rolling(self.ma_short).mean()
        df["ma_long"] = df["close"].rolling(self.ma_long).mean()

    def _add_atr(self, df: pd.DataFrame, tr: pd.Series) -> None:
        df["atr"] = tr.rolling(self.atr_period).mean()

    def _add_adx(self, df: pd.DataFrame, tr: pd.Series, period: int = 14) -> None:
        """ADX (Average Directional Index) - 추세 강도 지표
        ADX >= 25: 추세장 (매매 적합)
        ADX < 20: 횡보장 (매매 위험)
        """
        h, l = df["high"], df["low"]

        plus_dm = h.diff()
        minus_dm = -l.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

        atr_sm = tr.ewm(alpha=1 / period, min_periods=period).mean()
        eps = np.finfo(float).eps

        plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() /
                         atr_sm.replace(0, eps))
        minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() /
                          atr_sm.replace(0, eps))

        dx = 100 * ((plus_di - minus_di).abs() /
                     (plus_di + minus_di).replace(0, eps))
        df["adx"] = dx.ewm(alpha=1 / period, min_periods=period).mean()
        df["plus_di"] = plus_di
        df["minus_di"] = minus_di

    def _add_volume_ma(self, df: pd.DataFrame) -> None:
        df["vol_ma"] = df["volume"].rolling(20).mean()
        df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0, np.nan)

    # ── 하위 호환: 개별 호출용 공개 메서드 ──

    def add_rsi(self, df: pd.DataFrame) -> pd.DataFrame:
        self._add_rsi(df)
        return df

    def add_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        self._add_macd(df)
        return df

    def add_bollinger(self, df: pd.DataFrame) -> pd.DataFrame:
        self._add_bollinger(df)
        return df

    def add_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        self._add_ma(df)
        return df

    def add_atr(self, df: pd.DataFrame) -> pd.DataFrame:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        self._add_atr(df, tr)
        return df

    def add_adx(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        h, l, c = df["high"], df["low"], df["close"]
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        self._add_adx(df, tr, period)
        return df

    def add_volume_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        self._add_volume_ma(df)
        return df
