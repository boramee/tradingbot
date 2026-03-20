"""볼린저 밴드 전략

- 하단 밴드 하향 돌파 후 반등 → 매수
- 상단 밴드 상향 돌파 후 하락 → 매도
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal


class BollingerStrategy(BaseStrategy):

    def __init__(self, buy_threshold: float = 0.05, sell_threshold: float = 0.95):
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    @property
    def name(self) -> str:
        return "Bollinger"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        pctb = self._last(df, "bb_pctb")
        prev_pctb = self._prev(df, "bb_pctb")
        price = self._last(df, "close") or 0.0

        if pctb is None:
            return TradeSignal(Signal.HOLD, 0.0, "볼린저 데이터 부족", price)

        if pctb <= self.buy_threshold:
            conf = min(1.0, (self.buy_threshold - pctb) / 0.2 + 0.5)
            return TradeSignal(Signal.BUY, conf, f"볼린저 하단 근접 (%B: {pctb:.2f})", price)

        if prev_pctb is not None and prev_pctb <= 0 and pctb > 0:
            return TradeSignal(Signal.BUY, 0.7, f"볼린저 하단 반등 (%B: {prev_pctb:.2f}→{pctb:.2f})", price)

        if pctb >= self.sell_threshold:
            conf = min(1.0, (pctb - self.sell_threshold) / 0.2 + 0.5)
            return TradeSignal(Signal.SELL, conf, f"볼린저 상단 근접 (%B: {pctb:.2f})", price)

        if prev_pctb is not None and prev_pctb >= 1.0 and pctb < 1.0:
            return TradeSignal(Signal.SELL, 0.7, f"볼린저 상단 이탈 (%B: {prev_pctb:.2f}→{pctb:.2f})", price)

        return TradeSignal(Signal.HOLD, 0.0, f"볼린저 중립 (%B: {pctb:.2f})", price)
