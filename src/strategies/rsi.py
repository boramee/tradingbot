"""RSI 전략

- RSI 30 이하: 과매도 → 매수
- RSI 70 이상: 과매수 → 매도
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal


class RSIStrategy(BaseStrategy):

    def __init__(self, oversold: float = 30.0, overbought: float = 70.0):
        self.oversold = oversold
        self.overbought = overbought

    @property
    def name(self) -> str:
        return "RSI"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        rsi = self._last(df, "rsi")
        prev_rsi = self._prev(df, "rsi")
        price = self._last(df, "close") or 0.0

        if rsi is None:
            return TradeSignal(Signal.HOLD, 0.0, "RSI 데이터 부족", price)

        if rsi <= self.oversold:
            conf = min(1.0, (self.oversold - rsi) / 15 + 0.5)
            return TradeSignal(Signal.BUY, conf, f"RSI 과매도 ({rsi:.1f})", price)

        if rsi >= self.overbought:
            conf = min(1.0, (rsi - self.overbought) / 15 + 0.5)
            return TradeSignal(Signal.SELL, conf, f"RSI 과매수 ({rsi:.1f})", price)

        if prev_rsi and prev_rsi <= self.oversold and rsi > self.oversold:
            return TradeSignal(Signal.BUY, 0.6, f"RSI 과매도 탈출 ({prev_rsi:.1f}→{rsi:.1f})", price)

        if prev_rsi and prev_rsi >= self.overbought and rsi < self.overbought:
            return TradeSignal(Signal.SELL, 0.6, f"RSI 과매수 탈출 ({prev_rsi:.1f}→{rsi:.1f})", price)

        return TradeSignal(Signal.HOLD, 0.0, f"RSI 중립 ({rsi:.1f})", price)
