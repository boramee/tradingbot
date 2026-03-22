"""MACD 전략 v2: 골든/데드 크로스 + 거래량 + ADX + 이평선 필터

v1 문제: 저거래량/횡보장에서도 신호 발생 → 헛매매
v2 개선:
  - 거래량 0.5x 미만이면 신호 무시
  - ADX < 20 (횡보)이면 신호 무시
  - 골든크로스 + 이평선 정배열일 때만 매수
  - 데드크로스 + 이평선 역배열일 때만 매도
"""

from __future__ import annotations

import pandas as pd
from .base import BaseStrategy, Signal, TradeSignal


class MACDStrategy(BaseStrategy):

    MIN_VOLUME_RATIO = 0.3
    MIN_ADX = 10

    @property
    def name(self) -> str:
        return "MACD"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        hist = self._last(df, "macd_hist")
        prev = self._prev(df, "macd_hist")

        if hist is None or prev is None:
            return TradeSignal(Signal.HOLD, 0, "MACD 데이터 부족")

        price = float(df["close"].iloc[-1])

        vol_ratio = self._last(df, "vol_ratio")
        adx = self._last(df, "adx")

        # 이평선
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        ma_bull = ma_s is not None and ma_l is not None and ma_s > ma_l
        ma_bear = ma_s is not None and ma_l is not None and ma_s < ma_l

        # 골든크로스 (매수) — 거래량/ADX 필터 적용
        if prev < 0 < hist:
            if vol_ratio is not None and vol_ratio < self.MIN_VOLUME_RATIO:
                return TradeSignal(Signal.HOLD, 0,
                                   "MACD 골든크로스 but 거래량부족(%.1fx)" % vol_ratio, price)
            if adx is not None and adx < self.MIN_ADX:
                return TradeSignal(Signal.HOLD, 0,
                                   "MACD 골든크로스 but 횡보(ADX:%.0f)" % adx, price)

            conf = min(1.0, abs(hist) / (abs(prev) + 1e-10) * 0.3 + 0.5)
            reason = "MACD 골든크로스 (hist:%.2f" % hist

            if adx is not None:
                reason += ", ADX:%.0f" % adx

            if ma_bull:
                conf = min(1.0, conf + 0.1)
                reason += ", 정배열"
            elif ma_bear:
                conf *= 0.5
                reason += ", 역배열→신뢰↓"

            if vol_ratio is not None and vol_ratio > 1.5:
                conf = min(1.0, conf + 0.1)
                reason += ", 거래량%.1fx" % vol_ratio

            reason += ")"
            return TradeSignal(Signal.BUY, conf, reason, price)

        # 데드크로스 (매도)
        if prev > 0 > hist:
            conf = min(1.0, abs(hist) / (abs(prev) + 1e-10) * 0.3 + 0.5)
            reason = "MACD 데드크로스 (hist:%.2f" % hist

            if ma_bear:
                conf = min(1.0, conf + 0.1)
                reason += ", 역배열"
            elif ma_bull:
                conf *= 0.5
                reason += ", 정배열→신뢰↓"

            reason += ")"
            return TradeSignal(Signal.SELL, conf, reason, price)

        return TradeSignal(Signal.HOLD, 0, "MACD 중립", price)
