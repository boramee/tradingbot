"""MACD 전략 v4: 골든/데드 크로스 + 모멘텀 반전 조기진입 + 추세강도 매도 완화

v3 → v4 변경점:
  - 히스토그램 모멘텀 반전 조기진입: 크로스 전에 기울기 반전으로 선행 매수
  - 강한 상승추세(ADX>25+정배열)에서 데드크로스 → 전량매도 대신 약한 매도
  - 히스토그램 3봉 연속 축소 감지: 추세 약화 조기 경고
"""

from __future__ import annotations

import pandas as pd
from .base import BaseStrategy, Signal, TradeSignal


class MACDStrategy(BaseStrategy):

    MIN_VOLUME_RATIO = 0.3
    MIN_ADX = 15

    @property
    def name(self) -> str:
        return "MACD"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        hist = self._last(df, "macd_hist")
        prev = self._prev(df, "macd_hist")
        prev2 = self._prev(df, "macd_hist", 3)

        if hist is None or prev is None:
            return TradeSignal(Signal.HOLD, 0, "MACD 데이터 부족")

        price = float(df["close"].iloc[-1])

        vol_ratio = self._last(df, "vol_ratio")
        adx = self._last(df, "adx")
        macd_line = self._last(df, "macd")

        # 이평선
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        ma_bull = ma_s is not None and ma_l is not None and ma_s > ma_l
        ma_bear = ma_s is not None and ma_l is not None and ma_s < ma_l

        # 히스토그램 모멘텀 (연속 증가/감소)
        hist_accelerating = prev2 is not None and hist > prev > prev2
        hist_decelerating = prev2 is not None and hist < prev < prev2

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

            # MACD 0선 위에서 크로스 = 더 강한 신호
            if macd_line is not None and macd_line > 0:
                conf = min(1.0, conf + 0.1)
                reason += ", 0선위"

            if ma_bull:
                conf = min(1.0, conf + 0.1)
                reason += ", 정배열"
            elif ma_bear:
                conf *= 0.6  # v2: 0.5 → v3: 0.6 (역배열 패널티 완화)
                reason += ", 역배열→신뢰↓"

            if vol_ratio is not None and vol_ratio > 1.5:
                conf = min(1.0, conf + 0.1)
                reason += ", 거래량%.1fx" % vol_ratio

            if hist_accelerating:
                conf = min(1.0, conf + 0.1)
                reason += ", 모멘텀가속"

            reason += ")"
            return TradeSignal(Signal.BUY, conf, reason, price)

        # 데드크로스 (매도) — v4: 강한 상승추세에서는 신뢰도 대폭 하향
        if prev > 0 > hist:
            conf = min(1.0, abs(hist) / (abs(prev) + 1e-10) * 0.3 + 0.5)
            reason = "MACD 데드크로스 (hist:%.2f" % hist

            if ma_bear:
                conf = min(1.0, conf + 0.1)
                reason += ", 역배열"
            elif ma_bull:
                # v4: 강한 상승추세(ADX>25+정배열) → 단순 조정일 가능성
                if adx is not None and adx >= 25:
                    conf *= 0.4  # v3: 0.6 → v4: 0.4 (추세 중 풀백은 매도X)
                    reason += ", 강한상승중→풀백가능"
                else:
                    conf *= 0.6
                    reason += ", 정배열→신뢰↓"

            if hist_decelerating:
                conf = min(1.0, conf + 0.1)
                reason += ", 하락가속"

            reason += ")"
            return TradeSignal(Signal.SELL, conf, reason, price)

        # v4: 히스토그램 모멘텀 반전 조기진입
        # hist가 아직 음수이지만 바닥 찍고 올라오기 시작 → 크로스 전 선행 매수
        if hist < 0 and prev < 0 and prev2 is not None and prev2 < 0:
            slope_now = hist - prev
            slope_prev = prev - prev2
            # 기울기가 음→양 전환 (모멘텀 반전) + ADX 확인
            if slope_now > 0 > slope_prev and abs(hist) < abs(prev):
                if adx is not None and adx >= self.MIN_ADX:
                    if vol_ratio is not None and vol_ratio >= self.MIN_VOLUME_RATIO:
                        # 기존 크로스보다 낮은 신뢰도 (선행 신호이므로)
                        conf = min(0.65, abs(slope_now) / (abs(slope_prev) + 1e-10) * 0.2 + 0.4)
                        reason = "MACD 모멘텀반전 (hist:%.2f→%.2f" % (prev, hist)
                        if ma_bull:
                            conf = min(0.75, conf + 0.1)
                            reason += ", 정배열"
                        if adx is not None:
                            reason += ", ADX:%.0f" % adx
                        reason += ")"
                        return TradeSignal(Signal.BUY, conf, reason, price)

        # 양수 히스토그램 가속 (기존 유지)
        if hist > 0 and hist_accelerating and hist > abs(prev) * 0.5:
            if adx is not None and adx >= self.MIN_ADX:
                conf = min(0.6, hist / (abs(prev) + 1e-10) * 0.2 + 0.3)
                return TradeSignal(Signal.BUY, conf,
                                   "MACD 모멘텀 가속 (hist:%.2f, ADX:%.0f)" % (hist, adx), price)

        return TradeSignal(Signal.HOLD, 0, "MACD 중립", price)
