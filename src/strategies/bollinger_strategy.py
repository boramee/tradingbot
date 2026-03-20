"""볼린저 밴드 기반 매매 전략"""

import logging

import pandas as pd

from config.settings import IndicatorConfig
from .base_strategy import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger(__name__)


class BollingerStrategy(BaseStrategy):
    """
    볼린저 밴드 전략:
    - 가격이 하단밴드 이하 → 매수 (과매도)
    - 가격이 상단밴드 이상 → 매도 (과매수)
    - %B 지표로 정밀한 위치 판단
    """

    @property
    def name(self) -> str:
        return "Bollinger"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        close = self._latest(df, "close")
        bb_upper = self._latest(df, "bb_upper")
        bb_lower = self._latest(df, "bb_lower")
        bb_middle = self._latest(df, "bb_middle")
        bb_pctb = self._latest(df, "bb_pctb")

        if any(v is None for v in (close, bb_upper, bb_lower, bb_middle)):
            return TradeSignal(Signal.HOLD, 0.0, "볼린저밴드 데이터 부족")

        price = close

        if close <= bb_lower:
            distance = (bb_lower - close) / bb_lower * 100
            confidence = min(1.0, distance / 2 + 0.55)
            return TradeSignal(
                Signal.BUY,
                confidence,
                f"볼린저 하단 돌파 (%B: {bb_pctb:.2f})" if bb_pctb else "볼린저 하단 돌파",
                price,
            )

        if close >= bb_upper:
            distance = (close - bb_upper) / bb_upper * 100
            confidence = min(1.0, distance / 2 + 0.55)
            return TradeSignal(
                Signal.SELL,
                confidence,
                f"볼린저 상단 돌파 (%B: {bb_pctb:.2f})" if bb_pctb else "볼린저 상단 돌파",
                price,
            )

        if bb_pctb is not None and bb_pctb < 0.2:
            return TradeSignal(
                Signal.BUY, 0.4, f"볼린저 하단 접근 (%B: {bb_pctb:.2f})", price
            )
        if bb_pctb is not None and bb_pctb > 0.8:
            return TradeSignal(
                Signal.SELL, 0.4, f"볼린저 상단 접근 (%B: {bb_pctb:.2f})", price
            )

        return TradeSignal(Signal.HOLD, 0.0, "볼린저밴드 중립", price)
