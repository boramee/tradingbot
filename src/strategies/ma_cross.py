"""이동평균 크로스 전략

- 단기(5일) MA가 중기(20일) MA 상향 돌파 → 매수
- 단기(5일) MA가 중기(20일) MA 하향 돌파 → 매도
- 중기(20일) MA가 장기(60일) MA 위에 있으면 상승 추세로 판단
"""

from __future__ import annotations

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal


class MACrossStrategy(BaseStrategy):

    @property
    def name(self) -> str:
        return "MA Cross"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        ma_s = self._last(df, "ma_short")
        ma_m = self._last(df, "ma_mid")
        ma_l = self._last(df, "ma_long")
        prev_ma_s = self._prev(df, "ma_short")
        prev_ma_m = self._prev(df, "ma_mid")
        price = self._last(df, "close") or 0.0

        if ma_s is None or ma_m is None or prev_ma_s is None or prev_ma_m is None:
            return TradeSignal(Signal.HOLD, 0.0, "이동평균 데이터 부족", price)

        golden_cross = prev_ma_s <= prev_ma_m and ma_s > ma_m
        dead_cross = prev_ma_s >= prev_ma_m and ma_s < ma_m

        uptrend = ma_l is not None and ma_m > ma_l

        if golden_cross:
            conf = 0.7 if uptrend else 0.55
            trend_str = " (상승추세)" if uptrend else ""
            return TradeSignal(Signal.BUY, conf, f"이평선 골든크로스{trend_str}", price)

        if dead_cross:
            conf = 0.7 if not uptrend else 0.55
            trend_str = " (하락추세)" if not uptrend else ""
            return TradeSignal(Signal.SELL, conf, f"이평선 데드크로스{trend_str}", price)

        if ma_s > ma_m:
            return TradeSignal(Signal.HOLD, 0.3, "단기>중기 이평 (매수 유지)", price)

        return TradeSignal(Signal.HOLD, 0.0, "단기<중기 이평 (관망)", price)
