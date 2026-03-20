"""MACD 전략: 골든/데드 크로스"""

from __future__ import annotations

import pandas as pd
from .base import BaseStrategy, Signal, TradeSignal


class MACDStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "MACD"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        hist = self._last(df, "macd_hist")
        prev = self._prev(df, "macd_hist")

        if hist is None or prev is None:
            return TradeSignal(Signal.HOLD, 0, "MACD 데이터 부족")

        price = float(df["close"].iloc[-1])

        if prev < 0 < hist:
            conf = min(1.0, abs(hist) / (abs(prev) + 1e-10) * 0.3 + 0.5)
            return TradeSignal(Signal.BUY, conf, "MACD 골든크로스 (hist: %.2f)" % hist, price)

        if prev > 0 > hist:
            conf = min(1.0, abs(hist) / (abs(prev) + 1e-10) * 0.3 + 0.5)
            return TradeSignal(Signal.SELL, conf, "MACD 데드크로스 (hist: %.2f)" % hist, price)

        return TradeSignal(Signal.HOLD, 0, "MACD 중립", price)
