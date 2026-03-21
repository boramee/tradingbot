"""개선된 복합 전략

기존 대비 개선점:
  1. ADX 횡보장 필터 - ADX < 20이면 매매 중지
  2. 거래량 동반 조건 - 평균 거래량 이상일 때만 진입
  3. 상위 차트 정배열 - 단기 이평선 > 장기 이평선일 때만 매수
  4. DI 방향 확인 - +DI > -DI일 때 매수, 반대일 때 매도
"""

from __future__ import annotations

import logging
from typing import List

import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal
from .rsi import RSIStrategy
from .macd import MACDStrategy
from .bollinger import BollingerStrategy

logger = logging.getLogger(__name__)

WEIGHTS = {"RSI": 0.30, "MACD": 0.35, "Bollinger": 0.35}


class CombinedStrategy(BaseStrategy):

    ADX_TREND_THRESHOLD = 20   # 이 이상이면 추세장
    VOLUME_MIN_RATIO = 0.8     # 평균 거래량의 이 비율 이상이어야 진입

    def __init__(self, oversold: float = 30, overbought: float = 70):
        self._strategies: List[BaseStrategy] = [
            RSIStrategy(oversold, overbought),
            MACDStrategy(),
            BollingerStrategy(),
        ]

    @property
    def name(self) -> str:
        return "Combined"

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        price = float(df["close"].iloc[-1])

        # ── 필터 1: ADX 횡보장 감지 ──
        adx = self._last(df, "adx")
        if adx is not None and adx < self.ADX_TREND_THRESHOLD:
            return TradeSignal(Signal.HOLD, 0,
                               "횡보장 감지 (ADX: %.1f < %d)" % (adx, self.ADX_TREND_THRESHOLD), price)

        # ── 필터 2: 거래량 확인 ──
        vol_ratio = self._last(df, "vol_ratio")
        if vol_ratio is not None and vol_ratio < self.VOLUME_MIN_RATIO:
            return TradeSignal(Signal.HOLD, 0,
                               "거래량 부족 (%.1fx < %.1fx)" % (vol_ratio, self.VOLUME_MIN_RATIO), price)

        # ── 전략 신호 수집 ──
        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        for strat in self._strategies:
            sig = strat.analyze(df)
            w = WEIGHTS.get(strat.name, 0.33)
            if sig.signal == Signal.BUY:
                buy_score += sig.confidence * w
                reasons.append("%s:매수" % strat.name)
            elif sig.signal == Signal.SELL:
                sell_score += sig.confidence * w
                reasons.append("%s:매도" % strat.name)

        # ── 필터 3: 이동평균선 정배열/역배열 ──
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        ma_aligned_up = False
        ma_aligned_down = False

        if ma_s is not None and ma_l is not None:
            if ma_s > ma_l:
                ma_aligned_up = True
                buy_score += 0.1
                reasons.append("MA:정배열")
            elif ma_s < ma_l:
                ma_aligned_down = True
                sell_score += 0.1
                reasons.append("MA:역배열")

        # ── 필터 4: DI 방향 확인 ──
        plus_di = self._last(df, "plus_di")
        minus_di = self._last(df, "minus_di")
        di_bullish = plus_di is not None and minus_di is not None and plus_di > minus_di

        # ── 거래량 급등 시 신뢰도 보정 ──
        if vol_ratio is not None and vol_ratio > 1.5:
            boost = min(0.2, (vol_ratio - 1.5) * 0.1)
            buy_score *= (1 + boost)
            sell_score *= (1 + boost)
            reasons.append("거래량: %.1fx" % vol_ratio)

        tag = " | ".join(reasons) if reasons else "없음"
        adx_str = " (ADX:%.0f)" % adx if adx else ""

        # ── 최종 판단 (정배열+DI 방향 필터 적용) ──
        if buy_score > sell_score and buy_score >= 0.3:
            if not ma_aligned_up:
                return TradeSignal(Signal.HOLD, buy_score * 0.5,
                                   "매수신호 있으나 역배열%s: %s" % (adx_str, tag), price)
            if not di_bullish:
                return TradeSignal(Signal.HOLD, buy_score * 0.5,
                                   "매수신호 있으나 -DI 우세%s: %s" % (adx_str, tag), price)
            return TradeSignal(Signal.BUY, min(1.0, buy_score),
                               "매수%s: %s" % (adx_str, tag), price)

        if sell_score > buy_score and sell_score >= 0.3:
            if not ma_aligned_down and not (plus_di and minus_di and minus_di > plus_di):
                return TradeSignal(Signal.HOLD, sell_score * 0.5,
                                   "매도신호 있으나 정배열%s: %s" % (adx_str, tag), price)
            return TradeSignal(Signal.SELL, min(1.0, sell_score),
                               "매도%s: %s" % (adx_str, tag), price)

        return TradeSignal(Signal.HOLD, 0, "관망%s: %s" % (adx_str, tag), price)
