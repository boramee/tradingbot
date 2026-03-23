"""시장 심리 분석 (VKOSPI + 코스피 종합)

VKOSPI (한국 공포지수):
  15 이하: 극도의 안심 (탐욕)
  15~20: 안정
  20~25: 불안
  25~30: 공포
  30 이상: 극도의 공포 → 매수 기회

코스피 급락 + VKOSPI 급등 = "피의 금요일" → 우량주 매수 적기
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    vkospi: float = 0.0
    kospi_change: float = 0.0
    sentiment: str = "neutral"  # extreme_fear, fear, neutral, greed, extreme_greed
    score: int = 50  # 0=극공포, 100=극탐욕
    buy_boost: float = 0.0
    reason: str = ""


class MarketSentiment:
    """시장 심리 종합 분석"""

    CACHE_TTL = 300  # 5분 캐시

    def __init__(self, kis_client=None):
        self._kis = kis_client
        self._cache: Optional[SentimentResult] = None
        self._cache_time: float = 0

    def analyze(self) -> SentimentResult:
        """현재 시장 심리 분석"""
        now = time.time()
        if self._cache and now - self._cache_time < self.CACHE_TTL:
            return self._cache

        result = SentimentResult()

        # VKOSPI 조회
        if self._kis:
            try:
                vkospi_data = self._kis.get_index_price("V001")
                if vkospi_data:
                    result.vkospi = vkospi_data.get("price", 0)
            except Exception:
                pass

            # 코스피 등락률
            try:
                kospi_data = self._kis.get_index_price("0001")
                if kospi_data:
                    result.kospi_change = kospi_data.get("change_pct", 0)
            except Exception:
                pass

        # 심리 점수 계산
        score = 50  # 기본 중립

        # VKOSPI 반영
        if result.vkospi > 0:
            if result.vkospi >= 30:
                score -= 30  # 극공포
            elif result.vkospi >= 25:
                score -= 20
            elif result.vkospi >= 20:
                score -= 10
            elif result.vkospi <= 15:
                score += 15  # 안심 = 탐욕

        # 코스피 등락률 반영
        if result.kospi_change <= -3:
            score -= 25
        elif result.kospi_change <= -1.5:
            score -= 15
        elif result.kospi_change >= 2:
            score += 15
        elif result.kospi_change >= 1:
            score += 10

        score = max(0, min(100, score))
        result.score = score

        # 심리 분류
        if score <= 15:
            result.sentiment = "extreme_fear"
            result.buy_boost = 0.3
            result.reason = "극도의 공포 (VKOSPI:%.1f, 코스피:%+.1f%%)" % (result.vkospi, result.kospi_change)
        elif score <= 30:
            result.sentiment = "fear"
            result.buy_boost = 0.15
            result.reason = "공포 (VKOSPI:%.1f, 코스피:%+.1f%%)" % (result.vkospi, result.kospi_change)
        elif score >= 80:
            result.sentiment = "extreme_greed"
            result.buy_boost = -0.2
            result.reason = "극도의 탐욕 (VKOSPI:%.1f, 코스피:%+.1f%%)" % (result.vkospi, result.kospi_change)
        elif score >= 65:
            result.sentiment = "greed"
            result.buy_boost = -0.1
            result.reason = "탐욕 (VKOSPI:%.1f)" % result.vkospi
        else:
            result.sentiment = "neutral"
            result.reason = "중립 (VKOSPI:%.1f)" % result.vkospi

        self._cache = result
        self._cache_time = now
        return result
