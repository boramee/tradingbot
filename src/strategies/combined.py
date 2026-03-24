"""고급 복합 전략 v3

v2 → v3 변경점:
  1. MA 정배열 필수 조건 제거 → 보너스로 전환 (정배열이면 신뢰도 ↑)
  2. DI 방향 필수 조건 제거 → 보너스로 전환
  3. 저항선/지지선 근처에서 진입 차단 완화 (감점만, 완전 차단 X)
  4. 상위TF 하락 시 매수 차단 → 신뢰도 감점으로 완화
  5. 횡보장에서도 볼린저 신호가 강하면 진입 허용
  6. 거래량 필터 0.8 → 0.5로 완화

이유: v2는 너무 많은 조건이 AND로 묶여서 진입 기회가 거의 없었음.
     실전에서 수익을 내려면 적절한 진입 빈도가 필요.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import pandas as pd
import pyupbit

from .base import BaseStrategy, Signal, TradeSignal
from .rsi import RSIStrategy
from .macd import MACDStrategy
from .bollinger import BollingerStrategy
from src.indicators.advanced import AdvancedIndicators

logger = logging.getLogger(__name__)

WEIGHTS = {"RSI": 0.30, "MACD": 0.35, "Bollinger": 0.35}


class CombinedStrategy(BaseStrategy):

    ADX_TREND_THRESHOLD = 20
    VOLUME_MIN_RATIO = 0.5          # v2: 0.8 → v3: 0.5 (완화)
    DIVERGENCE_BOOST = 0.25

    def __init__(self, oversold: float = 30, overbought: float = 70):
        self._strategies: List[BaseStrategy] = [
            RSIStrategy(oversold, overbought),
            MACDStrategy(),
            BollingerStrategy(),
        ]
        self._adv = AdvancedIndicators()
        self._higher_tf_trend: Optional[str] = None
        self._htf_ticker: str = ""
        self._htf_indicators: Optional["TechnicalIndicators"] = None

    @property
    def name(self) -> str:
        return "Combined_v3"

    def set_higher_timeframe(self, ticker: str, htf_interval: str = "minute60"):
        """상위 타임프레임 추세 캐시 (엔진에서 주기적 호출)"""
        try:
            if self._htf_indicators is None:
                from src.indicators.technical import TechnicalIndicators
                self._htf_indicators = TechnicalIndicators()
            df = pyupbit.get_ohlcv(ticker, interval=htf_interval, count=100)
            if df is not None and not df.empty:
                df.columns = ["open", "high", "low", "close", "volume", "value"]
                df = self._htf_indicators.add_all(df)
                self._higher_tf_trend = self._adv.classify_market(df)
                self._htf_ticker = ticker
                logger.debug("상위TF 추세: %s (%s)", self._higher_tf_trend, htf_interval)
        except Exception as e:
            logger.debug("상위TF 조회 실패: %s", e)

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        price = float(df["close"].iloc[-1])

        # ── 시장 상태 분류 ──
        market = self._adv.classify_market(df)
        adx = self._last(df, "adx")

        # 고변동장만 완전 차단, 횡보장은 볼린저 신호 허용
        if market == "volatile":
            return TradeSignal(Signal.HOLD, 0,
                               "[고변동] 매매 중지 (BB 밴드폭 과대)", price)

        # ── 거래량 필터 (완화) ──
        vol_ratio = self._last(df, "vol_ratio")
        if vol_ratio is not None and vol_ratio < self.VOLUME_MIN_RATIO:
            return TradeSignal(Signal.HOLD, 0,
                               "거래량 부족 (%.1fx < %.1f)" % (vol_ratio, self.VOLUME_MIN_RATIO), price)

        # ── 전략 신호 수집 ──
        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        for strat in self._strategies:
            sig = strat.analyze(df)
            w = WEIGHTS.get(strat.name, 0.33)
            if sig.signal == Signal.BUY:
                buy_score += sig.confidence * w
                reasons.append("%s:매수(%.0f%%)" % (strat.name, sig.confidence * 100))
            elif sig.signal == Signal.SELL:
                sell_score += sig.confidence * w
                reasons.append("%s:매도(%.0f%%)" % (strat.name, sig.confidence * 100))

        # ── RSI 다이버전스 (강력 신호) ──
        divergence = self._adv.detect_rsi_divergence(df)
        if divergence == "bullish":
            buy_score += self.DIVERGENCE_BOOST
            reasons.append("RSI상승다이버전스")
        elif divergence == "bearish":
            sell_score += self.DIVERGENCE_BOOST
            reasons.append("RSI하락다이버전스")

        # ── 보너스/감점 (v3: 필수 → 보너스로 전환) ──
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        plus_di = self._last(df, "plus_di")
        minus_di = self._last(df, "minus_di")
        htf = self._higher_tf_trend

        # 이동평균선 정배열/역배열 → 보너스
        if ma_s is not None and ma_l is not None:
            if ma_s > ma_l:
                buy_score += 0.1
                reasons.append("정배열")
            else:
                sell_score += 0.1
                reasons.append("역배열")

        # DI 방향 → 보너스
        if plus_di is not None and minus_di is not None:
            if plus_di > minus_di:
                buy_score += 0.05
            else:
                sell_score += 0.05

        # 거래량 급등 보정
        if vol_ratio is not None and vol_ratio > 1.5:
            boost = min(0.15, (vol_ratio - 1.5) * 0.1)
            buy_score *= (1 + boost)
            sell_score *= (1 + boost)
            reasons.append("거래량%.1fx" % vol_ratio)

        # ── 피봇포인트 → 감점만 (차단하지 않음) ──
        pivots = self._adv.pivot_points(df)
        sr_level = self._adv.near_support_resistance(price, pivots)

        tag = " | ".join(reasons) if reasons else "없음"
        mkt_str = " [%s]" % market
        htf_str = " HTF:%s" % htf if htf else ""

        # ── 최종 매수 판단 ──
        if buy_score > sell_score and buy_score >= 0.3:
            conf = min(1.0, buy_score)

            # 감점 요소 (v3: 차단 → 감점)
            if sr_level and "resistance" in sr_level:
                conf *= 0.7
                reasons.append("저항선근처")
            if htf == "trending_down":
                conf *= 0.6
                reasons.append("상위TF하락")
            if market == "ranging":
                conf *= 0.8  # 횡보장에서도 감점만

            # 보너스 요소
            if divergence == "bullish":
                conf = min(1.0, conf + 0.1)
            if htf == "trending_up":
                conf = min(1.0, conf + 0.1)

            tag = " | ".join(reasons) if reasons else "없음"
            if conf >= 0.45:  # v3.1: 0.35→0.45 (저품질 신호 걸러냄, is_actionable≥0.5 이중 필터)
                return TradeSignal(Signal.BUY, conf,
                                   "매수%s%s: %s" % (mkt_str, htf_str, tag), price)

        # ── 최종 매도 판단 ──
        if sell_score > buy_score and sell_score >= 0.3:
            conf = min(1.0, sell_score)

            if sr_level and "support" in sr_level:
                conf *= 0.7
            if htf == "trending_up":
                conf *= 0.7

            if divergence == "bearish":
                conf = min(1.0, conf + 0.1)

            tag = " | ".join(reasons) if reasons else "없음"
            if conf >= 0.45:
                return TradeSignal(Signal.SELL, conf,
                                   "매도%s%s: %s" % (mkt_str, htf_str, tag), price)

        return TradeSignal(Signal.HOLD, 0, "관망%s%s: %s" % (mkt_str, htf_str, tag), price)
