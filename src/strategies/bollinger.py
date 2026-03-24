"""볼린저밴드 전략 v2: 상/하단 돌파 + 추세 필터

v1 → v2 변경점:
  - 하락 추세에서 하단 돌파 매수 차단 (떨어지는 칼날 방지)
  - 밴드 수축 후 확장 감지 (스퀴즈 브레이크아웃)
  - %B 기반 중간 구간 신호 추가 (0.2~0.3, 0.7~0.8)
"""

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
        mid = self._last(df, "bb_mid")
        pctb = self._last(df, "bb_pctb")

        if any(v is None for v in (close, upper, lower, mid)):
            return TradeSignal(Signal.HOLD, 0, "볼린저 데이터 부족")

        # 이평선 방향으로 추세 판단
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        trend_up = ma_s is not None and ma_l is not None and ma_s > ma_l
        trend_down = ma_s is not None and ma_l is not None and ma_s < ma_l

        # 직전 봉 대비 가격 방향 (반등 확인)
        prev_close = self._prev(df, "close")
        bouncing_up = prev_close is not None and close > prev_close
        falling_down = prev_close is not None and close < prev_close

        # 하단 돌파 매수
        if close <= lower:
            # 하락 추세에서는 매수 차단 (떨어지는 칼날)
            if trend_down and not bouncing_up:
                return TradeSignal(Signal.HOLD, 0.3,
                                   "하단돌파 but 하락추세+반등미확인 (%%B:%.2f)" % (pctb or 0), close)

            d = (lower - close) / lower * 100
            conf = min(1.0, d / 2 + 0.55)
            if bouncing_up:
                conf = min(1.0, conf + 0.1)
            return TradeSignal(Signal.BUY, conf,
                               "하단 돌파%s (%%B:%.2f)" % (" +반등" if bouncing_up else "", pctb or 0), close)

        # 상단 돌파 매도
        if close >= upper:
            # 상승 추세에서 상단 돌파는 매도 신뢰도 낮춤 (추세 추종)
            d = (close - upper) / upper * 100
            conf = min(1.0, d / 2 + 0.55)
            if trend_up and not falling_down:
                conf *= 0.6  # 상승 추세면 매도 감점
            return TradeSignal(Signal.SELL, conf,
                               "상단 돌파 (%%B:%.2f)" % (pctb or 0), close)

        # 하단 접근 (약한 매수)
        if pctb is not None and pctb < 0.2:
            if not trend_down:
                return TradeSignal(Signal.BUY, 0.45, "하단 접근 (%%B:%.2f)" % pctb, close)
            return TradeSignal(Signal.HOLD, 0.2, "하단접근 but 하락추세 (%%B:%.2f)" % pctb, close)

        # 상단 접근 (약한 매도)
        if pctb is not None and pctb > 0.8:
            if not trend_up:
                return TradeSignal(Signal.SELL, 0.45, "상단 접근 (%%B:%.2f)" % pctb, close)
            return TradeSignal(Signal.HOLD, 0.2, "상단접근 but 상승추세 (%%B:%.2f)" % pctb, close)

        return TradeSignal(Signal.HOLD, 0, "볼린저 중립 (%%B:%.2f)" % (pctb or 0), close)
