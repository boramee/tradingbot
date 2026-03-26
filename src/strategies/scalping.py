"""분봉 기반 단타 전략 (Scalping Strategy)

일봉 MACD/Bollinger 대신 분봉 데이터 + 실시간 지표로 진입 판단.
스캐너가 종목을 찾으면, 이 전략이 "지금 사도 되는가"를 분봉으로 판단.

매수 조건 (모두 충족):
  1. 현재가 > VWAP (당일 평균가 위 = 강세)
  2. 최근 3분봉 양봉 연속 (상승 추세 확인)
  3. 체결강도 100% 이상 (매수세 > 매도세)
  4. 호가 매수/매도 비율 0.5~2.5 (건전한 호가창)
  5. 분봉 고점 대비 위치 확인 (꼭대기 매수 방지)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseStrategy, Signal, TradeSignal

logger = logging.getLogger(__name__)


@dataclass
class ScalpingContext:
    """분봉 분석에 필요한 실시간 데이터"""
    minute_df: pd.DataFrame          # 분봉 OHLCV
    volume_power: float = 0.0        # 체결강도 (100 = 중립)
    orderbook_ratio: float = 0.0     # 호가 매수/매도 비율
    scanner_score: float = 0.0       # 스캐너 점수


class ScalpingStrategy(BaseStrategy):
    """분봉 기반 단타 전략"""

    # 매수 최소 조건
    MIN_VOLUME_POWER = 100.0     # 체결강도 (매수세 >= 매도세)
    MIN_ORDERBOOK_RATIO = 0.5    # 호가 비율 최소
    MAX_ORDERBOOK_RATIO = 2.5    # 호가 비율 최대
    MIN_BULLISH_CANDLES = 2      # 최소 연속 양봉 수
    MAX_HIGH_POSITION = 0.85     # 고점 대비 위치 상한 (85% 이상이면 꼭대기)

    @property
    def name(self) -> str:
        return "Scalping"

    def analyze(self, df: pd.DataFrame, **kwargs) -> TradeSignal:
        """일봉 기반 분석 (호환용). 분봉 분석은 analyze_scalping 사용."""
        return TradeSignal(Signal.HOLD, 0, "스캘핑: 분봉 분석 필요", 0)

    def analyze_scalping(self, ctx: ScalpingContext) -> TradeSignal:
        """분봉 데이터 기반 매수 판단.

        Returns:
            TradeSignal: BUY(매수), HOLD(대기), SELL(매도)
        """
        mdf = ctx.minute_df
        if mdf is None or len(mdf) < 10:
            return TradeSignal(Signal.HOLD, 0, "분봉 데이터 부족", 0)

        price = float(mdf["close"].iloc[-1])
        reasons = []
        score = 0.0

        # ── 1. VWAP 계산 및 확인 ──
        vwap = self._calc_vwap(mdf)
        if vwap is None or vwap <= 0:
            return TradeSignal(Signal.HOLD, 0, "VWAP 계산 불가", price)

        vwap_pct = (price - vwap) / vwap * 100
        if price > vwap:
            score += 0.25
            reasons.append("VWAP위(%.1f%%)" % vwap_pct)
        else:
            reasons.append("VWAP아래(%.1f%%)" % vwap_pct)
            # VWAP 아래지만 접근 중이면 감점만
            if vwap_pct > -0.5:
                score += 0.05
                reasons.append("VWAP접근중")
            else:
                return TradeSignal(Signal.HOLD, 0,
                                   "VWAP 하회 (%.1f%%)" % vwap_pct, price)

        # ── 2. 분봉 양봉 연속 체크 (추세 확인) ──
        bullish_count = self._count_bullish_candles(mdf)
        if bullish_count >= 3:
            score += 0.25
            reasons.append("양봉%d연속" % bullish_count)
        elif bullish_count >= self.MIN_BULLISH_CANDLES:
            score += 0.15
            reasons.append("양봉%d연속" % bullish_count)
        else:
            # 양봉이 아니어도, 직전봉 대비 상승 중이면 약간 가산
            if len(mdf) >= 2 and float(mdf["close"].iloc[-1]) > float(mdf["close"].iloc[-2]):
                score += 0.05
                reasons.append("직전봉대비상승")
            else:
                reasons.append("양봉부족(%d)" % bullish_count)

        # ── 3. 체결강도 ──
        vp = ctx.volume_power
        if vp >= 150:
            score += 0.25
            reasons.append("체결강도강(%.0f%%)" % vp)
        elif vp >= self.MIN_VOLUME_POWER:
            score += 0.15
            reasons.append("체결강도(%.0f%%)" % vp)
        else:
            reasons.append("체결강도약(%.0f%%)" % vp)

        # ── 4. 호가창 건전성 ──
        ob = ctx.orderbook_ratio
        if self.MIN_ORDERBOOK_RATIO <= ob <= self.MAX_ORDERBOOK_RATIO:
            score += 0.1
            reasons.append("호가건전(%.1f)" % ob)
        elif ob > self.MAX_ORDERBOOK_RATIO:
            score -= 0.1
            reasons.append("호가위험(매수과다%.1f)" % ob)
        else:
            score -= 0.1
            reasons.append("호가위험(매도압도%.1f)" % ob)

        # ── 5. 고점 대비 위치 (꼭대기 매수 방지) ──
        position = self._get_high_position(mdf)
        if position is not None:
            if position > self.MAX_HIGH_POSITION:
                # 꼭대기 근처 — 강한 감점
                score -= 0.2
                reasons.append("꼭대기(%.0f%%위치)" % (position * 100))
            elif position < 0.5:
                # 중간 이하 — 눌림목 구간
                score += 0.1
                reasons.append("눌림목(%.0f%%위치)" % (position * 100))

        # ── 6. 분봉 거래량 급증 ──
        vol_surge = self._check_volume_surge(mdf)
        if vol_surge >= 2.0:
            score += 0.15
            reasons.append("분봉거래량%.1fx" % vol_surge)
        elif vol_surge >= 1.5:
            score += 0.05
            reasons.append("분봉거래량%.1fx" % vol_surge)

        # ── 7. 스캐너 보너스 ──
        if ctx.scanner_score >= 100:
            score += 0.1
            reasons.append("스캐너%.0f점" % ctx.scanner_score)
        elif ctx.scanner_score >= 80:
            score += 0.05

        # ── 최종 판단 ──
        tag = " | ".join(reasons)
        conf = max(0, min(1.0, score))

        if conf >= 0.5:
            return TradeSignal(Signal.BUY, conf, "분봉매수: %s" % tag, price)

        return TradeSignal(Signal.HOLD, conf, "분봉관망: %s" % tag, price)

    # ── 헬퍼 메서드 ──

    @staticmethod
    def _calc_vwap(mdf: pd.DataFrame) -> Optional[float]:
        """VWAP (Volume Weighted Average Price) 계산"""
        if "volume" not in mdf.columns or len(mdf) < 2:
            return None
        typical_price = (mdf["high"] + mdf["low"] + mdf["close"]) / 3
        cum_tp_vol = (typical_price * mdf["volume"]).cumsum()
        cum_vol = mdf["volume"].cumsum()
        vwap_series = cum_tp_vol / cum_vol.replace(0, np.nan)
        last = vwap_series.iloc[-1]
        return float(last) if pd.notna(last) else None

    @staticmethod
    def _count_bullish_candles(mdf: pd.DataFrame) -> int:
        """최근 연속 양봉 수"""
        count = 0
        for i in range(len(mdf) - 1, -1, -1):
            if float(mdf["close"].iloc[i]) > float(mdf["open"].iloc[i]):
                count += 1
            else:
                break
        return count

    @staticmethod
    def _get_high_position(mdf: pd.DataFrame) -> Optional[float]:
        """장중 고가 대비 현재가 위치 (0.0=저가, 1.0=고가)"""
        if len(mdf) < 5:
            return None
        intraday_high = float(mdf["high"].max())
        intraday_low = float(mdf["low"].min())
        if intraday_high == intraday_low:
            return 0.5
        current = float(mdf["close"].iloc[-1])
        return (current - intraday_low) / (intraday_high - intraday_low)

    @staticmethod
    def _check_volume_surge(mdf: pd.DataFrame) -> float:
        """최근 봉 거래량 / 평균 거래량"""
        if len(mdf) < 5:
            return 1.0
        avg_vol = mdf["volume"].iloc[:-1].mean()
        if avg_vol <= 0:
            return 1.0
        return float(mdf["volume"].iloc[-1]) / avg_vol
