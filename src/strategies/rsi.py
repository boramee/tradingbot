"""RSI 전략: 과매수/과매도 + 추세 반전 감지"""

from __future__ import annotations

import pandas as pd
from .base import BaseStrategy, Signal, TradeSignal


class RSIStrategy(BaseStrategy):

    def __init__(self, oversold: float = 30, overbought: float = 70):
        self.oversold = oversold
        self.overbought = overbought

    @property
    def name(self) -> str:
        return "RSI"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        rsi = self._last(df, "rsi")
        if rsi is None:
            return TradeSignal(Signal.HOLD, 0, "RSI 데이터 부족")

        price = float(df["close"].iloc[-1])
        prev = self._prev(df, "rsi")

        if rsi <= self.oversold:
            conf = min(1.0, (self.oversold - rsi) / 15 + 0.5)
            reason = "RSI 과매도 (%.1f)" % rsi
            if prev is not None and rsi > prev:
                conf = min(1.0, conf + 0.15)
                reason += " + 반등"
            return TradeSignal(Signal.BUY, conf, reason, price)

        if rsi >= self.overbought:
            conf = min(1.0, (rsi - self.overbought) / 15 + 0.5)
            reason = "RSI 과매수 (%.1f)" % rsi
            if prev is not None and rsi < prev:
                conf = min(1.0, conf + 0.15)
                reason += " + 하락"
            return TradeSignal(Signal.SELL, conf, reason, price)

        return TradeSignal(Signal.HOLD, 0, "RSI 중립 (%.1f)" % rsi, price)
