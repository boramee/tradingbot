"""볼린저밴드 전략: 상/하단 돌파"""

from __future__ import annotations

import pandas as pd
from .base import BaseStrategy, Signal, TradeSignal


class BollingerStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "Bollinger"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        close = self._last(df, "close")
        upper = self._last(df, "bb_upper")
        lower = self._last(df, "bb_lower")
        pctb = self._last(df, "bb_pctb")

        if any(v is None for v in (close, upper, lower)):
            return TradeSignal(Signal.HOLD, 0, "볼린저 데이터 부족")

        if close <= lower:
            d = (lower - close) / lower * 100
            return TradeSignal(Signal.BUY, min(1.0, d / 2 + 0.55),
                               "하단 돌파 (%%B: %.2f)" % (pctb or 0), close)

        if close >= upper:
            d = (close - upper) / upper * 100
            return TradeSignal(Signal.SELL, min(1.0, d / 2 + 0.55),
                               "상단 돌파 (%%B: %.2f)" % (pctb or 0), close)

        if pctb is not None and pctb < 0.2:
            return TradeSignal(Signal.BUY, 0.4, "하단 접근 (%%B: %.2f)" % pctb, close)
        if pctb is not None and pctb > 0.8:
            return TradeSignal(Signal.SELL, 0.4, "상단 접근 (%%B: %.2f)" % pctb, close)

        return TradeSignal(Signal.HOLD, 0, "볼린저 중립", close)
