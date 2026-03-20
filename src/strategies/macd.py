"""MACD 전략

- MACD가 시그널 상향 돌파 (골든크로스) → 매수
- MACD가 시그널 하향 돌파 (데드크로스) → 매도
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal


class MACDStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "MACD"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        macd_hist = self._last(df, "macd_hist")
        prev_hist = self._prev(df, "macd_hist")
        macd = self._last(df, "macd")
        price = self._last(df, "close") or 0.0

        if macd_hist is None or prev_hist is None:
            return TradeSignal(Signal.HOLD, 0.0, "MACD 데이터 부족", price)

        if prev_hist <= 0 < macd_hist:
            conf = min(1.0, abs(macd_hist) / (abs(macd) + 1e-10) + 0.5)
            conf = min(conf, 1.0)
            return TradeSignal(Signal.BUY, conf, f"MACD 골든크로스 (hist: {macd_hist:.0f})", price)

        if prev_hist >= 0 > macd_hist:
            conf = min(1.0, abs(macd_hist) / (abs(macd) + 1e-10) + 0.5)
            conf = min(conf, 1.0)
            return TradeSignal(Signal.SELL, conf, f"MACD 데드크로스 (hist: {macd_hist:.0f})", price)

        if macd_hist > 0 and macd_hist > prev_hist:
            return TradeSignal(Signal.HOLD, 0.3, f"MACD 상승 지속 (hist: {macd_hist:.0f})", price)

        if macd_hist < 0 and macd_hist < prev_hist:
            return TradeSignal(Signal.HOLD, 0.3, f"MACD 하락 지속 (hist: {macd_hist:.0f})", price)

        return TradeSignal(Signal.HOLD, 0.0, f"MACD 중립 (hist: {macd_hist:.0f})", price)
