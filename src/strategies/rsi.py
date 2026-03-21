"""RSI 전략 v2: 반등/하락 확인 후 진입

기존 문제: RSI < 30이면 즉시 매수 → 계속 하락 중인데 매수 → 손절
개선: RSI가 바닥을 찍고 올라오기 시작할 때(반등 확인) 매수
"""

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

        rsi_prev1 = self._prev(df, "rsi", 2)
        rsi_prev2 = self._prev(df, "rsi", 3)

        if rsi <= self.oversold:
            if rsi_prev1 is not None and rsi > rsi_prev1:
                conf = min(1.0, (self.oversold - rsi) / 15 + 0.5)
                if rsi_prev2 is not None and rsi_prev1 < rsi_prev2:
                    conf = min(1.0, conf + 0.15)
                return TradeSignal(Signal.BUY, conf,
                                   "RSI 과매도 반등 확인 (%.1f→%.1f)" % (rsi_prev1, rsi), price)
            return TradeSignal(Signal.HOLD, 0.3,
                               "RSI 과매도 but 아직 하락 중 (%.1f)" % rsi, price)

        if rsi >= self.overbought:
            if rsi_prev1 is not None and rsi < rsi_prev1:
                conf = min(1.0, (rsi - self.overbought) / 15 + 0.5)
                if rsi_prev2 is not None and rsi_prev1 > rsi_prev2:
                    conf = min(1.0, conf + 0.15)
                return TradeSignal(Signal.SELL, conf,
                                   "RSI 과매수 하락 확인 (%.1f→%.1f)" % (rsi_prev1, rsi), price)
            return TradeSignal(Signal.HOLD, 0.3,
                               "RSI 과매수 but 아직 상승 중 (%.1f)" % rsi, price)

        return TradeSignal(Signal.HOLD, 0, "RSI 중립 (%.1f)" % rsi, price)
