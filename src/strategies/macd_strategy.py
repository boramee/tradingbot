"""MACD 기반 매매 전략"""

import logging

import pandas as pd

from config.settings import IndicatorConfig
from .base_strategy import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger(__name__)


class MACDStrategy(BaseStrategy):
    """
    MACD 전략:
    - MACD가 시그널선 상향돌파 (골든크로스) → 매수
    - MACD가 시그널선 하향돌파 (데드크로스) → 매도
    - 히스토그램 크기로 신뢰도 판단
    """

    @property
    def name(self) -> str:
        return "MACD"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        macd = self._latest(df, "macd")
        signal_line = self._latest(df, "macd_signal")
        histogram = self._latest(df, "macd_histogram")

        if any(v is None for v in (macd, signal_line, histogram)):
            return TradeSignal(Signal.HOLD, 0.0, "MACD 데이터 부족")

        price = float(df["close"].iloc[-1])

        prev_hist = None
        if "macd_histogram" in df.columns and len(df) >= 2:
            val = df["macd_histogram"].iloc[-2]
            if pd.notna(val):
                prev_hist = float(val)

        if prev_hist is not None:
            if prev_hist < 0 < histogram:
                confidence = min(1.0, abs(histogram) / abs(prev_hist + 1e-10) * 0.3 + 0.5)
                return TradeSignal(
                    Signal.BUY,
                    confidence,
                    f"MACD 골든크로스 (hist: {histogram:.2f})",
                    price,
                )

            if prev_hist > 0 > histogram:
                confidence = min(1.0, abs(histogram) / abs(prev_hist + 1e-10) * 0.3 + 0.5)
                return TradeSignal(
                    Signal.SELL,
                    confidence,
                    f"MACD 데드크로스 (hist: {histogram:.2f})",
                    price,
                )

        if histogram > 0 and macd > 0:
            return TradeSignal(Signal.HOLD, 0.3, f"MACD 상승 추세 (hist: {histogram:.2f})", price)
        if histogram < 0 and macd < 0:
            return TradeSignal(Signal.HOLD, 0.3, f"MACD 하락 추세 (hist: {histogram:.2f})", price)

        return TradeSignal(Signal.HOLD, 0.0, "MACD 중립", price)
