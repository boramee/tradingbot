"""고급 복합 전략 v2

개선점:
  1. ADX 횡보장 필터 → 시장 상태 분류 (trending/ranging/volatile)
  2. 거래량 동반 + 정배열 + DI 방향
  3. RSI 다이버전스 감지 → 강력 신호 부스트
  4. 피봇포인트 지지/저항 → 저항 근처 매수 차단, 지지 근처 매도 차단
  5. 멀티타임프레임 (상위 차트 추세 확인)
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
    VOLUME_MIN_RATIO = 0.8
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

    @property
    def name(self) -> str:
        return "Combined_v2"

    def set_higher_timeframe(self, ticker: str, htf_interval: str = "minute60"):
        """상위 타임프레임 추세 캐시 (엔진에서 주기적 호출)"""
        try:
            from src.indicators.technical import TechnicalIndicators
            df = pyupbit.get_ohlcv(ticker, interval=htf_interval, count=100)
            if df is not None and not df.empty:
                df.columns = ["open", "high", "low", "close", "volume", "value"]
                ti = TechnicalIndicators()
                df = ti.add_all(df)
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

        if market == "ranging":
            return TradeSignal(Signal.HOLD, 0,
                               "[횡보장] 매매 중지 (ADX:%.0f)" % (adx or 0), price)

        if market == "volatile":
            return TradeSignal(Signal.HOLD, 0,
                               "[고변동] 매매 중지 (BB 밴드폭 과대)", price)

        # ── 거래량 필터 ──
        vol_ratio = self._last(df, "vol_ratio")
        if vol_ratio is not None and vol_ratio < self.VOLUME_MIN_RATIO:
            return TradeSignal(Signal.HOLD, 0,
                               "거래량 부족 (%.1fx)" % vol_ratio, price)

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

        # ── RSI 다이버전스 (강력 신호) ──
        divergence = self._adv.detect_rsi_divergence(df)
        if divergence == "bullish":
            buy_score += self.DIVERGENCE_BOOST
            reasons.append("RSI상승다이버전스")
        elif divergence == "bearish":
            sell_score += self.DIVERGENCE_BOOST
            reasons.append("RSI하락다이버전스")

        # ── 이동평균선 정배열/역배열 ──
        ma_s = self._last(df, "ma_short")
        ma_l = self._last(df, "ma_long")
        ma_aligned_up = ma_s is not None and ma_l is not None and ma_s > ma_l
        ma_aligned_down = ma_s is not None and ma_l is not None and ma_s < ma_l

        if ma_aligned_up:
            buy_score += 0.1
            reasons.append("정배열")
        elif ma_aligned_down:
            sell_score += 0.1
            reasons.append("역배열")

        # ── DI 방향 ──
        plus_di = self._last(df, "plus_di")
        minus_di = self._last(df, "minus_di")
        di_bull = plus_di is not None and minus_di is not None and plus_di > minus_di

        # ── 거래량 급등 보정 ──
        if vol_ratio is not None and vol_ratio > 1.5:
            boost = min(0.2, (vol_ratio - 1.5) * 0.1)
            buy_score *= (1 + boost)
            sell_score *= (1 + boost)
            reasons.append("거래량%.1fx" % vol_ratio)

        # ── 피봇포인트 지지/저항 체크 ──
        pivots = self._adv.pivot_points(df)
        sr_level = self._adv.near_support_resistance(price, pivots)

        # ── 멀티타임프레임 필터 ──
        htf = self._higher_tf_trend

        tag = " | ".join(reasons) if reasons else "없음"
        mkt_str = " [%s]" % market
        htf_str = " HTF:%s" % htf if htf else ""

        # ── 최종 매수 판단 ──
        if buy_score > sell_score and buy_score >= 0.3:
            if not ma_aligned_up:
                return TradeSignal(Signal.HOLD, buy_score * 0.3,
                                   "매수신호 but 역배열%s: %s" % (mkt_str, tag), price)
            if not di_bull:
                return TradeSignal(Signal.HOLD, buy_score * 0.3,
                                   "매수신호 but -DI우세%s: %s" % (mkt_str, tag), price)

            if sr_level and "resistance" in sr_level:
                return TradeSignal(Signal.HOLD, buy_score * 0.4,
                                   "매수신호 but 저항선 근처(%s)%s: %s" % (sr_level, mkt_str, tag), price)

            if htf == "trending_down":
                return TradeSignal(Signal.HOLD, buy_score * 0.3,
                                   "매수신호 but 상위TF 하락%s: %s" % (mkt_str, tag), price)

            conf = min(1.0, buy_score)
            if divergence == "bullish":
                conf = min(1.0, conf + 0.1)
            if htf == "trending_up":
                conf = min(1.0, conf + 0.1)

            return TradeSignal(Signal.BUY, conf,
                               "매수%s%s: %s" % (mkt_str, htf_str, tag), price)

        # ── 최종 매도 판단 ──
        if sell_score > buy_score and sell_score >= 0.3:
            if sr_level and "support" in sr_level:
                return TradeSignal(Signal.HOLD, sell_score * 0.4,
                                   "매도신호 but 지지선 근처(%s)%s: %s" % (sr_level, mkt_str, tag), price)

            if htf == "trending_up" and not ma_aligned_down:
                return TradeSignal(Signal.HOLD, sell_score * 0.4,
                                   "매도신호 but 상위TF 상승%s: %s" % (mkt_str, tag), price)

            conf = min(1.0, sell_score)
            if divergence == "bearish":
                conf = min(1.0, conf + 0.1)

            return TradeSignal(Signal.SELL, conf,
                               "매도%s%s: %s" % (mkt_str, htf_str, tag), price)

        return TradeSignal(Signal.HOLD, 0, "관망%s%s: %s" % (mkt_str, htf_str, tag), price)
