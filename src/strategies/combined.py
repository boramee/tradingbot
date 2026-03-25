"""고급 복합 전략 v4

v3 → v4 변경점:
  1. 다중시간대 필터 강화: HTF MACD 방향 + RSI 과매수/과매도 추가
     - HTF MACD 상승 + LTF 매수 → 보너스
     - HTF RSI > 75 → 매수 억제 (고점 과열)
     - HTF RSI < 30 → 매수 보너스 (반등 가능)
  2. 기존 v3 완화 정책 유지 (보너스/감점 방식)

연구: 4시간봉 MACD 전략이 1시간 이하 전략보다 2배 이상 높은 수익률.
     상위 시간대 추세 필터가 허위신호를 가장 효과적으로 제거.
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
        self._htf_macd_rising: Optional[bool] = None   # v4: HTF MACD 히스토그램 상승 여부
        self._htf_rsi: Optional[float] = None           # v4: HTF RSI 값
        self._htf_ticker: str = ""
        self._htf_indicators: Optional["TechnicalIndicators"] = None

    @property
    def name(self) -> str:
        return "Combined_v4"

    def set_higher_timeframe(self, ticker: str, htf_interval: str = "minute60"):
        """v4: 상위 타임프레임 추세 + MACD 방향 + RSI 캐시"""
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

                # v4: HTF MACD 히스토그램 방향 (상승 중인지)
                if "macd_hist" in df.columns and len(df) >= 3:
                    h1 = df["macd_hist"].iloc[-1]
                    h2 = df["macd_hist"].iloc[-2]
                    if pd.notna(h1) and pd.notna(h2):
                        self._htf_macd_rising = float(h1) > float(h2)
                    else:
                        self._htf_macd_rising = None
                # v4: HTF RSI (과매수/과매도 감지)
                if "rsi" in df.columns:
                    rsi_val = df["rsi"].iloc[-1]
                    self._htf_rsi = float(rsi_val) if pd.notna(rsi_val) else None

                logger.debug("상위TF: %s, MACD↑:%s, RSI:%.0f (%s)",
                             self._higher_tf_trend,
                             self._htf_macd_rising,
                             self._htf_rsi or 0,
                             htf_interval)
        except Exception as e:
            logger.debug("상위TF 조회 실패: %s", e)

    def analyze(self, df: pd.DataFrame) -> TradeSignal:
        price = float(df["close"].iloc[-1])

        # ── 시장 상태 분류 ──
        market = self._adv.classify_market(df)
        adx = self._last(df, "adx")

        # 고변동장: RSI 과매수(75+)면 차단, 아니면 감점 후 진행
        is_volatile = (market == "volatile")
        if is_volatile:
            rsi = self._last(df, "rsi")
            if rsi is not None and rsi > 75:
                return TradeSignal(Signal.HOLD, 0,
                                   "[고변동] RSI 과매수 (%.0f > 75)" % rsi, price)

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
        htf_macd_rising = self._htf_macd_rising
        htf_rsi = self._htf_rsi

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
            if is_volatile:
                conf *= 0.7  # 고변동장: 차단 대신 감점
                reasons.append("고변동감점")

            # 보너스 요소
            if divergence == "bullish":
                conf = min(1.0, conf + 0.1)
            if htf == "trending_up":
                conf = min(1.0, conf + 0.1)

            # v4: HTF MACD 상승 + LTF 매수 → 강한 확인
            if htf_macd_rising is True:
                conf = min(1.0, conf + 0.1)
                reasons.append("HTF_MACD↑")
            elif htf_macd_rising is False:
                conf *= 0.8
                reasons.append("HTF_MACD↓")

            # v4: HTF RSI 과매수 → 매수 억제 (고점 과열)
            if htf_rsi is not None:
                if htf_rsi > 75:
                    conf *= 0.6
                    reasons.append("HTF_RSI과열%.0f" % htf_rsi)
                elif htf_rsi < 30:
                    conf = min(1.0, conf + 0.1)
                    reasons.append("HTF_RSI과매도%.0f" % htf_rsi)

            tag = " | ".join(reasons) if reasons else "없음"
            if conf >= 0.45:
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

            # v4: HTF MACD 하락 + LTF 매도 → 매도 확인 강화
            if htf_macd_rising is False:
                conf = min(1.0, conf + 0.1)
                reasons.append("HTF_MACD↓확인")
            elif htf_macd_rising is True:
                conf *= 0.7
                reasons.append("HTF_MACD↑→매도억제")

            # v4: HTF RSI 과매도 → 매도 억제 (바닥 가능)
            if htf_rsi is not None:
                if htf_rsi < 30:
                    conf *= 0.6
                    reasons.append("HTF_RSI과매도%.0f" % htf_rsi)
                elif htf_rsi > 75:
                    conf = min(1.0, conf + 0.1)
                    reasons.append("HTF_RSI과열%.0f" % htf_rsi)

            tag = " | ".join(reasons) if reasons else "없음"
            if conf >= 0.45:
                return TradeSignal(Signal.SELL, conf,
                                   "매도%s%s: %s" % (mkt_str, htf_str, tag), price)

        return TradeSignal(Signal.HOLD, 0, "관망%s%s: %s" % (mkt_str, htf_str, tag), price)
