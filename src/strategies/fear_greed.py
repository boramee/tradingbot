"""공포에 사서 환희에 팔아라 전략 (야수의 심장)

핵심 로직:
  시장 공포 = 남들이 던질 때 = 싸게 살 기회
  시장 환희 = 남들이 살 때 = 비싸게 팔 기회

공포/탐욕 판단 기준:
  1. 가격 위치: 최근 N일 중 어디? (0%=바닥, 100%=천장)
  2. RSI: 30 이하=극도의 공포, 70 이상=극도의 탐욕
  3. 볼린저 %B: 0 이하=공포, 1 이상=탐욕
  4. 거래량 급증: 공포 패닉셀 or 탐욕 FOMO
  5. 연속 하락/상승 일수

공포 구간 진입 (분할매수):
  공포 레벨 1 (약간 공포): 자금의 30% 매수
  공포 레벨 2 (강한 공포): 자금의 30% 추가 매수
  공포 레벨 3 (극도의 공포): 자금의 40% 올인

환희 구간 청산 (분할매도):
  탐욕 레벨 1: 보유량의 30% 매도
  탐욕 레벨 2: 보유량의 30% 추가 매도
  탐욕 레벨 3: 전량 매도
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger(__name__)


class FearGreedStrategy(BaseStrategy):

    # 공포/탐욕 점수 경계 (0=극공포, 50=중립, 100=극탐욕)
    EXTREME_FEAR = 20
    FEAR = 35
    GREED = 65
    EXTREME_GREED = 80

    def __init__(self, lookback: int = 60):
        self.lookback = lookback  # 최근 N봉 기준

    @property
    def name(self) -> str:
        return "FearGreed"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        if len(df) < self.lookback:
            return TradeSignal(Signal.HOLD, 0, "데이터 부족")

        price = float(df["close"].iloc[-1])
        score = self._calc_fear_greed(df)

        if score <= self.EXTREME_FEAR:
            conf = min(1.0, (self.EXTREME_FEAR - score) / 20 + 0.7)
            return TradeSignal(Signal.BUY, conf,
                               "극도의 공포 (지수:%.0f) → 야수의 심장 매수" % score, price)

        if score <= self.FEAR:
            conf = min(1.0, (self.FEAR - score) / 15 + 0.5)
            return TradeSignal(Signal.BUY, conf,
                               "공포 구간 (지수:%.0f) → 분할 매수" % score, price)

        if score >= self.EXTREME_GREED:
            conf = min(1.0, (score - self.EXTREME_GREED) / 20 + 0.7)
            return TradeSignal(Signal.SELL, conf,
                               "극도의 탐욕 (지수:%.0f) → 전량 매도" % score, price)

        if score >= self.GREED:
            conf = min(1.0, (score - self.GREED) / 15 + 0.5)
            return TradeSignal(Signal.SELL, conf,
                               "탐욕 구간 (지수:%.0f) → 분할 매도" % score, price)

        return TradeSignal(Signal.HOLD, 0,
                           "중립 (지수:%.0f)" % score, price)

    def _calc_fear_greed(self, df: pd.DataFrame) -> float:
        """공포/탐욕 지수 계산 (0~100)"""
        scores = []

        # 1. 가격 위치 (최근 N일 중 현재 위치) — 25%
        recent = df["close"].iloc[-self.lookback:]
        price = float(df["close"].iloc[-1])
        low = float(recent.min())
        high = float(recent.max())
        if high > low:
            price_position = (price - low) / (high - low) * 100
        else:
            price_position = 50
        scores.append(("가격위치", price_position, 0.25))

        # 2. RSI — 25%
        rsi = self._last(df, "rsi")
        if rsi is not None:
            rsi_score = rsi  # RSI 자체가 0~100
            scores.append(("RSI", rsi_score, 0.25))

        # 3. 볼린저 %B — 20%
        pctb = self._last(df, "bb_pctb")
        if pctb is not None:
            bb_score = max(0, min(100, pctb * 100))
            scores.append(("BB%B", bb_score, 0.20))

        # 4. 거래량 이상 — 15%
        vol_ratio = self._last(df, "vol_ratio")
        if vol_ratio is not None:
            # 거래량 급증 + 하락 = 패닉셀(공포), 거래량 급증 + 상승 = FOMO(탐욕)
            change = (price - float(df["close"].iloc[-2])) / float(df["close"].iloc[-2]) * 100
            if vol_ratio > 2.0 and change < -2:
                vol_score = 10  # 패닉셀 = 극공포
            elif vol_ratio > 2.0 and change > 2:
                vol_score = 90  # FOMO = 극탐욕
            else:
                vol_score = 50
            scores.append(("거래량", vol_score, 0.15))

        # 5. 연속 하락/상승 — 15%
        consec = self._consecutive_direction(df)
        if consec < -4:
            streak_score = 10  # 5일 이상 연속 하락 = 극공포
        elif consec < -2:
            streak_score = 25
        elif consec > 4:
            streak_score = 90  # 5일 이상 연속 상승 = 극탐욕
        elif consec > 2:
            streak_score = 75
        else:
            streak_score = 50
        scores.append(("연속", streak_score, 0.15))

        # 가중평균
        total_weight = sum(w for _, _, w in scores)
        if total_weight <= 0:
            return 50

        weighted = sum(s * w for _, s, w in scores) / total_weight
        return weighted

    @staticmethod
    def _consecutive_direction(df: pd.DataFrame, lookback: int = 10) -> int:
        """최근 연속 상승/하락 일수. 양수=상승, 음수=하락"""
        if len(df) < 2:
            return 0
        changes = df["close"].diff().iloc[-lookback:]
        count = 0
        last_dir = None
        for ch in reversed(changes.dropna()):
            d = 1 if ch > 0 else -1 if ch < 0 else 0
            if d == 0:
                break
            if last_dir is None:
                last_dir = d
            if d != last_dir:
                break
            count += d
        return count
