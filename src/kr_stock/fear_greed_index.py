"""
한국 주식시장 공포/탐욕 지수 (Fear & Greed Index)

워렌 버핏의 원칙: "시장이 탐욕적일 때 공포에 떨고, 시장이 공포에 떨 때 탐욕을 가져라"

지수 구성 (0~100):
  0~20  : 극단적 공포 (Extreme Fear)  → 매수 기회
  20~40 : 공포 (Fear)
  40~60 : 중립 (Neutral)
  60~80 : 탐욕 (Greed)
  80~100: 극단적 탐욕 (Extreme Greed) → 매도 고려

세부 지표:
  1. RSI (25%) - 과매수/과매도
  2. 이동평균 괴리율 (20%) - 200일 이평선 대비 현재가
  3. 변동성 (15%) - 현재 변동성 vs 1년 평균
  4. 거래량 추세 (15%) - 상승일/하락일 거래량 비율
  5. 52주 고저 위치 (15%) - 52주 범위 내 위치
  6. 볼린저 밴드 %B (10%) - 밴드 내 위치
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class Sentiment(Enum):
    EXTREME_FEAR = "극단적 공포"
    FEAR = "공포"
    NEUTRAL = "중립"
    GREED = "탐욕"
    EXTREME_GREED = "극단적 탐욕"


@dataclass
class FearGreedResult:
    score: float
    sentiment: Sentiment
    rsi_score: float
    ma_deviation_score: float
    volatility_score: float
    volume_trend_score: float
    high_low_score: float
    bollinger_score: float
    current_price: float
    price_change_pct: float

    @property
    def action_signal(self) -> str:
        if self.score <= 20:
            return "강력 매수 신호 - 극단적 공포 구간"
        elif self.score <= 40:
            return "매수 관심 - 공포 구간"
        elif self.score <= 60:
            return "관망 - 중립 구간"
        elif self.score <= 80:
            return "매도 관심 - 탐욕 구간"
        else:
            return "강력 매도 신호 - 극단적 탐욕 구간"

    @property
    def emoji(self) -> str:
        if self.score <= 20:
            return "🟢🟢"
        elif self.score <= 40:
            return "🟢"
        elif self.score <= 60:
            return "⚪"
        elif self.score <= 80:
            return "🔴"
        else:
            return "🔴🔴"


def _classify_sentiment(score: float) -> Sentiment:
    if score <= 20:
        return Sentiment.EXTREME_FEAR
    elif score <= 40:
        return Sentiment.FEAR
    elif score <= 60:
        return Sentiment.NEUTRAL
    elif score <= 80:
        return Sentiment.GREED
    return Sentiment.EXTREME_GREED


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


class FearGreedCalculator:
    """개별 종목 또는 지수의 공포/탐욕 점수를 계산한다."""

    WEIGHTS = {
        "rsi": 0.25,
        "ma_deviation": 0.20,
        "volatility": 0.15,
        "volume_trend": 0.15,
        "high_low": 0.15,
        "bollinger": 0.10,
    }

    def __init__(
        self,
        rsi_period: int = 14,
        ma_long_period: int = 200,
        volatility_period: int = 20,
        bb_period: int = 20,
        bb_std: float = 2.0,
    ):
        self.rsi_period = rsi_period
        self.ma_long_period = ma_long_period
        self.volatility_period = volatility_period
        self.bb_period = bb_period
        self.bb_std = bb_std

    def calculate(self, df: pd.DataFrame) -> Optional[FearGreedResult]:
        if df is None or len(df) < self.ma_long_period:
            logger.warning("데이터 부족 (최소 %d일 필요, 현재 %d일)",
                           self.ma_long_period, 0 if df is None else len(df))
            return None

        try:
            rsi_score = self._rsi_score(df)
            ma_score = self._ma_deviation_score(df)
            vol_score = self._volatility_score(df)
            volume_score = self._volume_trend_score(df)
            hl_score = self._high_low_score(df)
            bb_score = self._bollinger_score(df)

            composite = (
                self.WEIGHTS["rsi"] * rsi_score
                + self.WEIGHTS["ma_deviation"] * ma_score
                + self.WEIGHTS["volatility"] * vol_score
                + self.WEIGHTS["volume_trend"] * volume_score
                + self.WEIGHTS["high_low"] * hl_score
                + self.WEIGHTS["bollinger"] * bb_score
            )
            composite = _clamp(composite)

            current_price = float(df["close"].iloc[-1])
            prev_price = float(df["close"].iloc[-2]) if len(df) >= 2 else current_price
            price_change_pct = ((current_price - prev_price) / prev_price) * 100 if prev_price else 0

            return FearGreedResult(
                score=round(composite, 1),
                sentiment=_classify_sentiment(composite),
                rsi_score=round(rsi_score, 1),
                ma_deviation_score=round(ma_score, 1),
                volatility_score=round(vol_score, 1),
                volume_trend_score=round(volume_score, 1),
                high_low_score=round(hl_score, 1),
                bollinger_score=round(bb_score, 1),
                current_price=current_price,
                price_change_pct=round(price_change_pct, 2),
            )
        except Exception as e:
            logger.error("공포/탐욕 지수 계산 실패: %s", e)
            return None

    def _rsi_score(self, df: pd.DataFrame) -> float:
        """RSI → 0~100 점수 (RSI 그 자체가 공포/탐욕의 좋은 척도)"""
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(alpha=1 / self.rsi_period, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, np.finfo(float).eps)
        rsi = 100 - (100 / (1 + rs))
        return _clamp(float(rsi.iloc[-1]))

    def _ma_deviation_score(self, df: pd.DataFrame) -> float:
        """200일 이동평균 대비 괴리율 → 0~100 점수

        괴리율 -20% 이하 → 0 (극단적 공포)
        괴리율 +20% 이상 → 100 (극단적 탐욕)
        """
        ma200 = df["close"].rolling(self.ma_long_period).mean()
        current = float(df["close"].iloc[-1])
        ma_val = float(ma200.iloc[-1])
        if ma_val == 0 or np.isnan(ma_val):
            return 50.0
        deviation_pct = ((current - ma_val) / ma_val) * 100
        score = 50 + (deviation_pct / 20) * 50
        return _clamp(score)

    def _volatility_score(self, df: pd.DataFrame) -> float:
        """현재 변동성 vs 1년 평균 변동성 → 0~100

        변동성이 높으면 → 높은 점수 (탐욕이 아닌 공포일 수 있으나,
        여기선 정규화하여 높은 변동성 = 공포로 처리 → 점수를 반전)
        """
        returns = df["close"].pct_change().dropna()
        current_vol = float(returns.iloc[-self.volatility_period:].std())
        year_vol = float(returns.iloc[-252:].std()) if len(returns) >= 252 else float(returns.std())

        if year_vol == 0:
            return 50.0
        ratio = current_vol / year_vol
        score = 100 - _clamp(ratio * 50, 0, 100)
        return _clamp(score)

    def _volume_trend_score(self, df: pd.DataFrame) -> float:
        """상승일/하락일 거래량 비율 → 0~100

        최근 20일간 상승일 거래량 합 vs 하락일 거래량 합
        상승일 거래량이 많으면 탐욕, 하락일이 많으면 공포
        """
        recent = df.iloc[-self.volatility_period:]
        price_change = recent["close"].diff()
        up_volume = recent.loc[price_change > 0, "volume"].sum()
        down_volume = recent.loc[price_change < 0, "volume"].sum()
        total = up_volume + down_volume
        if total == 0:
            return 50.0
        score = (up_volume / total) * 100
        return _clamp(score)

    def _high_low_score(self, df: pd.DataFrame) -> float:
        """52주(약 250거래일) 최고/최저 대비 현재 위치 → 0~100"""
        window = min(250, len(df))
        recent = df.iloc[-window:]
        high_52w = float(recent["high"].max())
        low_52w = float(recent["low"].min())
        current = float(df["close"].iloc[-1])

        rng = high_52w - low_52w
        if rng == 0:
            return 50.0
        score = ((current - low_52w) / rng) * 100
        return _clamp(score)

    def _bollinger_score(self, df: pd.DataFrame) -> float:
        """볼린저 밴드 %B → 0~100

        %B < 0 → 극단적 공포, %B > 1 → 극단적 탐욕
        """
        mid = df["close"].rolling(self.bb_period).mean()
        std = df["close"].rolling(self.bb_period).std()
        upper = mid + std * self.bb_std
        lower = mid - std * self.bb_std
        bw = float(upper.iloc[-1]) - float(lower.iloc[-1])
        if bw == 0:
            return 50.0
        pct_b = (float(df["close"].iloc[-1]) - float(lower.iloc[-1])) / bw
        score = _clamp(pct_b * 100)
        return score


class MarketFearGreedIndex:
    """시장 전체의 종합 공포/탐욕 지수를 계산한다."""

    def __init__(self):
        self.calculator = FearGreedCalculator()

    def calculate_composite(
        self, stock_results: dict[str, FearGreedResult]
    ) -> Optional[FearGreedResult]:
        if not stock_results:
            return None

        scores = [r.score for r in stock_results.values()]
        avg_score = float(np.mean(scores))

        avg_fields = {}
        for field_name in [
            "rsi_score", "ma_deviation_score", "volatility_score",
            "volume_trend_score", "high_low_score", "bollinger_score",
        ]:
            avg_fields[field_name] = round(
                float(np.mean([getattr(r, field_name) for r in stock_results.values()])), 1
            )

        return FearGreedResult(
            score=round(avg_score, 1),
            sentiment=_classify_sentiment(avg_score),
            current_price=0,
            price_change_pct=0,
            **avg_fields,
        )
