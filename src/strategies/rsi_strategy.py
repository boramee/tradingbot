"""RSI 기반 매매 전략"""

import logging

import pandas as pd

from config.settings import IndicatorConfig
from .base_strategy import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger(__name__)


class RSIStrategy(BaseStrategy):
    """
    RSI(상대강도지수) 전략:
    - RSI < 과매도 기준(30) → 매수 신호
    - RSI > 과매수 기준(70) → 매도 신호
    - RSI 추세 반전도 함께 고려
    """

    @property
    def name(self) -> str:
        return "RSI"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        rsi = self._latest(df, "rsi")
        if rsi is None:
            return TradeSignal(Signal.HOLD, 0.0, "RSI 데이터 부족")

        price = float(df["close"].iloc[-1])
        prev_rsi = self._latest_at(df, "rsi", -2)

        if rsi <= self.config.rsi_oversold:
            confidence = min(1.0, (self.config.rsi_oversold - rsi) / 15 + 0.5)
            reason = f"RSI 과매도 ({rsi:.1f})"
            if prev_rsi is not None and rsi > prev_rsi:
                confidence = min(1.0, confidence + 0.15)
                reason += " + 반등 시작"
            return TradeSignal(Signal.BUY, confidence, reason, price)

        if rsi >= self.config.rsi_overbought:
            confidence = min(1.0, (rsi - self.config.rsi_overbought) / 15 + 0.5)
            reason = f"RSI 과매수 ({rsi:.1f})"
            if prev_rsi is not None and rsi < prev_rsi:
                confidence = min(1.0, confidence + 0.15)
                reason += " + 하락 시작"
            return TradeSignal(Signal.SELL, confidence, reason, price)

        return TradeSignal(Signal.HOLD, 0.0, f"RSI 중립 ({rsi:.1f})", price)

    def _latest_at(self, df: pd.DataFrame, column: str, idx: int) -> float | None:
        if column in df.columns and len(df) >= abs(idx):
            val = df[column].iloc[idx]
            if pd.notna(val):
                return float(val)
        return None
